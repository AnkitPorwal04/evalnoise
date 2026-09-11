"""Opt-in real engine gates for hard-kill recovery, run coordination, and batch ordering.

These start real containers. They only ever remove fixtures they created, through the
ownership-checked cleanup path; nothing here prunes or sweeps the engine.
"""

from datetime import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from evalnoise.config import parse
from evalnoise.coordination import CoordinationError, LABEL_RUN, live_holder
from evalnoise.docker import Docker
from evalnoise.recovery import cleanup, diagnose
from evalnoise.runner import execute

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "evalnoise-workloads:local"

CHILD = """
import os, sys
sys.path.insert(0, {root!r})
os.environ["EVALNOISE_STATE_DIR"] = {state!r}
from evalnoise.config import parse
from evalnoise.runner import execute
execute(parse({data!r}), {output!r})
"""


def blocking_config(name, timeout_s=120):
    return {"schema_version": 1, "name": name, "seed": 5, "repeats": 1,
            "tasks": [{"id": "blocker", "image": IMAGE,
                       "command": ["python", "/opt/evalnoise/workload.py", "timeout"]}],
            "profiles": [{"id": "hold", "cpus": 1, "memory_mb": 128, "timeout_s": timeout_s}]}


def instant(seconds):
    try:
        return datetime.fromisoformat(seconds.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


@unittest.skipUnless(os.environ.get("EVALNOISE_DOCKER_TESTS") == "1", "explicit Docker opt-in required")
class RealRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.backend = Docker()
        self.backend.doctor()
        self.state = tempfile.mkdtemp(prefix="evalnoise-real-state-")
        self.output = tempfile.mkdtemp(prefix="evalnoise-real-out-")
        self.child = None
        # Parent and child must resolve the same lock file, or they never actually contend.
        self.previous_state = os.environ.get("EVALNOISE_STATE_DIR")
        os.environ["EVALNOISE_STATE_DIR"] = self.state

    def tearDown(self):
        if self.child is not None:
            if self.child.poll() is None:
                self.child.kill()
                self.child.wait(15)
            for stream in (self.child.stdout, self.child.stderr):
                if stream is not None:
                    stream.close()
        for directory in Path(self.output).iterdir():
            if (directory / "manifest.json").exists():
                try:
                    cleanup(Docker(), directory, confirm=True)
                except Exception:
                    pass
        if self.previous_state is None:
            os.environ.pop("EVALNOISE_STATE_DIR", None)
        else:
            os.environ["EVALNOISE_STATE_DIR"] = self.previous_state

    def start_child(self, data, output):
        script = CHILD.format(root=str(ROOT), state=self.state, data=data, output=str(output))
        environment = dict(os.environ, EVALNOISE_STATE_DIR=self.state)
        self.child = subprocess.Popen([sys.executable, "-c", script], env=environment,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return self.child

    def await_container(self, deadline_s=60):
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            listing = self.backend.call(
                ["ps", "--filter", f"label={LABEL_RUN}", "--format", "{{.Names}}"], 20).split()
            if listing:
                return listing[0]
            if self.child.poll() is not None:
                self.fail(f"child exited early: {self.child.stderr.read().decode()[-800:]}")
            time.sleep(.25)
        self.fail("no EvalNoise container appeared within the deadline")

    def only_run(self, output):
        directories = [p for p in Path(output).iterdir() if (p / "manifest.json").exists()]
        self.assertEqual(len(directories), 1, directories)
        return directories[0]

    def test_hard_kill_leaves_a_diagnosable_orphan_that_only_cleanup_removes(self):
        self.start_child(blocking_config("killed-run"), self.output)
        name = self.await_container()
        self.child.send_signal(signal.SIGKILL)
        self.child.wait(20)
        directory = self.only_run(self.output)

        first = diagnose(self.backend, directory)
        self.assertTrue(first["read_only"])
        self.assertEqual(first["recorded_trials"], 0)
        self.assertEqual(first["missing_trials"], ["r000-p00-t000"])
        self.assertEqual(first["run_status"], "running")
        self.assertTrue(first["engine_matches"])
        self.assertIsNone(first["live_coordinator"], "SIGKILL must release the engine lock")
        surviving = [entry for entry in first["containers"] if entry["name"] == name]
        self.assertEqual(len(surviving), 1, first["containers"])
        self.assertTrue(surviving[0]["owned"], surviving[0]["refusals"])
        self.assertTrue(surviving[0]["running"])
        container_id = surviving[0]["container_id"]
        self.assertRegex(container_id, r"^[0-9a-f]{64}$")

        before = {p.name: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
        audit = cleanup(self.backend, directory, confirm=True)
        self.assertEqual([entry["container_id"] for entry in audit["removed"]], [container_id])
        self.assertEqual(audit["failures"], [])

        after = diagnose(self.backend, directory)
        self.assertEqual(after["containers"], [])
        self.assertEqual(after["missing_trials"], ["r000-p00-t000"])
        self.assertEqual(after["run_status"], "running")
        preserved = {p.name: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
        self.assertEqual(before, {k: v for k, v in preserved.items() if k != "cleanup.json"})
        self.assertIn("cleanup.json", preserved)

    def test_second_run_is_refused_while_the_first_holds_the_engine(self):
        self.start_child(blocking_config("holding-run"), self.output)
        self.await_container()
        holder = live_holder(self.backend.identity()["id"])
        self.assertIsNotNone(holder)

        other = tempfile.mkdtemp(prefix="evalnoise-real-out2-")
        with self.assertRaises(CoordinationError) as raised:
            execute(parse(blocking_config("second-run")), other, Docker())
        message = str(raised.exception)
        self.assertIn("holds this engine", message)
        self.assertIn(holder["run_id"], message)
        self.assertIn("Not a distributed lock", message)
        self.assertEqual([p for p in Path(other).iterdir()], [],
                         "a refused run must not create an output directory")

        self.child.send_signal(signal.SIGKILL)
        self.child.wait(20)
        self.assertIsNone(live_holder(self.backend.identity()["id"]))

    def test_batch_isolation_and_real_concurrency_under_load(self):
        data = {"schema_version": 1, "name": "load-ordering", "seed": 11, "repeats": 1,
                "tasks": [{"id": "left", "image": IMAGE,
                           "command": ["python", "/opt/evalnoise/workload.py", "cpu-long"]},
                          {"id": "right", "image": IMAGE,
                           "command": ["python", "/opt/evalnoise/workload.py", "cpu-long"]}],
                "profiles": [{"id": "pair-a", "cpus": 1, "memory_mb": 256, "timeout_s": 120, "concurrency": 2},
                             {"id": "pair-b", "cpus": 1, "memory_mb": 256, "timeout_s": 120, "concurrency": 2}]}
        directory = execute(parse(data), self.output, self.backend)
        trials = [json.loads(p.read_text()) for p in (directory / "trials").glob("*.json")]
        self.assertEqual(len(trials), 4, trials)
        self.assertEqual({t["status"] for t in trials}, {"passed"})

        spans = {}
        for trial in trials:
            state = trial["inspection"]["state"]
            start, end = instant(state["StartedAt"]), instant(state["FinishedAt"])
            self.assertIsNotNone(start)
            self.assertIsNotNone(end)
            spans.setdefault(trial["profile"], []).append((start, end, trial["id"]))
        self.assertEqual(len(spans), 2)

        for profile, entries in spans.items():
            self.assertEqual(len(entries), 2, profile)
            (first_start, first_end, _), (second_start, second_end, _) = sorted(entries)
            self.assertLess(second_start, first_end,
                            f"{profile} trials did not overlap, so concurrency was not exercised")

        (a_start, a_end) = min(s for s, _, _ in spans["pair-a"]), max(e for _, e, _ in spans["pair-a"])
        (b_start, b_end) = min(s for s, _, _ in spans["pair-b"]), max(e for _, e, _ in spans["pair-b"])
        earlier, later = sorted([(a_start, a_end), (b_start, b_end)])
        self.assertLessEqual(earlier[1], later[0],
                             "profile batches overlapped; batches must not run beside each other")

        for trial in trials:
            self.assertTrue(trial["resource_audit"]["enforced_as_requested"], trial["resource_audit"])
            self.assertIsNone(trial["cleanup_error"])


if __name__ == "__main__":
    unittest.main()
