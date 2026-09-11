"""Opt-in real engine tests. Build workloads first; never pull during trials."""

import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from evalnoise.config import Profile, Task
from evalnoise.docker import Docker, DockerError
from evalnoise.runner import trial


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
        self.assertEqual(result["status"], "oom_killed", result)
        self.assertTrue(result["inspection"]["state"]["OOMKilled"])
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
