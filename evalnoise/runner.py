"""Run randomized profile blocks with isolated container lifecycles."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import signal
import sys
from pathlib import Path
import threading
import time
import uuid

from . import __version__
from .budget import Ledger, admit_plan
from .config import cpuset, plan, Profile, Task
from .coordination import EngineLock, refuse_orphans, SCOPE
from .docker import audit, Docker, DockerError, EnforcementError, classify, container_duration
from .provider import load_cassette
from .storage import write_json
from .verification import contract_hash, envelope, normalize


def utc():
    return datetime.now(timezone.utc).isoformat()


# The CLI snapshot path is the fallback used when the engine stream is unavailable. Its
# bounds mirror the streaming reader so neither path can grow a trial artifact without
# limit, and its join bound exceeds the 5 s `docker stats --no-stream` subprocess timeout
# in `Docker.stats`, so a healthy sampler always stops well inside it.
MAX_SNAPSHOT_SAMPLES = 3600
MAX_SNAPSHOT_ERRORS = 200
SAMPLER_JOIN_TIMEOUT_S = 8


class CliSampler:
    """Bounded, detachable `docker stats --no-stream` sampler.

    Buffers are private and lock-guarded; `close()` waits a bounded time, then detaches, so
    a still-live thread can never mutate evidence that was already reported or persisted.
    A leak is metadata, not a workload outcome.
    """

    def __init__(self, backend, name, interval, started):
        self.backend, self.name = backend, name
        self.interval = interval
        self.started = started
        self.received = 0
        self.truncated = False
        self.thread_leaked = False
        self._samples, self._errors = [], []
        self._guard = threading.Lock()
        self._detached = False
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name=f"evalnoise-sampler-{self.name}",
                                        daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            before = time.monotonic()
            try:
                data = self.backend.stats(self.name)
                self._record({"elapsed_s": before - self.started,
                              "collection_s": time.monotonic() - before, "raw": data})
            except Exception as error:
                # A sampling fault is telemetry, never the workload's verdict. A stopped or
                # detached sampler stays quiet: removal makes these calls fail by design.
                if not self._stop.is_set():
                    self._fault(f"{type(error).__name__}: {error}")
            self._stop.wait(self.interval)

    def _record(self, sample):
        with self._guard:
            if self._detached:
                return
            self.received += 1
            if len(self._samples) >= MAX_SNAPSHOT_SAMPLES:
                self.truncated = True
                return
            self._samples.append(sample)

    def _fault(self, message):
        with self._guard:
            if self._detached or len(self._errors) >= MAX_SNAPSHOT_ERRORS:
                return
            self._errors.append(message)
            if len(self._errors) == MAX_SNAPSHOT_ERRORS:
                self._errors.append("snapshot sampler error log reached its bound; "
                                    "later sampling faults were counted only by omission")

    def close(self, timeout=None):
        """Bounded. Always called before container removal and never blocks cleanup."""
        timeout = SAMPLER_JOIN_TIMEOUT_S if timeout is None else timeout
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        alive = self._thread is not None and self._thread.is_alive()
        with self._guard:
            # Detaching inside the same critical section that copies the buffers means no
            # in-flight append can land after the copy is taken.
            self._detached = True
            samples, errors = list(self._samples), list(self._errors)
            received, truncated = self.received, self.truncated
        if alive:
            self.thread_leaked = True
            errors = errors + [
                f"snapshot sampler thread did not stop within {timeout}s; it was detached and "
                "can no longer record into this trial. Counts below exclude anything it "
                "observed afterwards."]
        return {"samples": samples, "errors": errors, "samples_received": received,
                "samples_retained": len(samples), "truncated": truncated,
                "thread_leaked": alive, "closed_before_cleanup": not alive,
                "retention_policy": (
                    f"Host-driven `docker stats --no-stream` snapshot roughly every {self.interval}s "
                    "plus collection time; the interval is a floor, not a guaranteed cadence, and "
                    "nothing is interpolated"),
                "derived": None,
                "derived_note": ("The CLI snapshot path reports formatted display strings, not the "
                                 "raw integer counters the engine stream exposes, so no deltas are "
                                 "derived from it. Null means not measured, never zero.")}


def trial(backend, run_id, directory, specification, task, profile, image, interval, stop,
          artifact=None, artifact_env="EVALNOISE_ARTIFACT_B64"):
    name = f"evalnoise-{run_id}-{specification['id']}"
    result = {"schema_version": 1, **specification, "profile": profile.id,
              "container_name": name, "started_at": utc(), "status": "unknown",
              "telemetry": [], "telemetry_errors": [], "evidence_errors": [],
              "telemetry_source": "disabled", "telemetry_meta": None, "resource_audit": None,
              "cleanup_error": None, "timed_out": False, "cancelled": False}
    started = time.monotonic()
    sampler = None
    stream = None
    attempted = False

    try:
        if stop.is_set():
            result["status"] = "cancelled"
            result["cancelled"] = True
            return result
        attempted = True
        extra = {"artifact": artifact, "artifact_env": artifact_env} if artifact is not None else {}
        result["container_id"] = backend.create(name, run_id, task, profile, image, specification["seed"], **extra)
        result["created_after_s"] = time.monotonic() - started
        created = backend.inspect(name)
        result["inspection"] = created
        result["resource_audit"] = audit(profile, created["resources"])
        if not result["resource_audit"]["enforced_as_requested"]:
            raise EnforcementError(
                "Engine did not record the requested resource limits: "
                + json.dumps(result["resource_audit"]["mismatches"]))
        # Deadline includes the start request, but excludes create/image preparation.
        deadline = time.monotonic() + profile.timeout_s
        backend.call(["start", name], timeout=min(20, profile.timeout_s))
        if interval:
            stream = backend.stream(created.get("container_id") or result["container_id"], interval, started)
            if stream is None:
                result["telemetry_source"] = "cli_snapshot"
                sampler = CliSampler(backend, name, interval, started).start()
            else:
                result["telemetry_source"] = "engine_stream"
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
                            "enforcement_error" if isinstance(error, EnforcementError) else
                            "runtime_error" if "container_id" in result else "setup_error")
        result["error"] = str(error)
    except Exception as error:
        result["status"] = "runner_error"
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        active_error = sys.exc_info()[1]
        final_error = None
        # Both collectors are stopped before removal, or a live reader blocks cleanup. Each
        # close is bounded and a failure in either must never skip cleanup or persistence of
        # the workload evidence.
        for collector in (sampler, stream):
            if collector is None:
                continue
            try:
                meta = collector.close()
                result["telemetry"] = meta.pop("samples")
                result["telemetry_errors"].extend(meta.pop("errors"))
                result["telemetry_meta"] = meta
            except Exception as error:
                result["telemetry_errors"].append(f"{type(error).__name__}: {error}")
                result["telemetry_meta"] = {"close_failed": True}
                final_error = error
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


def agent_trial(backend, run_id, directory, specification, task, profile, image, stop,
                provider, ledger):
    """Aggregates bounded agent steps. There is no parent container, so no duration is claimed."""
    from .agent import run_agent
    started = time.monotonic()
    result = {"schema_version": 1, **specification, "profile": profile.id,
              "container_name": None, "container_duration_s": None, "started_at": utc(),
              "status": "agent_incomplete", "execution_status": "agent_incomplete",
              "measurement_kind": "agent_step_aggregate",
              "telemetry": [], "telemetry_errors": [], "evidence_errors": [],
              "telemetry_source": "disabled", "telemetry_meta": None, "resource_audit": None,
              "cleanup_error": None, "timed_out": False, "cancelled": False}
    try:
        run_agent(backend, run_id, directory, result, task, profile, image, stop, provider, ledger)
    except Exception as error:
        result["status"] = "runner_error"
        result["execution_status"] = "runner_error"
        result["error"] = f"{type(error).__name__}: {error}"
        write_json(directory / "trials" / f"{specification['id']}.json", result)
        raise
    finally:
        result["finished_at"] = utc()
        result["lifecycle_s"] = time.monotonic() - started
        write_json(directory / "trials" / f"{specification['id']}.json", result)
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
            if task.agent:
                # An agent's answer is produced on the host, so it is validated by the same
                # strict rules instead of being written back into fabricated container logs.
                value, artifact = normalize((result.get("agent") or {}).get("final_artifact"),
                                            "evalnoise_artifact")
            else:
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


def check_cpuset(profile, engine):
    indices = cpuset(profile.cpuset_cpus)
    count = engine.get("NCPU")
    if not isinstance(count, int) or count <= 0:
        raise DockerError(
            f"Profile {profile.id!r} requests a CPU affinity mask but this engine did not report "
            "a usable CPU count; the available CPU indices are unknown and cannot be validated")
    if max(indices) >= count:
        raise DockerError(
            f"Profile {profile.id!r} requests CPU index {max(indices)} but the engine reports "
            f"{count} CPUs. Engine NCPU is a count, not proof that indices 0..{count - 1} are all "
            "schedulable; a constrained host may expose a narrower mask, which only the "
            "enforcement probe can confirm.")


def engine_stability(backend, expected):
    try:
        observed = backend.identity()["id"]
    except Exception as error:
        return {"stable": False, "expected": expected.get("id"), "observed": None,
                "error": f"{type(error).__name__}: {error}",
                "note": "Engine was unreachable at the end of the run"}
    return {"stable": observed == expected.get("id"), "expected": expected.get("id"),
            "observed": observed, "error": None,
            "note": "Daemon ID compared before and after the run"}


def execute(experiment, output, backend=None, stop=None, config_dir=None):
    backend = backend or Docker()
    stop = stop or threading.Event()
    provider, ledger, admission = None, None, None
    if any(task.agent for task in experiment.tasks):
        provider = load_cassette(experiment.provider["cassette"], config_dir or Path.cwd())
        prices = experiment.budget["prices"]
        budget = {k: v for k, v in experiment.budget.items() if k != "prices"}
        # Plan admission runs before the engine is touched or any directory exists.
        admission = admit_plan(experiment, plan(experiment), budget, prices)
        ledger = Ledger(budget, prices)
    environment = backend.doctor()
    images = {task.image: backend.image(task.image) for task in experiment.tasks}
    for task in experiment.tasks:
        if task.verifier and task.verifier.image not in images:
            images[task.verifier.image] = backend.image(task.verifier.image)
    snapshot = provider.snapshot if provider else None
    contracts = {task.id: contract_hash(task, images, snapshot) for task in experiment.tasks}
    engine = environment["engine"]
    if any(image["architecture"] not in (engine.get("Architecture"),
            {"aarch64": "arm64", "x86_64": "amd64"}.get(engine.get("Architecture"))) for image in images.values()):
        raise DockerError("Image and engine architectures differ; emulation experiments are not supported")
    engine_identity = environment.get("engine_identity") or {}
    for profile in experiment.profiles:
        if profile.cpuset_cpus:
            check_cpuset(profile, engine)
    run_id = uuid.uuid4().hex[:12]
    lock = EngineLock(engine_identity.get("id"))
    lock.acquire(run_id, output)
    try:
        return _execute_locked(experiment, output, backend, stop, environment, images, contracts,
                               engine_identity, run_id, lock, provider, ledger, admission)
    finally:
        lock.release()


def _execute_locked(experiment, output, backend, stop, environment, images, contracts,
                    engine_identity, run_id, lock, provider=None, ledger=None, admission=None):
    # Orphans are rechecked while the lock is held; checking before acquiring it is a race.
    refuse_orphans(backend, lock.engine_id, run_id)
    backend.owner_token = lock.token
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
                 "coordination": {**lock.record(), "orphan_check": "refused_if_present"},
                 "provider_snapshot": provider.snapshot if provider else None,
                 "budget_admission": admission,
                "warnings": ["No CPU or memory reservations. Other host workloads are uncontrolled.",
                             SCOPE,
                             "Scripted workloads do not measure model capability.",
                             "No automatic retries; incomplete experiments must not be treated as complete."]}
    results = []
    profiles = {profile.id: profile for profile in experiment.profiles}
    tasks = {task.id: task for task in experiment.tasks}
    previous = {}
    try:
        write_json(directory / "manifest.json", manifest)
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, lambda *_: stop.set())
        for batch in batches:
            if stop.is_set():
                break
            profile = profiles[batch["profile"]]
            def run(specification):
                task = tasks[specification["task"]]
                spec = {**specification, "repeat": batch["repeat"], "batch": batch["batch"],
                        "contract_sha256": contracts[task.id]}
                if task.agent:
                    return agent_trial(backend, run_id, directory, spec, task, profile,
                                       images[task.image], stop, provider, ledger)
                return trial(backend, run_id, directory, spec, task, profile, images[task.image],
                             experiment.sample_interval_s if profile.sample_interval_s is None
                             else profile.sample_interval_s, stop)
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
        manifest["budget_ledger"] = ledger.data() if ledger else None
        manifest["engine_identity_final"] = engine_stability(backend, engine_identity)
        if not manifest["engine_identity_final"]["stable"]:
            manifest["warnings"] = manifest["warnings"] + [
                "Engine identity changed or became unreachable before the run finished. "
                "Recorded trials are unchanged; interpret them against that warning."]
        lock.release()
        manifest["finished_at"] = utc()
        manifest["recorded_trials"] = len(list((directory / "trials").glob("*.json")))
        manifest["planned_trials"] = sum(len(batch["trials"]) for batch in batches)
        write_json(directory / "manifest.json", manifest)
    return directory
