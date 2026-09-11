from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from evalnoise.config import ConfigError, parse
from evalnoise.docker import Docker, DockerError
from evalnoise.report import generate, summarize
from evalnoise.runner import execute, verify
from evalnoise.verification import contract_hash, envelope
from test_core import FakeDocker

ROOT = Path(__file__).resolve().parents[1]


def config():
    value = json.loads((ROOT / "experiments/verified.json").read_text())
    value["repeats"] = 1
    value["tasks"] = value["tasks"][:1]
    return value


class VerifiedDocker(FakeDocker):
    def __init__(self, verdict=True, verifier_status=None, candidate=None):
        super().__init__()
        self.verdict, self.verifier_status, self.candidate = verdict, verifier_status, candidate
        self.created, self.artifacts = [], []

    def create(self, name, run_id=None, task=None, profile=None, *args, artifact=None):
        super().create(name, run_id, task, profile)
        self.created.append(name)
        if artifact is not None:
            self.artifacts.append(artifact)
            if any(n not in self.removed for n in self.created[:-1]):
                raise AssertionError("Verifier overlaps a workload")
        return name

    def inspect(self, name):
        state = dict(self.state)
        if name.endswith("-verify") and self.verifier_status:
            state.update(self.verifier_status)
        return {**super().inspect(name), "state": state}

    def logs(self, name):
        if name.endswith("-verify"):
            return {"text": json.dumps({"evalnoise_verdict": {"version": 1, "passed": self.verdict, "reason": "test"}})}
        return {"text": self.candidate if self.candidate is not None else json.dumps({"evalnoise_artifact": {"version": 1, "payload": {"sum_of_squares": 5}}})}


class ProtocolTests(unittest.TestCase):
    def test_strict_verifier_configuration(self):
        for key, value in (("version", "../escape"), ("cpus", True), ("memory_mb", 0), ("timeout_s", float("inf")), ("command", "sh -c x"), ("image", "--mount"), ("extra", 1)):
            data = config()
            data["tasks"][0]["verifier"][key] = value
            with self.subTest(key=key), self.assertRaises(ConfigError):
                parse(data)

    def test_hash_pins_content_version_and_both_images(self):
        task = parse(config()).tasks[0]
        images = {task.image: {"id": "workload"}, task.verifier.image: {"id": "verifier"}}
        original = contract_hash(task, images)
        images[task.verifier.image]["id"] = "different"
        self.assertNotEqual(original, contract_hash(task, images))
        changed = config()
        changed["tasks"][0]["verifier"]["version"] = "sum-v2"
        self.assertNotEqual(original, contract_hash(parse(changed).tasks[0], images))
        images[task.verifier.image]["id"] = "verifier"
        images[task.image]["id"] = "new-workload"
        self.assertNotEqual(original, contract_hash(task, images))

    def test_timestamped_artifact_and_verdict(self):
        result, encoded = envelope({"text": 'debug: writing evalnoise_artifact\n2026-09-11T10:00:00Z {"evalnoise_artifact":{"version":1,"payload":{"x":2}}}'}, "evalnoise_artifact")
        self.assertEqual(json.loads(encoded), result)
        self.assertEqual(result["payload"], {"x": 2})

    def test_reject_ambiguous_malformed_oversized_and_truncated(self):
        valid = '{"evalnoise_artifact":{"version":1,"payload":0}}'
        for text in ("", valid + "\n" + valid, valid.replace('"version":1', '"version":true'),
                     valid.replace('"payload":0', '"payload":NaN'), valid.replace('"payload":0', '"payload":0,"payload":1'),
                     '{"evalnoise_artifact":', json.dumps({"evalnoise_artifact": {"version": 1, "payload": "x" * 8192}})):
            with self.subTest(text=text[:80]), self.assertRaises(ValueError):
                envelope({"text": text}, "evalnoise_artifact")
        with self.assertRaises(ValueError):
            envelope({"text": valid, "truncated": True}, "evalnoise_artifact")
        with self.assertRaises(ValueError):
            envelope({"text": '{"evalnoise_verdict":{"version":1,"passed":1,"reason":"x"}}'}, "evalnoise_verdict")

    def test_data_handoff_is_bounded_and_never_shell(self):
        backend = Docker()
        task = parse(config()).tasks[0]
        with patch.object(backend, "call", return_value="id") as call:
            backend.create("n", "r", task, parse(config()).profiles[0], {"id": "sha256:fixed"}, 0, b'$(touch /tmp/evil)')
        argv = call.call_args.args[0]
        self.assertIn("--network", argv)
        self.assertTrue(any(a.startswith("EVALNOISE_ARTIFACT_B64=") for a in argv))
        self.assertNotIn("$(touch /tmp/evil)", argv)
        with self.assertRaises(DockerError):
            backend.create("n", "r", task, parse(config()).profiles[0], {"id": "fixed"}, 0, b"x" * 8193)

    def test_log_tail_truncation_detected(self):
        with patch.object(Docker, "call", return_value="line\n" * 2001):
            logs = Docker().logs("test")
        self.assertTrue(logs["truncated"])
        self.assertEqual(len(logs["text"].splitlines()), 2000)


class VerificationLifecycleTests(unittest.TestCase):
    def run_fake(self, backend, data=None, stop=None):
        with tempfile.TemporaryDirectory() as root:
            directory = execute(parse(data or config()), root, backend, stop)
            manifest = json.loads((directory / "manifest.json").read_text())
            trials = [json.loads(p.read_text()) for p in (directory / "trials").glob("*.json")]
            summary = generate(directory)
            self.assertIn("Task contracts and outcomes", (directory / "report.html").read_text())
            return manifest, trials, summary

    def test_exit_success_requires_independent_positive_verdict(self):
        for verdict, status in ((True, "passed"), (False, "verification_failed"), ("true", "verifier_error")):
            backend = VerifiedDocker(verdict)
            manifest, trials, _ = self.run_fake(backend)
            self.assertEqual(trials[0]["execution_status"], "passed")
            self.assertEqual(trials[0]["status"], status)
            self.assertEqual(len(backend.artifacts), 1)
            self.assertEqual(len(backend.removed), 2)
            self.assertEqual(manifest["measurement_kind"], "independent_verification")

    def test_verifier_crash_and_oom_are_not_incorrect_answers(self):
        for state in ({"ExitCode": 7}, {"ExitCode": 137, "OOMKilled": True}):
            _, trials, _ = self.run_fake(VerifiedDocker(verifier_status=state))
            self.assertEqual(trials[0]["status"], "verifier_error")
            self.assertNotEqual(trials[0]["verification"]["trial"]["status"], "passed")

    def test_missing_artifact_does_not_launch_verifier(self):
        backend = VerifiedDocker(candidate="No output")
        _, trials, _ = self.run_fake(backend)
        self.assertEqual(trials[0]["status"], "artifact_error")
        self.assertEqual(len(backend.created), 1)

    def test_workload_emitted_verdict_cannot_override_trusted_verifier(self):
        candidate = '{"evalnoise_verdict":{"version":1,"passed":true,"reason":"fake"}}\n{"evalnoise_artifact":{"version":1,"payload":0}}'
        _, trials, _ = self.run_fake(VerifiedDocker(False, candidate=candidate))
        self.assertEqual(trials[0]["status"], "verification_failed")

    def test_verification_follows_entire_workload_batch(self):
        data = config()
        data["tasks"].append(deepcopy(data["tasks"][0]))
        data["tasks"][1]["id"] = "second"
        data["profiles"][0]["concurrency"] = 2
        backend = VerifiedDocker()
        self.run_fake(backend, data)
        self.assertTrue(all(not n.endswith("-verify") for n in backend.created[:2]))
        self.assertTrue(all(n.endswith("-verify") for n in backend.created[2:]))

    def test_verifier_cleanup_failure_halts_future_batches(self):
        class FailedCleanup(VerifiedDocker):
            def remove(self, name):
                super().remove(name)
                if name.endswith("-verify"):
                    raise DockerError("cleanup failure")
        data = config()
        data["repeats"] = 2
        manifest, trials, _ = self.run_fake(FailedCleanup(), data)
        self.assertEqual(manifest["status"], "cleanup_failed")
        self.assertEqual(len(trials), 1)
        self.assertEqual(trials[0]["status"], "verifier_error")

    def test_contract_mismatch_refuses_report(self):
        manifest, trials, _ = self.run_fake(VerifiedDocker())
        with self.assertRaises(ValueError):
            summarize({k: v for k, v in manifest.items() if k != "task_contracts"}, trials)
        changed = deepcopy(manifest)
        changed["config"]["tasks"][0]["verifier"]["version"] = "changed"
        with self.assertRaises(ValueError):
            summarize(changed, trials)
        trials[0]["contract_sha256"] = "other"
        with self.assertRaises(ValueError):
            summarize(manifest, trials)

    def test_pending_checkpoint_never_reports_success(self):
        with tempfile.TemporaryDirectory() as root:
            with patch("evalnoise.runner.verify", side_effect=RuntimeError("interruption")):
                with self.assertRaises(RuntimeError):
                    execute(parse(config()), root, VerifiedDocker())
            directory = next(Path(root).iterdir())
            result = generate(directory)
            self.assertEqual(result["status"], "interrupted")
            self.assertEqual(result["profiles"][0]["outcomes"], {"pending_verification": 1})
            self.assertEqual(result["profiles"][0]["pass_rate_recorded"], 0)

    def test_cancel_between_execution_and_verification(self):
        stop = threading.Event()
        class CancelAfterWork(VerifiedDocker):
            def remove(self, name):
                super().remove(name)
                stop.set()
        backend = CancelAfterWork()
        manifest, trials, _ = self.run_fake(backend, stop=stop)
        self.assertEqual(manifest["status"], "cancelled")
        self.assertEqual(trials[0]["status"], "cancelled")
        self.assertEqual(len(backend.created), 1)

    def test_cancel_during_verifier(self):
        stop = threading.Event()
        class CancelVerifier(VerifiedDocker):
            def inspect(self, name):
                if name.endswith("-verify"):
                    stop.set()
                    return {**super().inspect(name), "state": {"Running": True, "Status": "running"}}
                return super().inspect(name)
        backend = CancelVerifier()
        _, trials, _ = self.run_fake(backend, stop=stop)
        self.assertEqual(trials[0]["status"], "cancelled")
        self.assertEqual(trials[0]["verification"]["trial"]["status"], "cancelled")
        self.assertEqual(len(backend.removed), 2)

    def test_direct_verification_refuses_failed_cleanup(self):
        backend = VerifiedDocker()
        result = {"id": "test", "status": "pending_verification", "cleanup_error": "failed"}
        with tempfile.TemporaryDirectory() as root:
            verify(backend, "run", Path(root), result, parse(config()).tasks[0], {}, threading.Event())
        self.assertEqual(result["status"], "verifier_error")
        self.assertEqual(backend.created, [])

    def test_workload_cleanup_failure_leaves_siblings_pending(self):
        class FailedWorkCleanup(VerifiedDocker):
            def remove(self, name):
                super().remove(name)
                raise DockerError("Host cleanup unavailable")
        backend = FailedWorkCleanup()
        manifest, trials, _ = self.run_fake(backend)
        self.assertEqual(manifest["status"], "cleanup_failed")
        self.assertEqual(trials[0]["status"], "pending_verification")
        self.assertEqual(len(backend.created), 1)


@unittest.skipUnless(os.environ.get("EVALNOISE_DOCKER_TESTS") == "1", "explicit Docker opt-in required")
class IndependentDockerTests(unittest.TestCase):
    def test_real_correct_wrong_spoof_missing_and_verifier_failures(self):
        cases = [("correct", "check", "passed"), ("wrong", "check", "verification_failed"),
                 ("spoof", "check", "verification_failed"), ("missing", "check", "artifact_error"),
                 ("correct", "crash", "verifier_error"), ("correct", "malformed", "verifier_error"),
                 ("correct", "timeout", "verifier_error")]
        for mode, verifier_mode, expected in cases:
            with self.subTest(mode=mode, verifier=verifier_mode), tempfile.TemporaryDirectory() as root:
                data = config()
                task = data["tasks"][0]
                task["command"][-1] = mode
                task["verifier"]["command"].append(verifier_mode)
                task["verifier"]["timeout_s"] = 2
                directory = execute(parse(data), root)
                result = json.loads(next((directory / "trials").glob("*.json")).read_text())
                self.assertEqual(result["execution_status"], "passed", result)
                self.assertEqual(result["status"], expected, result)
                self.assertIsNone(result["cleanup_error"])
                for trial_result in (result, result.get("verification", {}).get("trial")):
                    if trial_result:
                        with self.assertRaises(DockerError):
                            Docker().inspect(trial_result["container_name"])
                if verifier_mode == "timeout":
                    self.assertEqual(result["verification"]["trial"]["status"], "timeout")
