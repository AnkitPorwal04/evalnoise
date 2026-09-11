"""Opt-in real engine tests. Build workloads first; never pull during trials."""

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from evalnoise import endpoint as endpoints, probe
from evalnoise.config import Profile, Task, parse
from evalnoise.docker import Docker, DockerError
from evalnoise.recovery import cleanup, diagnose
from evalnoise.runner import execute, trial


@unittest.skipUnless(os.environ.get("EVALNOISE_DOCKER_TESTS") == "1", "explicit Docker opt-in required")
class DockerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Docker()
        cls.backend.doctor()
        cls.image = cls.backend.image("evalnoise-workloads:local")

    def run_mode(self, mode, memory=256, timeout=10, interval=0):
        task = Task(mode, "evalnoise-workloads:local", ("python", "/opt/evalnoise/workload.py", mode))
        profile = Profile("test", 1, memory, timeout)
        with tempfile.TemporaryDirectory() as directory:
            result = trial(self.backend, uuid.uuid4().hex[:12], Path(directory),
                           {"id": mode, "task": mode, "seed": 42, "repeat": 0},
                           task, profile, self.image, interval, threading.Event())
        self.assertIsNone(result["cleanup_error"])
        with self.assertRaises(DockerError):
            self.backend.inspect(result["container_name"])
        return result

    def test_memory_pressure_and_recorded_limits(self):
        result = self.run_mode("memory", memory=48)
        state = result["inspection"]["state"]
        # Engines may expose allocation failure or SIGKILL without an OOM flag.
        # Require failure and preserve the distinction instead of inventing OOM evidence.
        if state["OOMKilled"]:
            self.assertEqual(result["status"], "oom_killed", result)
        else:
            self.assertEqual(result["status"], "workload_failed", result)
            self.assertIn(state["ExitCode"], (1, 137), result)
            if state["ExitCode"] == 1:
                self.assertIn("MemoryError", result["logs"]["text"], result)
        resources = result["inspection"]["resources"]
        self.assertEqual(resources["Memory"], 48 * 1024**2)
        self.assertEqual(resources["MemorySwap"], resources["Memory"])
        self.assertEqual(resources["NanoCpus"], 10**9)
        self.assertEqual(resources["NetworkMode"], "none")
        self.assertTrue(resources["ReadonlyRootfs"])

    def test_roomy_workload_passes(self):
        self.assertEqual(self.run_mode("memory")["status"], "passed")

    def test_exit137_without_oom(self):
        result = self.run_mode("exit137")
        self.assertEqual(result["status"], "workload_failed")
        self.assertFalse(result["inspection"]["state"]["OOMKilled"])

    def test_nonzero_exit_and_stderr(self):
        result = self.run_mode("failure")
        self.assertEqual(result["status"], "workload_failed")
        self.assertEqual(result["inspection"]["state"]["ExitCode"], 7)
        self.assertIn("Intentional nonzero calibration outcome", result["logs"]["text"])

    def test_timeout_sampling_and_cleanup(self):
        result = self.run_mode("timeout", timeout=4, interval=2)
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["timed_out"])
        self.assertTrue(result["telemetry"])


@unittest.skipUnless(os.environ.get("EVALNOISE_DOCKER_TESTS") == "1", "explicit Docker opt-in required")
class EngineTelemetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Docker()
        cls.environment = cls.backend.doctor()
        cls.image = cls.backend.image("evalnoise-workloads:local")

    def test_endpoint_and_daemon_identity_are_pinned(self):
        endpoint = self.environment["endpoint"]
        self.assertIn(endpoint["source"], ("DOCKER_CONTEXT", "DOCKER_HOST", "context", "default"))
        self.assertNotIn("TLSMaterial", json.dumps(endpoint))
        self.assertTrue(self.environment["engine_identity"]["id"])
        self.assertEqual(self.environment["engine_identity"]["id"], self.backend.identity()["id"])

    def test_negotiation_pins_a_version_the_daemon_supports(self):
        telemetry = self.environment["telemetry"]
        if telemetry["telemetry_source"] != "engine_stream":
            self.skipTest(f"Streaming unavailable here: {telemetry['reason']}")
        pinned = endpoints.parse_version(telemetry["api_version_pinned"])
        self.assertGreaterEqual(pinned, endpoints.parse_version(telemetry["api_version_server_minimum"]))
        self.assertGreaterEqual(pinned, endpoints.STATS_API_FLOOR)

    def test_streaming_records_raw_throttling_counters(self):
        if self.environment["telemetry"]["telemetry_source"] != "engine_stream":
            self.skipTest("Streaming unavailable on this endpoint")
        task = Task("timeout", "evalnoise-workloads:local",
                    ("python", "/opt/evalnoise/workload.py", "timeout"))
        profile = Profile("stream", .5, 256, 6)
        with tempfile.TemporaryDirectory() as directory:
            result = trial(self.backend, uuid.uuid4().hex[:12], Path(directory),
                           {"id": "stream", "task": "timeout", "seed": 1, "repeat": 0},
                           task, profile, self.image, 2, threading.Event())
        self.assertEqual(result["telemetry_source"], "engine_stream")
        self.assertIsNone(result["cleanup_error"])
        self.assertGreaterEqual(len(result["telemetry"]), 1, result["telemetry_meta"])
        self.assertTrue(result["telemetry_meta"]["closed_before_cleanup"])
        self.assertFalse(result["telemetry_meta"]["thread_leaked"])
        usable = [s for s in result["telemetry"] if not s["degraded"]]
        self.assertTrue(usable, result["telemetry"])
        throttling = usable[0]["raw"]["cpu_stats"]["throttling_data"]
        for field in ("periods", "throttled_periods", "throttled_time"):
            self.assertIsInstance(throttling[field], int)

    def test_cgroup_v2_unavailable_fields_are_listed_not_zeroed(self):
        support = self.environment["telemetry_support"]
        if support["cgroup_version"] != "2":
            self.skipTest("Engine is not cgroup v2")
        self.assertIn("memory_stats.max_usage", support["unavailable"])

    def test_enforcement_probe_reports_real_cgroup_limits(self):
        profile = Profile("probe", 1, 128, 20)
        with tempfile.TemporaryDirectory() as output:
            result = probe.run(self.backend, "evalnoise-probe:local", [profile], output)
        entry = result["profiles"][0]
        self.assertEqual(entry["execution_status"], "passed", entry)
        self.assertTrue(entry["evaluation"]["conclusive"], entry)
        self.assertTrue(entry["evaluation"]["enforced_as_requested"], entry)
        self.assertEqual(entry["payload"]["files"]["memory_swap_max"], "0")
        self.assertTrue(entry["resource_audit"]["enforced_as_requested"])

    def test_affinity_mask_is_applied_and_effective(self):
        count = self.environment["engine"]["NCPU"]
        if not isinstance(count, int) or count < 2:
            self.skipTest("Engine reports fewer than two CPUs")
        profile = Profile("pinned", 1, 128, 20, 1, "0-1")
        with tempfile.TemporaryDirectory() as output:
            result = probe.run(self.backend, "evalnoise-probe:local", [profile], output)
        entry = result["profiles"][0]
        self.assertEqual(entry["execution_status"], "passed", entry)
        self.assertEqual(entry["resource_audit"]["observed"]["CpusetCpus"], "0-1")
        self.assertTrue(entry["evaluation"]["affinity"]["effective_is_requested"], entry["evaluation"])

    def test_diagnose_and_cleanup_on_a_real_leftover_container(self):
        with tempfile.TemporaryDirectory() as root:
            directory = execute(parse(self.calibration()), root, self.backend)
            report = diagnose(self.backend, directory)
            self.assertTrue(report["engine_matches"])
            self.assertEqual(report["missing_trials"], [])
            self.assertEqual(report["containers"], [])
            audit = cleanup(self.backend, directory, confirm=True)
            self.assertEqual(audit["removed"], [])
            manifest = json.loads((directory / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "completed")
            self.assertTrue(manifest["engine_identity_final"]["stable"])

    def calibration(self):
        data = json.loads((Path(__file__).resolve().parents[1] / "experiments/calibration.json").read_text())
        data["repeats"] = 1
        data["tasks"] = data["tasks"][:1]
        data["profiles"] = data["profiles"][1:]
        return data
