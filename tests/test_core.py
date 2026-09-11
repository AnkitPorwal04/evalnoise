from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from evalnoise.config import ConfigError, load, parse, plan
from evalnoise.docker import Docker, DockerError, classify, container_duration
from evalnoise.report import generate, summarize
from evalnoise.runner import execute, trial
from evalnoise.storage import write_json

ROOT = Path(__file__).resolve().parents[1]


def config():
    return json.loads((ROOT / "experiments/calibration.json").read_text())


def setUpModule():
    directory = tempfile.mkdtemp(prefix="evalnoise-state-")
    os.environ["EVALNOISE_STATE_DIR"] = directory


class FakeDocker:
    """Mirrors the real Docker backend contract used by the runner."""

    def __init__(self, state=None, failure=None, engine_id=None, telemetry_source="disabled"):
        self.state = state or {"Running": False, "Status": "exited", "ExitCode": 0, "OOMKilled": False,
                               "StartedAt": "2026-09-11T10:00:00Z", "FinishedAt": "2026-09-11T10:00:01Z"}
        self.failure = failure
        self.removed = []
        self.calls = []
        self.engine_id = engine_id or uuid.uuid4().hex
        self.engine_identity = {"id": self.engine_id}
        self.owner_token = None
        self.telemetry = {"telemetry_source": telemetry_source}
        self.profiles = {}
        self.streams = []
        self.identity_error = None
        self.engine_id_at_end = None

    def doctor(self):
        return {"engine": {"Architecture": "arm64", "NCPU": 4, "MemTotal": 2**30, "CgroupVersion": "2"},
                "engine_identity": {"id": self.engine_id}, "telemetry": self.telemetry,
                "endpoint": {"source": "default", "scheme": "unix", "local_unix_socket": True}}

    def identity(self):
        if self.identity_error:
            raise DockerError(self.identity_error)
        return {"id": self.engine_id_at_end or self.engine_id}

    def image(self, reference):
        digest = "sha256:" + hashlib.sha256(reference.encode()).hexdigest()
        return {"id": digest, "architecture": "arm64"}

    def create(self, name, run_id=None, task=None, profile=None, *args, **kwargs):
        if self.failure == "create":
            raise DockerError("create failed")
        if profile is not None:
            self.profiles[name] = profile
        return name

    def call(self, args, timeout=20, merge=False):
        self.calls.append(args)
        if args[0] == self.failure:
            raise DockerError("command failed")
        if args[0] == "kill":
            self.state = {**self.state, "Running": False, "Status": "exited", "ExitCode": 137}
        return ""

    def resources(self, name):
        profile = self.profiles.get(name)
        if profile is None:
            return {}
        return {"NanoCpus": round(profile.cpus * 10**9), "Memory": profile.memory_mb * 1024**2,
                "MemorySwap": profile.memory_mb * 1024**2, "PidsLimit": 128, "NetworkMode": "none",
                "ReadonlyRootfs": True, "CpusetCpus": profile.cpuset_cpus or ""}

    def inspect(self, name):
        if self.failure == "inspect":
            raise DockerError("inspect failed")
        return {"state": self.state, "resources": self.resources(name),
                "container_id": "c" * 64, "labels": {}}

    def stream(self, container_id, interval, started):
        return None

    def stats(self, name):
        return {"MemUsage": "1MiB / 48MiB"}

    def logs(self, name):
        if self.failure == "logs":
            raise DockerError("logs unavailable")
        return {"text": "<script>alert('untrusted log')</script>"}

    def remove(self, name):
        self.removed.append(name)
        if self.failure == "remove":
            raise DockerError("daemon unreachable")


class ConfigTests(unittest.TestCase):
    def test_valid_and_stable_digest(self):
        value = parse(config())
        self.assertEqual(value.digest(), parse(value.data() | {"tasks": [dict(t, command=list(t["command"])) for t in value.data()["tasks"]], "profiles": list(value.data()["profiles"])}).digest())

    def test_numeric_and_unknown_rejection(self):
        for field, value in (("seed", True), ("repeats", 0), ("repeats", 1.5), ("schema_version", True),
                             ("sample_interval_s", float("nan")), ("sample_interval_s", .1), ("unexpected", 1)):
            with self.subTest(field=field, value=value):
                data = config()
                data[field] = value
                with self.assertRaises(ConfigError):
                    parse(data)

    def test_command_is_argv_and_ids_safe(self):
        for key, value in (("command", "echo hello"), ("command", ["python", "x\0y"]),
                           ("id", "../../escape"), ("image", "--privileged")):
            data = config()
            data["tasks"][0][key] = value
            with self.assertRaises(ConfigError):
                parse(data)

    def test_duplicate_ids(self):
        data = config()
        data["tasks"].append(data["tasks"][0])
        with self.assertRaises(ConfigError):
            parse(data)

    def test_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"name": "first", "name": "second"}')
            with self.assertRaises(ConfigError):
                load(path)

    def test_schedule_complete_reproducible_and_blocked(self):
        experiment = parse(config())
        batches = plan(experiment)
        self.assertEqual(batches, plan(experiment))
        self.assertEqual(len(batches), 6)
        self.assertEqual(len({t["id"] for b in batches for t in b["trials"]}), 18)
        for repeat in range(3):
            block = [b for b in batches if b["repeat"] == repeat]
            self.assertEqual({b["profile"] for b in block}, {"tight", "roomy"})
            self.assertEqual([t["task"] for t in block[0]["trials"]], [t["task"] for t in block[1]["trials"]])

    def test_resource_bounds(self):
        for key, value in (("cpus", 0), ("memory_mb", 15), ("concurrency", 17), ("timeout_s", float("inf"))):
            data = config()
            data["profiles"][0][key] = value
            with self.assertRaises(ConfigError):
                parse(data)

    def test_large_integer_rejected_without_overflow(self):
        data = config()
        data["seed"] = 10**1000
        with self.assertRaises(ConfigError):
            parse(data)

    def test_separator_names_cannot_collide(self):
        data = config()
        data["profiles"][0]["id"] = "a-b"
        data["profiles"][1]["id"] = "a"
        data["tasks"][0]["id"] = "c"
        data["tasks"][1]["id"] = "b-c"
        ids = [t["id"] for b in plan(parse(data)) for t in b["trials"]]
        self.assertEqual(len(set(ids)), len(ids))


class EvidenceTests(unittest.TestCase):
    def test_exit137_is_not_oom_proof(self):
        self.assertEqual(classify({"Running": False, "Status": "exited", "ExitCode": 137}), "workload_failed")

    def test_outcome_precedence(self):
        state = {"Running": False, "Status": "exited", "ExitCode": 137, "OOMKilled": True}
        self.assertEqual(classify(state), "oom_killed")
        self.assertEqual(classify(state, timed_out=True), "timeout")
        self.assertEqual(classify(state, timed_out=True, cancelled=True), "cancelled")
        self.assertEqual(classify({"Error": "runtime error"}), "runtime_error")
        self.assertEqual(classify({}), "unknown")
        self.assertEqual(classify({"Running": False, "Status": "exited", "ExitCode": 7}, expected_exit=7), "passed")

    def test_duration_validation(self):
        self.assertEqual(container_duration(FakeDocker().state), 1)
        self.assertIsNone(container_duration({}))
        self.assertIsNone(container_duration({"StartedAt": "0001-01-01T00:00:00Z", "FinishedAt": "2026-09-11T00:00:00Z"}))

    def test_oom_with_expected_exit_is_not_clean_pass(self):
        self.assertEqual(classify({"ExitCode": 0, "OOMKilled": True}), "oom_observed_expected_exit")

    def test_missing_container_cleanup_is_idempotent(self):
        backend = Docker()
        with patch.object(backend, "call", side_effect=[DockerError("missing container"), ""]):
            backend.remove("missing")
        with patch.object(backend, "call", side_effect=[DockerError("removal failed"), "container-id"]):
            with self.assertRaises(DockerError):
                backend.remove("missing")
        with patch.object(backend, "call", side_effect=DockerError("daemon unavailable")):
            with self.assertRaises(DockerError):
                backend.remove("missing")

    def test_preflight_refuses_missing_enforcement(self):
        backend = Docker()
        for info in ({"OSType": "linux", "CgroupDriver": "none"},
                     {"OSType": "linux", "CgroupDriver": "systemd", "Warnings": ["No swap limit support"]}):
            with patch.object(backend, "call", side_effect=["{}", json.dumps(info)]):
                with self.assertRaises(DockerError):
                    backend.doctor()

    def test_create_security_and_immutable_image(self):
        backend = Docker()
        experiment = parse(config())
        with patch.object(backend, "call", return_value="id") as call:
            backend.create("name", "run", experiment.tasks[0], experiment.profiles[0], {"id": "sha256:abc"}, 42)
        args = call.call_args.args[0]
        for flag in ("--read-only", "--cap-drop", "--security-opt", "--user", "--pids-limit"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--memory") + 1], args[args.index("--memory-swap") + 1])
        self.assertIn("sha256:abc", args)
        self.assertNotIn("evalnoise-workloads:local", args)
        self.assertNotIn("--privileged", args)

    def test_subprocess_no_shell_and_combined_logs(self):
        backend = Docker()
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "out"
            run.return_value.stderr = "err"
            self.assertEqual(backend.logs("safe")["text"], "outerr")
            self.assertNotIn("shell", run.call_args.kwargs)


class LifecycleTests(unittest.TestCase):
    def test_post_kill_inspection_failure_preserves_cancellation(self):
        stop = threading.Event()
        class LostAfterKill(FakeDocker):
            def inspect(self, name):
                if any(call[0] == "kill" for call in self.calls):
                    raise DockerError("Post-kill inspection unavailable")
                if name in self.profiles:
                    stop.set()
                return {**super().inspect(name), "state": {"Running": True, "Status": "running"}}
        backend = LostAfterKill()
        experiment = parse(config())
        with tempfile.TemporaryDirectory() as root:
            result = trial(backend, "run", Path(root), {"id": "trial", "seed": 0}, experiment.tasks[0],
                           experiment.profiles[0], {"id": "image"}, 0, stop)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(backend.removed), 1)
        self.assertIn("Post-kill", result["error"])

    def test_unexpected_log_error_still_cleans_and_persists(self):
        class BrokenLogs(FakeDocker):
            def logs(self, name):
                raise ValueError("Unexpected log decoder bug")
        backend = BrokenLogs()
        experiment = parse(config())
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                trial(backend, "run", Path(root), {"id": "trial", "seed": 0}, experiment.tasks[0],
                      experiment.profiles[0], {"id": "image"}, 0, threading.Event())
            result = json.loads((Path(root) / "trials/trial.json").read_text())
        self.assertEqual(result["status"], "runner_error")
        self.assertEqual(len(backend.removed), 1)
        self.assertIn("log decoder", result["error"])
    def run_trial(self, backend, stop=None, interval=0):
        experiment = parse(config())
        profile = experiment.profiles[0]
        with tempfile.TemporaryDirectory() as directory:
            result = trial(backend, "abc", Path(directory),
                           {"id": "r000-tight-cpu", "task": "cpu", "repeat": 0, "seed": 42},
                           experiment.tasks[0], profile, {"id": "sha256:abc"}, interval, stop or threading.Event())
            persisted = json.loads((Path(directory) / "trials/r000-tight-cpu.json").read_text())
            self.assertEqual(result, persisted)
            return result

    def test_success_persisted_and_cleaned(self):
        backend = FakeDocker()
        result = self.run_trial(backend)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(len(backend.removed), 1)

    def test_create_and_runtime_failures_cleaned(self):
        for failure, outcome in (("create", "setup_error"), ("start", "runtime_error"), ("inspect", "runtime_error")):
            backend = FakeDocker(failure=failure)
            self.assertEqual(self.run_trial(backend)["status"], outcome)
            self.assertEqual(len(backend.removed), 1)

    def test_missing_logs_do_not_fabricate_failure(self):
        result = self.run_trial(FakeDocker(failure="logs"))
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["evidence_errors"])

    def test_cleanup_failure_explicit(self):
        self.assertTrue(self.run_trial(FakeDocker(failure="remove"))["cleanup_error"])

    def test_cancel_before_start(self):
        stop = threading.Event()
        stop.set()
        backend = FakeDocker()
        self.assertEqual(self.run_trial(backend, stop)["status"], "cancelled")
        self.assertEqual(backend.removed, [])

    def test_cancel_live_container(self):
        stop = threading.Event()
        backend = FakeDocker(state={"Running": True, "Status": "running"})
        original = backend.inspect
        def cancelling_inspect(name):
            stop.set()
            return original(name)
        backend.inspect = cancelling_inspect
        result = self.run_trial(backend, stop)
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(any(call[0] == "kill" for call in backend.calls))
        self.assertEqual(len(backend.removed), 1)

    def test_sampler_stopped_after_runtime_error(self):
        backend = FakeDocker(failure="inspect")
        before = set(threading.enumerate())
        result = self.run_trial(backend, interval=2)
        self.assertEqual(result["status"], "runtime_error")
        self.assertEqual(set(threading.enumerate()), before)

    def test_malformed_sample_is_recorded_without_thread_error(self):
        backend = FakeDocker()
        backend.stats = lambda _: json.loads("not-json")
        result = self.run_trial(backend, interval=2)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["telemetry_errors"])

    def test_unexpected_error_preserves_diagnostics_and_cleanup(self):
        backend = FakeDocker()
        backend.inspect = lambda _: {}
        experiment = parse(config())
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KeyError):
                trial(backend, "abc", Path(directory), {"id": "broken", "seed": 42},
                      experiment.tasks[0], experiment.profiles[0], {"id": "sha256:abc"}, 0, threading.Event())
            result = json.loads((Path(directory) / "trials/broken.json").read_text())
            self.assertEqual(result["status"], "runner_error")
            self.assertIn("KeyError", result["error"])
        self.assertEqual(len(backend.removed), 1)

    def test_timeout_kills_and_retains_evidence(self):
        backend = FakeDocker(state={"Running": True, "Status": "running"})
        with patch("evalnoise.runner.time.monotonic", side_effect=[0, 0, 0, 20, 21]):
            result = self.run_trial(backend)
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(any(call[0] == "kill" for call in backend.calls))

    def test_sampling_records_raw_not_peak(self):
        result = self.run_trial(FakeDocker(), interval=2)
        self.assertTrue(result["telemetry"])
        self.assertIn("raw", result["telemetry"][0])

    def test_full_experiment_and_offline_report(self):
        with tempfile.TemporaryDirectory() as directory:
            experiment = parse(config())
            run = execute(experiment, directory, FakeDocker())
            summary = generate(run)
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["recorded_trials"], 18)
            self.assertEqual(summary["comparisons"][0]["complete_pairs"], 9)
            self.assertEqual(summary["comparisons"][0]["paired_pass_delta"], 0)
            page = (run / "report.html").read_text()
            self.assertNotIn("<script>", page)
            self.assertIn("&lt;script&gt;", page)
            self.assertIn("Content-Security-Policy", page)

    def test_failed_cleanup_halts_future_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            run = execute(parse(config()), directory, FakeDocker(failure="remove"))
            summary = generate(run)
            self.assertEqual(summary["status"], "cleanup_failed")
            self.assertEqual(summary["recorded_trials"], 3)
            self.assertEqual(sum(p["missing"] for p in summary["profiles"]), 15)

    def test_concurrency_is_bounded_and_profiles_do_not_overlap(self):
        class CountingDocker(FakeDocker):
            def __init__(self):
                super().__init__()
                self.lock = threading.Lock()
                self.active = {}
                self.peak = 0
                self.mixed = False

            def create(self, name, run_id, task, profile, *args):
                with self.lock:
                    self.active[name] = profile.id
                    self.peak = max(self.peak, len(self.active))
                    self.mixed |= len(set(self.active.values())) > 1
                time.sleep(.02)
                return name

            def remove(self, name):
                with self.lock:
                    del self.active[name]

        data = config()
        data["repeats"] = 1
        for profile in data["profiles"]:
            profile["concurrency"] = 2
        backend = CountingDocker()
        with tempfile.TemporaryDirectory() as directory:
            execute(parse(data), directory, backend)
        self.assertEqual(backend.peak, 2)
        self.assertFalse(backend.mixed)
        self.assertEqual(backend.active, {})

    def test_report_missing_and_duplicate_records(self):
        experiment = parse(config())
        manifest = {"run_id": "x", "status": "interrupted", "config": experiment.data(), "plan": plan(experiment)}
        summary = summarize(manifest, [])
        self.assertIsNone(summary["profiles"][0]["pass_rate_recorded"])
        self.assertIsNone(summary["comparisons"][0]["paired_pass_delta"])
        batch = manifest["plan"][0]
        record = {**batch["trials"][0], "repeat": batch["repeat"], "profile": batch["profile"], "status": "passed"}
        with self.assertRaises(ValueError):
            summarize(manifest, [record, record])
        record["task"] = "wrong-task"
        with self.assertRaises(ValueError):
            summarize(manifest, [record])

    def test_edited_manifest_values_cannot_inject_html(self):
        with tempfile.TemporaryDirectory() as directory:
            run = execute(parse(config()), directory, FakeDocker())
            manifest = json.loads((run / "manifest.json").read_text())
            manifest["config"]["profiles"][0]["cpus"] = "<img src=x>"
            manifest["config"]["sample_interval_s"] = "<script>bad()</script>"
            write_json(run / "manifest.json", manifest)
            generate(run)
            page = (run / "report.html").read_text()
            self.assertNotIn("<img", page)
            self.assertNotIn("<script", page)
            self.assertIn("&lt;img", page)

    def test_missing_and_cancelled_pair_denominators(self):
        experiment = parse(config())
        manifest = {"run_id": "x", "status": "interrupted", "config": experiment.data(), "plan": plan(experiment)}
        records = []
        for batch in manifest["plan"]:
            if batch["repeat"] != 0:
                continue
            for specification in batch["trials"]:
                if specification["task"] == "repository":
                    continue
                status = "cancelled" if specification["task"] == "cpu" and batch["profile"] == "tight" else "passed"
                if specification["task"] == "memory" and batch["profile"] == "tight":
                    status = "oom_killed"
                records.append({**specification, "profile": batch["profile"], "repeat": 0, "status": status})
        summary = summarize(manifest, records)
        self.assertEqual(summary["profiles"][0]["pass_rate_recorded"], 0)
        self.assertEqual(summary["profiles"][0]["missing"], 7)
        self.assertEqual(summary["profiles"][1]["pass_rate_recorded"], 1)
        self.assertEqual(summary["comparisons"][0]["complete_pairs"], 1)
        self.assertEqual(summary["comparisons"][0]["fail_to_pass"], 1)
        self.assertEqual(summary["comparisons"][0]["paired_pass_delta"], 1)

    def test_atomic_json_and_invalid_number(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            write_json(path, {"value": 1})
            with self.assertRaises(ValueError):
                write_json(path, {"value": float("nan")})
            self.assertEqual(json.loads(path.read_text()), {"value": 1})
            self.assertEqual(len(list(Path(directory).iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
