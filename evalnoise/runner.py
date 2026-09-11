"""Run randomized profile blocks with isolated container lifecycles."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import signal
import sys
from pathlib import Path
import threading
import time
import uuid

from . import __version__
from .config import plan, Profile, Task
from .docker import Docker, DockerError, classify, container_duration
from .storage import write_json
from .verification import contract_hash, envelope


def utc():
    return datetime.now(timezone.utc).isoformat()


def trial(backend, run_id, directory, specification, task, profile, image, interval, stop, artifact=None):
    name = f"evalnoise-{run_id}-{specification['id']}"
    result = {"schema_version": 1, **specification, "profile": profile.id,
              "container_name": name, "started_at": utc(), "status": "unknown",
              "telemetry": [], "telemetry_errors": [], "evidence_errors": [],
              "cleanup_error": None, "timed_out": False, "cancelled": False}
    started = time.monotonic()
    sampler_stop = threading.Event()
    sampler = None
    attempted = False

    def sample():
        while not sampler_stop.is_set():
            before = time.monotonic()
            try:
                data = backend.stats(name)
                result["telemetry"].append({"elapsed_s": before - started,
                                            "collection_s": time.monotonic() - before, "raw": data})
            except (DockerError, ValueError, TypeError, KeyError) as error:
                if not sampler_stop.is_set():
                    result["telemetry_errors"].append(str(error))
            sampler_stop.wait(interval)

    try:
        if stop.is_set():
            result["status"] = "cancelled"
            result["cancelled"] = True
            return result
        attempted = True
        extra = {"artifact": artifact} if artifact is not None else {}
        result["container_id"] = backend.create(name, run_id, task, profile, image, specification["seed"], **extra)
        result["created_after_s"] = time.monotonic() - started
        # Deadline includes the start request, but excludes create/image preparation.
        deadline = time.monotonic() + profile.timeout_s
        backend.call(["start", name], timeout=min(20, profile.timeout_s))
        if interval:
            sampler = threading.Thread(target=sample, daemon=True)
            sampler.start()
        while True:
            observed = backend.inspect(name)
            result["inspection"] = observed
            if not observed["state"].get("Running", False):
                break
            result["cancelled"] = stop.is_set()
            result["timed_out"] = time.monotonic() >= deadline
            if result["cancelled"] or result["timed_out"]:
                try:
                    backend.call(["kill", name], timeout=10)
                except DockerError as error:
                    result["evidence_errors"].append(str(error))
                result["inspection"] = backend.inspect(name)
                break
            stop.wait(min(.2, max(0, deadline - time.monotonic())))
        state = result["inspection"]["state"]
        result["status"] = classify(state, result["timed_out"], result["cancelled"], task.expected_exit)
        result["container_duration_s"] = container_duration(state)
    except DockerError as error:
        result["status"] = ("cancelled" if result["cancelled"] else "timeout" if result["timed_out"] else
                            "runtime_error" if "container_id" in result else "setup_error")
        result["error"] = str(error)
    except Exception as error:
        result["status"] = "runner_error"
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        active_error = sys.exc_info()[1]
        final_error = None
        sampler_stop.set()
        if sampler:
            sampler.join()
        if attempted:
            try:
                result["logs"] = backend.logs(name)
            except Exception as error:
                result["evidence_errors"].append(str(error))
                if not isinstance(error, DockerError):
                    final_error = error
            try:
                backend.remove(name)
            except Exception as error:
                result["cleanup_error"] = str(error)
                if not isinstance(error, DockerError):
                    final_error = final_error or error
        if final_error:
            result["status"] = "runner_error"
            result.setdefault("error", f"{type(final_error).__name__}: {final_error}")
        result["finished_at"] = utc()
        result["lifecycle_s"] = time.monotonic() - started
        result["execution_status"] = result["status"]
        if task.verifier:
            if result["status"] == "passed":
                result["status"] = "pending_verification"
        write_json(directory / "trials" / f"{specification['id']}.json", result)
        if final_error and active_error is None:
            raise final_error
    return result


def verify(backend, run_id, directory, result, task, image, stop):
    spec = task.verifier
    if not spec or result["status"] != "pending_verification":
        return
    result["verification"] = {"version": spec.version, "protocol": "json-env-v1"}
    try:
        if stop.is_set():
            result["status"] = "cancelled"
            result["cancelled"] = True
            return
        if result["cleanup_error"]:
            result["status"] = "verifier_error"
            result["verification"]["error"] = "Workload cleanup failed; verifier was not started"
            return
        try:
            value, artifact = envelope(result.get("logs", {}), "evalnoise_artifact")
        except ValueError as error:
            result["status"] = "artifact_error"
            result["verification"]["error"] = str(error)
            return
        result["artifact"] = value
        verifier_task = Task(task.id, spec.image, spec.command)
        profile = Profile("verifier", spec.cpus, spec.memory_mb, spec.timeout_s)
        checked = trial(backend, run_id, directory / "verification",
                        {"id": result["id"] + "-verify", "task": task.id, "repeat": result["repeat"],
                         "seed": result["seed"], "contract_sha256": result["contract_sha256"]},
                        verifier_task, profile, image, 0, stop, artifact=artifact)
        result["verification"]["trial"] = checked
        result["cleanup_error"] = checked["cleanup_error"]
        if checked["status"] == "cancelled":
            result["status"] = "cancelled"
            result["cancelled"] = True
        elif checked["status"] != "passed" or checked["cleanup_error"]:
            result["status"] = "verifier_error"
        else:
            try:
                verdict, _ = envelope(checked.get("logs", {}), "evalnoise_verdict")
                result["verification"]["verdict"] = verdict
                result["status"] = "passed" if verdict["passed"] else "verification_failed"
            except ValueError as error:
                result["status"] = "verifier_error"
                result["verification"]["error"] = str(error)
    except Exception as error:
        result["status"] = "verifier_error"
        result["verification"]["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        result["verification_finished_at"] = utc()
        write_json(directory / "trials" / f"{result['id']}.json", result)


def execute(experiment, output, backend=None, stop=None):
    backend = backend or Docker()
    stop = stop or threading.Event()
    environment = backend.doctor()
    images = {task.image: backend.image(task.image) for task in experiment.tasks}
    for task in experiment.tasks:
        if task.verifier and task.verifier.image not in images:
            images[task.verifier.image] = backend.image(task.verifier.image)
    contracts = {task.id: contract_hash(task, images) for task in experiment.tasks}
    engine = environment["engine"]
    if any(image["architecture"] not in (engine.get("Architecture"),
            {"aarch64": "arm64", "x86_64": "amd64"}.get(engine.get("Architecture"))) for image in images.values()):
        raise DockerError("Image and engine architectures differ; emulation experiments are not supported")
    run_id = uuid.uuid4().hex[:12]
    directory = Path(output) / f"{experiment.name}-{run_id}"
    directory.mkdir(parents=True, exist_ok=False)
    batches = plan(experiment)
    manifest = {"schema_version": 1, "evalnoise_version": __version__, "run_id": run_id,
                "started_at": utc(), "status": "running", "config": experiment.data(),
                "config_sha256": experiment.digest(), "environment": environment, "images": images,
                 "plan": batches, "task_contracts": contracts,
                 "measurement_kind": "independent_verification" if all(t.verifier for t in experiment.tasks) else
                     "mixed_contracts" if any(t.verifier for t in experiment.tasks) else "trusted_workload_exit_contract",
                 "verification_schedule": "Sequential after each workload batch, before the next batch; no workload/verifier overlap",
                "cache_policy": "pre-existing image cache; fresh containers; host page cache uncontrolled",
                "warnings": ["No CPU or memory reservations. Other host workloads are uncontrolled.",
                             "Scripted workloads do not measure model capability.",
                             "No automatic retries; incomplete experiments must not be treated as complete."]}
    write_json(directory / "manifest.json", manifest)
    results = []
    profiles = {profile.id: profile for profile in experiment.profiles}
    tasks = {task.id: task for task in experiment.tasks}
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, lambda *_: stop.set())
    try:
        for batch in batches:
            if stop.is_set():
                break
            profile = profiles[batch["profile"]]
            def run(specification):
                task = tasks[specification["task"]]
                return trial(backend, run_id, directory,
                              {**specification, "repeat": batch["repeat"], "batch": batch["batch"],
                               "contract_sha256": contracts[task.id]},
                             task, profile, images[task.image], experiment.sample_interval_s, stop)
            with ThreadPoolExecutor(max_workers=profile.concurrency) as pool:
                batch_results = list(pool.map(run, batch["trials"]))
            results.extend(batch_results)
            # Do not continue loading a host after failed cleanup.
            if any(result["cleanup_error"] for result in results):
                manifest["status"] = "cleanup_failed"
                break
            for result in batch_results:
                task = tasks[result["task"]]
                if task.verifier:
                    verify(backend, run_id, directory, result, task, images[task.verifier.image], stop)
                if result["cleanup_error"]:
                    manifest["status"] = "cleanup_failed"
                    break
            if manifest["status"] == "cleanup_failed":
                break
        else:
            manifest["status"] = "completed"
        if stop.is_set():
            manifest["status"] = "cancelled"
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if manifest["status"] == "running":
            manifest["status"] = "interrupted"
        manifest["finished_at"] = utc()
        manifest["recorded_trials"] = len(list((directory / "trials").glob("*.json")))
        manifest["planned_trials"] = sum(len(batch["trials"]) for batch in batches)
        write_json(directory / "manifest.json", manifest)
    return directory
