"""Opt-in real engine gates for the agent slice: tool containers, isolation, and recovery.

These start real containers with the reviewed in-repo tool image. They remove only
fixtures they created, through the ownership-checked cleanup path. No provider is
contacted and no credential is read.
"""

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
from evalnoise.docker import Docker
from evalnoise.recovery import cleanup, diagnose, expected_names
from evalnoise.report import generate
from evalnoise.runner import execute

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "experiments"
TOOL_IMAGE = "evalnoise-tools:local"

CHILD = """
import os, sys
sys.path.insert(0, {root!r})
os.environ["EVALNOISE_STATE_DIR"] = {state!r}
from evalnoise.config import parse
from evalnoise.runner import execute
execute(parse({data!r}), {output!r}, config_dir={experiments!r})
"""


def config(**overrides):
    value = json.loads((EXPERIMENTS / "agent-offline.json").read_text())
    value.update(overrides)
    return value


@unittest.skipUnless(os.environ.get("EVALNOISE_DOCKER_TESTS") == "1", "explicit Docker opt-in required")
class RealAgentTests(unittest.TestCase):
    def setUp(self):
        self.backend = Docker()
        self.backend.doctor()
        self.state = tempfile.mkdtemp(prefix="evalnoise-agent-state-")
        self.output = tempfile.mkdtemp(prefix="evalnoise-agent-out-")
        self.previous_state = os.environ.get("EVALNOISE_STATE_DIR")
        os.environ["EVALNOISE_STATE_DIR"] = self.state
        self.child = None

    def tearDown(self):
        if self.child is not None:
            if self.child.poll() is None:
                self.child.kill()
                self.child.wait(15)
            for stream in (self.child.stdout, self.child.stderr):
                if stream is not None:
                    stream.close()
        if self.previous_state is None:
            os.environ.pop("EVALNOISE_STATE_DIR", None)
        else:
            os.environ["EVALNOISE_STATE_DIR"] = self.previous_state

    def test_real_tool_container_emits_a_valid_observation(self):
        import base64
        from evalnoise.verification import envelope
        call = {"version": 1, "payload": {"tool_call_id": "c1", "function_name": "read_file",
                                          "arguments": {"path": "limit.txt"}}}
        encoded = base64.b64encode(json.dumps(call, sort_keys=True,
                                              separators=(",", ":")).encode()).decode()
        output = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--read-only", "--user", "65534:65534",
             "--env", "EVALNOISE_SEED=42", "--env", f"EVALNOISE_TOOL_CALL_B64={encoded}",
             TOOL_IMAGE], capture_output=True, text=True, timeout=60)
        self.assertEqual(output.returncode, 0, output.stderr)
        value, _ = envelope({"text": output.stdout}, "evalnoise_observation")
        self.assertEqual(value["payload"]["result"], {"path": "limit.txt", "content": "2"})

    def test_real_tool_container_has_no_network(self):
        output = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--read-only", "--user", "65534:65534",
             TOOL_IMAGE, "probe-network"], capture_output=True, text=True, timeout=60)
        self.assertEqual(output.returncode, 0, output.stderr)
        payload = json.loads(output.stdout)["evalnoise_observation"]["payload"]
        self.assertFalse(payload["connected"])
        self.assertIsNotNone(payload["error"])

    def test_end_to_end_agent_run_passes_independent_verification(self):
        path = execute(parse(config()), self.output, config_dir=EXPERIMENTS)
        summary = generate(path)
        trials = [json.loads(p.read_text()) for p in sorted((path / "trials").glob("*.json"))]
        self.assertEqual([t["status"] for t in trials], ["passed", "passed"])
        for trial in trials:
            self.assertEqual(trial["execution_status"], "agent_answered")
            self.assertTrue(trial["verification"]["verdict"]["passed"])
            self.assertEqual([s["tool_calls"][0]["function_name"] for s in trial["agent"]["steps"]],
                             ["list_files", "read_file", "final_answer"])
            for step in trial["agent"]["steps"][:2]:
                audit = step["extra"]["tool_trial"]["resource_audit"]
                self.assertTrue(audit["enforced_as_requested"])
                self.assertEqual(audit["observed"]["NetworkMode"], "none")
        self.assertEqual(summary["agency"]["ledger"]["actual_charged_micros"], 0)
        self.assertEqual(summary["agency"]["ledger"]["provider_requests_sent"], 0)

    def test_lazy_cassette_really_fails_the_trusted_verifier(self):
        value = config()
        value["provider"]["cassette"] = "cassettes/offline-lazy-v1.json"
        path = execute(parse(value), self.output, config_dir=EXPERIMENTS)
        generate(path)
        trials = [json.loads(p.read_text()) for p in sorted((path / "trials").glob("*.json"))]
        self.assertEqual([t["status"] for t in trials], ["verification_failed"] * 2)

    def test_sigkilled_agent_step_leaves_a_diagnosable_owned_orphan(self):
        value = config()
        # The dwell keeps a step container running long enough to observe deterministically.
        value["tasks"][0]["command"] = ["python", "/opt/tools/tool.py", "tool", "--dwell", "45"]
        value["profiles"][0]["timeout_s"] = 120
        script = CHILD.format(root=str(ROOT), state=self.state, data=value,
                              output=self.output, experiments=str(EXPERIMENTS))
        self.child = subprocess.Popen([sys.executable, "-c", script],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.time() + 90
        directory = None
        while time.time() < deadline:
            candidates = list(Path(self.output).glob("agent-offline-*"))
            if candidates and (candidates[0] / "manifest.json").exists():
                directory = candidates[0]
                break
            time.sleep(0.2)
        self.assertIsNotNone(directory, "child never wrote a manifest")
        manifest = json.loads((directory / "manifest.json").read_text())
        run_id = manifest["run_id"]
        target = None
        while time.time() < deadline and target is None:
            listing = subprocess.run(
                ["docker", "ps", "--filter", f"label=io.evalnoise.run={run_id}",
                 "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30).stdout
            target = next((n for n in listing.split() if n.rsplit("-", 1)[-1].startswith("s")), None)
            time.sleep(0.1)
        self.assertIsNotNone(target, "no agent step container was observed running")
        self.child.send_signal(signal.SIGKILL)
        self.child.wait(30)
        report = diagnose(self.backend, directory)
        owned = [c for c in report["containers"] if c["owned"]]
        self.assertTrue(owned, f"diagnosis found no owned orphan: {report['containers']}")
        self.assertEqual({c["stage"] for c in owned}, {"agent_step"})
        self.assertIn(target, {c["name"] for c in owned})
        self.assertIn(target, expected_names(manifest))
        removed = cleanup(self.backend, directory, confirm=True)
        self.assertEqual([c["name"] for c in removed["removed"]], [c["name"] for c in owned])
        self.assertEqual(removed["failures"], [])
        remaining = subprocess.run(
            ["docker", "ps", "--all", "--filter", f"label=io.evalnoise.run={run_id}",
             "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30).stdout
        self.assertEqual(remaining.split(), [])
        after = json.loads((directory / "manifest.json").read_text())
        self.assertEqual(after["status"], manifest["status"])


if __name__ == "__main__":
    unittest.main()
