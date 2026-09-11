"""Run randomized profile blocks with isolated container lifecycles."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import signal
from pathlib import Path
import threading
import time
import uuid

from . import __version__
from .config import plan
from .docker import Docker, DockerError, classify, container_duration
from .storage import write_json


def utc():
    return datetime.now(timezone.utc).isoformat()


def trial(backend, run_id, directory, specification, task, profile, image, interval, stop):
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
        result["container_id"] = backend.create(name, run_id, task, profile, image, specification["seed"])
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
        result["status"] = "runtime_error" if "container_id" in result else "setup_error"
        result["error"] = str(error)
    except Exception as error:
        result["status"] = "runner_error"
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        sampler_stop.set()
        if sampler:
            sampler.join()
        if attempted:
            try:
                result["logs"] = backend.logs(name)
            except DockerError as error:
                result["evidence_errors"].append(str(error))
            try:
                backend.remove(name)
            except DockerError as error:
                result["cleanup_error"] = str(error)
        result["finished_at"] = utc()
        result["lifecycle_s"] = time.monotonic() - started
        write_json(directory / "trials" / f"{specification['id']}.json", result)
    return result


def execute(experiment, output, backend=None, stop=None):
    backend = backend or Docker()
    stop = stop or threading.Event()
    environment = backend.doctor()
    images = {task.image: backend.image(task.image) for task in experiment.tasks}
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
                "plan": batches, "measurement_kind": "trusted_workload_exit_contract",
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
                             {**specification, "repeat": batch["repeat"], "batch": batch["batch"]},
                             task, profile, images[task.image], experiment.sample_interval_s, stop)
            with ThreadPoolExecutor(max_workers=profile.concurrency) as pool:
                results.extend(pool.map(run, batch["trials"]))
            # Do not continue loading a host after failed cleanup.
            if any(result["cleanup_error"] for result in results):
                manifest["status"] = "cleanup_failed"
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
