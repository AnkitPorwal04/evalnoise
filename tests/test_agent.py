"""Bounded agent loop: step containers, provenance, budget refusal, and failure statuses."""

import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from evalnoise.agent import TOOLS, initial_messages, run_agent, validate_tool_call
from evalnoise.config import parse
from evalnoise.report import generate
from evalnoise.runner import execute
from test_core import FakeDocker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import tool as toolbox

EXPERIMENTS = ROOT / "experiments"
SECRET_ENVIRONMENT = {"ANTHROPIC_API_KEY": "sk-ant-fixture-should-never-appear",
                      "OPENAI_API_KEY": "sk-openai-fixture-should-never-appear"}


def config(cassette="cassettes/offline-sum-v1.json", **overrides):
    value = json.loads((EXPERIMENTS / "agent-offline.json").read_text())
    value["provider"]["cassette"] = cassette
    value.update(overrides)
    return value


class AgentDocker(FakeDocker):
    """Serves the same observations the real tool image produces, so digests match."""

    def __init__(self, tool_status=None, tool_logs=None, cleanup_error=None, **kwargs):
        super().__init__(**kwargs)
        self.tool_status = tool_status
        self.tool_logs = tool_logs
        self.cleanup_error = cleanup_error
        self.created, self.live, self.order = [], [], []
        self.seeds, self.artifacts, self.argv_env = {}, [], []

    def create(self, name, run_id=None, task=None, profile=None, *args, artifact=None,
               artifact_env="EVALNOISE_ARTIFACT_B64", **kwargs):
        super().create(name, run_id, task, profile)
        seed = args[1] if len(args) > 1 else 0
        self.seeds[name] = seed
        self.created.append(name)
        self.order.append(("create", name))
        if self.live:
            raise AssertionError(f"{name} started while {self.live} was still running")
        self.live.append(name)
        if artifact is not None:
            self.artifacts.append((artifact_env, artifact))
        self.argv_env.append((name, artifact_env, artifact))
        return name

    def remove(self, name):
        self.order.append(("remove", name))
        if name in self.live:
            self.live.remove(name)
        self.removed.append(name)

    def inspect(self, name):
        state = dict(self.state)
        if self.tool_status and self._is_step(name):
            state.update(self.tool_status)
        return {**super().inspect(name), "state": state}

    @staticmethod
    def _is_step(name):
        tail = name.rsplit("-", 1)[-1]
        return tail.startswith("s") and tail[1:].isdigit()

    def _observation(self, name):
        env, artifact = next((e, a) for n, e, a in self.argv_env if n == name and a is not None)
        call = json.loads(artifact.decode())["payload"]
        payload = {"tool_call_id": call["tool_call_id"],
                   "result": toolbox.dispatch(self.seeds[name], call["function_name"],
                                              call["arguments"])}
        return json.dumps({"evalnoise_observation": {"version": 1, "payload": payload}},
                          sort_keys=True, separators=(",", ":"))

    def logs(self, name):
        if self._is_step(name):
            return {"text": self.tool_logs if self.tool_logs is not None else self._observation(name)}
        if name.endswith("-verify"):
            env, artifact = next((e, a) for n, e, a in self.argv_env if n == name and a is not None)
            payload = json.loads(base64.b64decode(base64.b64encode(artifact)).decode())["payload"]
            n = self.seeds[name] % 20
            expected = n * (n + 1) * (2 * n + 1) // 6
            passed = payload.get("sum_of_squares") == expected
            return {"text": json.dumps({"evalnoise_verdict": {
                "version": 1, "passed": passed,
                "reason": "Matches the independent closed-form answer" if passed else "Incorrect"}})}
        return {"text": ""}


def run(value=None, backend=None, stop=None):
    experiment = parse(value or config())
    backend = backend or AgentDocker()
    directory = tempfile.mkdtemp(prefix="evalnoise-agent-")
    path = execute(experiment, directory, backend=backend, stop=stop or threading.Event(),
                   config_dir=EXPERIMENTS)
    trials = [json.loads(p.read_text()) for p in sorted((path / "trials").glob("*.json"))]
    generate(path)
    return path, trials, backend


def setUpModule():
    os.environ["EVALNOISE_STATE_DIR"] = tempfile.mkdtemp(prefix="evalnoise-agent-state-")


class ToolSchemaTests(unittest.TestCase):
    def test_unknown_tool_is_refused(self):
        with self.assertRaises(ValueError):
            validate_tool_call({"tool_call_id": "c1", "function_name": "rm", "arguments": {}})

    def test_argument_shape_is_enforced(self):
        for arguments, label in (({}, "missing"), ({"path": 1}, "wrong type"),
                                 ({"path": "a", "extra": 1}, "extra"), ({"path": ""}, "empty")):
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_tool_call({"tool_call_id": "c1", "function_name": "read_file",
                                    "arguments": arguments})

    def test_final_answer_rejects_a_boolean_masquerading_as_an_integer(self):
        with self.assertRaises(ValueError):
            validate_tool_call({"tool_call_id": "c1", "function_name": "final_answer",
                                "arguments": {"sum_of_squares": True}})

    def test_tool_table_has_no_execution_primitive(self):
        for forbidden in ("exec", "eval", "shell", "run", "write_file", "python"):
            self.assertNotIn(forbidden, TOOLS)


class LoopTests(unittest.TestCase):
    def test_loop_runs_one_container_per_tool_call_and_verifies(self):
        path, trials, backend = run()
        self.assertEqual([t["status"] for t in trials], ["passed", "passed"])
        self.assertEqual([t["execution_status"] for t in trials], ["agent_answered"] * 2)
        steps = [s for s in backend.created if AgentDocker._is_step(s)]
        self.assertEqual(len(steps), 4)
        self.assertEqual([t["agent"]["final_artifact"]["payload"]["sum_of_squares"] for t in trials],
                         [toolbox.expected_answer(42), toolbox.expected_answer(43)])

    def test_each_step_container_is_removed_before_the_next_is_created(self):
        path, trials, backend = run()
        outstanding = []
        for action, name in backend.order:
            if action == "create":
                self.assertEqual(outstanding, [], f"{name} overlapped {outstanding}")
                outstanding.append(name)
            else:
                outstanding.remove(name)

    def test_tool_calls_are_the_recorded_sequence(self):
        path, trials, backend = run()
        for trial in trials:
            names = [s["tool_calls"][0]["function_name"] for s in trial["agent"]["steps"]]
            self.assertEqual(names, ["list_files", "read_file", "final_answer"])

    def test_retry_attempts_are_recorded_as_provenance(self):
        path, trials, backend = run()
        outcomes = [[a["outcome"] for a in s["extra"]["provider_attempts"]]
                    for s in trials[0]["agent"]["steps"]]
        self.assertEqual(outcomes, [["ok"], ["transient_error", "ok"], ["ok"]])
        self.assertEqual(trials[0]["agent"]["provider_attempts"], 4)

    def test_step_records_use_harbor_atif_field_names(self):
        path, trials, backend = run()
        step = trials[0]["agent"]["steps"][0]
        self.assertEqual(set(step), {"step_id", "source", "timestamp", "model_name", "message",
                                     "tool_calls", "observation", "metrics", "llm_call_count",
                                     "extra"})
        self.assertEqual(set(step["tool_calls"][0]), {"tool_call_id", "function_name", "arguments"})
        for field in ("prompt_tokens", "completion_tokens", "cache_tokens"):
            self.assertIn(field, step["metrics"])

    def test_clock_domains_stay_separate_and_no_container_duration_is_claimed(self):
        path, trials, backend = run()
        for trial in trials:
            self.assertIsNone(trial["container_duration_s"])
            self.assertIsNone(trial["container_name"])
            self.assertEqual(trial["measurement_kind"], "agent_step_aggregate")
            agent = trial["agent"]
            for field in ("provider_s_total", "tool_container_s_total", "agent_wall_s"):
                self.assertIsInstance(agent[field], float)

    def test_replay_is_deterministic_across_runs(self):
        first = run()[1]
        second = run()[1]

        def shape(trials):
            return [(t["status"], t["agent"]["final_artifact"],
                     [(s["step_id"], s["tool_calls"], s["observation"], s["metrics"],
                       s["extra"]["request_sha256"]) for s in t["agent"]["steps"]])
                    for t in trials]

        self.assertEqual(shape(first), shape(second))

    def test_no_credential_value_reaches_a_container_or_an_artifact(self):
        with patch.dict(os.environ, SECRET_ENVIRONMENT):
            path, trials, backend = run()
        blob = json.dumps(trials) + json.dumps(json.loads((path / "manifest.json").read_text()))
        blob += (path / "report.html").read_text() if (path / "report.html").exists() else ""
        for name, value in SECRET_ENVIRONMENT.items():
            self.assertNotIn(value, blob)
            self.assertNotIn(name, blob)
        for _, environment, artifact in backend.argv_env:
            self.assertIn(environment, ("EVALNOISE_ARTIFACT_B64", "EVALNOISE_TOOL_CALL_B64"))

    def test_bounded_handoff_uses_the_tool_call_variable(self):
        path, trials, backend = run()
        variables = {environment for environment, _ in backend.artifacts}
        self.assertEqual(variables, {"EVALNOISE_TOOL_CALL_B64", "EVALNOISE_ARTIFACT_B64"})

    def test_replay_needs_no_socket(self):
        import socket
        with patch.object(socket, "socket", side_effect=AssertionError("no socket allowed")):
            path, trials, backend = run()
        self.assertEqual([t["status"] for t in trials], ["passed", "passed"])


class NonPassTests(unittest.TestCase):
    def test_a_cassette_that_skips_the_tools_fails_independent_verification(self):
        path, trials, backend = run(config(cassette="cassettes/offline-lazy-v1.json"))
        self.assertEqual([t["status"] for t in trials], ["verification_failed"] * 2)
        for trial in trials:
            self.assertEqual(len(trial["agent"]["steps"]), 1)
            self.assertFalse(trial["verification"]["verdict"]["passed"])

    def test_step_limit_without_a_final_answer_is_not_a_pass(self):
        value = config()
        value["tasks"][0]["agent"]["max_steps"] = 2
        path, trials, backend = run(value)
        for trial in trials:
            self.assertEqual(trial["status"], "step_limit_reached")
            self.assertEqual(trial["agent"]["termination"]["reason"], "step_limit")
            self.assertNotIn("verification", trial)

    def test_a_missing_recording_is_an_agent_error_not_a_pass(self):
        value = config()
        value["tasks"][0]["agent"]["parameters"]["temperature"] = 1
        path, trials, backend = run(value)
        for trial in trials:
            self.assertEqual(trial["status"], "agent_error")
            self.assertEqual(trial["agent"]["termination"]["reason"], "provider_error")
            self.assertIn("never generates", trial["agent"]["termination"]["detail"])

    def test_tool_container_failure_is_an_agent_error_and_keeps_the_raw_record(self):
        backend = AgentDocker(tool_status={"ExitCode": 3})
        path, trials, _ = run(backend=backend)
        for trial in trials:
            self.assertEqual(trial["status"], "agent_error")
            self.assertEqual(trial["agent"]["termination"]["reason"], "tool_container_failed")
            raw = trial["agent"]["steps"][0]["extra"]["tool_trial"]
            self.assertEqual(raw["status"], "workload_failed")
            self.assertEqual(raw["inspection"]["state"]["ExitCode"], 3)
            self.assertTrue(raw["resource_audit"]["enforced_as_requested"])

    def test_malformed_observation_is_an_agent_error(self):
        backend = AgentDocker(tool_logs='{"evalnoise_observation": {"version": 1}}')
        path, trials, _ = run(backend=backend)
        for trial in trials:
            self.assertEqual(trial["status"], "agent_error")
            self.assertEqual(trial["agent"]["termination"]["reason"], "observation_protocol_error")

    def test_oversized_observation_is_refused(self):
        oversized = json.dumps({"evalnoise_observation": {
            "version": 1, "payload": {"blob": "x" * 9000}}})
        backend = AgentDocker(tool_logs=oversized)
        path, trials, _ = run(backend=backend)
        for trial in trials:
            self.assertEqual(trial["status"], "agent_error")
            self.assertEqual(trial["agent"]["termination"]["reason"], "observation_protocol_error")

    def test_a_failed_call_overrunning_its_reservation_is_budget_exhausted(self):
        from evalnoise.provider import Attempt, ProviderExhausted, RecordedProvider
        # The recorded provider caps attempts at max_attempts, so an overrun cannot arise
        # from the shipped cassette. The ledger contract must still hold for any provider,
        # so the failure is injected at the boundary rather than left untested.
        attempts = tuple(Attempt(n, "transient_error", f"injected {n}") for n in range(1, 6))

        def exhausted(self, model, parameters, messages, max_attempts):
            raise ProviderExhausted("injected exhaustion beyond the reservation", attempts)

        with patch.object(RecordedProvider, "complete", exhausted):
            path, trials, backend = run()
        for trial in trials:
            self.assertEqual(trial["status"], "budget_exhausted")
            self.assertEqual(trial["agent"]["termination"]["reason"], "usage_overran_reservation")
            self.assertIn("provider_attempts_exhausted", trial["agent"]["termination"]["detail"])
            metrics = trial["agent"]["steps"][0]["metrics"]
            self.assertIsNotNone(metrics["accounting_error"])
            self.assertEqual(metrics["attempts"], 5)
            self.assertNotIn("verification", trial)
        ledger = json.loads((path / "manifest.json").read_text())["budget_ledger"]
        self.assertTrue(ledger["accounting_errors"])
        self.assertEqual(ledger["actual_charged_micros"], 0)
        self.assertEqual([s for s in backend.created if AgentDocker._is_step(s)], [])

    def test_a_failed_call_within_its_reservation_stays_an_agent_error(self):
        from evalnoise.provider import Attempt, ProviderExhausted, RecordedProvider
        attempts = (Attempt(1, "transient_error", "injected"),)

        def exhausted(self, model, parameters, messages, max_attempts):
            raise ProviderExhausted("injected exhaustion inside the reservation", attempts)

        with patch.object(RecordedProvider, "complete", exhausted):
            path, trials, backend = run()
        for trial in trials:
            self.assertEqual(trial["status"], "agent_error")
            self.assertEqual(trial["agent"]["termination"]["reason"], "provider_attempts_exhausted")
            self.assertIsNone(trial["agent"]["steps"][0]["metrics"]["accounting_error"])
        ledger = json.loads((path / "manifest.json").read_text())["budget_ledger"]
        self.assertEqual(ledger["accounting_errors"], [])
        self.assertEqual(ledger["provider_attempts"], 2)

    def test_ledger_publishes_no_worst_case_field_in_a_real_run(self):
        path, trials, backend = run()
        ledger = json.loads((path / "manifest.json").read_text())["budget_ledger"]
        self.assertNotIn("worst_case_admitted_micros", ledger)
        self.assertEqual(ledger["committed"]["cost_micros"],
                         ledger["simulated_reported"]["cost_micros"])
        self.assertEqual(ledger["committed"]["calls_outstanding"], 0)
        self.assertNotIn("worst_case_admitted_micros", (path / "report.html").read_text())

    def test_budget_refusal_before_the_provider_call_creates_no_container(self):
        value = config()
        value["budget"]["max_input_tokens"] = 10
        path, trials, backend = run(value)
        for trial in trials:
            self.assertEqual(trial["status"], "budget_exhausted")
            self.assertEqual(trial["agent"]["termination"]["reason"],
                             "budget_refused_before_provider_call")
            self.assertNotIn("verification", trial)
            self.assertIsNone(trial["agent"]["steps"][0]["metrics"])
        self.assertEqual([s for s in backend.created if AgentDocker._is_step(s)], [])

    def test_cost_ceiling_stops_the_loop_partway_without_a_verdict(self):
        value = config()
        value["budget"]["max_cost_micros"] = 2
        path, trials, backend = run(value)
        self.assertEqual([t["status"] for t in trials], ["budget_exhausted"] * 2)
        steps = [s for s in backend.created if AgentDocker._is_step(s)]
        self.assertEqual(len(steps), 1)
        for trial in trials:
            self.assertNotIn("verification", trial)

    def test_plan_admission_refuses_before_any_container_or_directory(self):
        from evalnoise.budget import BudgetRefused
        value = config()
        value["budget"]["max_calls"] = 1
        backend = AgentDocker()
        directory = tempfile.mkdtemp(prefix="evalnoise-agent-")
        with self.assertRaises(BudgetRefused):
            execute(parse(value), directory, backend=backend, stop=threading.Event(),
                    config_dir=EXPERIMENTS)
        self.assertEqual(backend.created, [])
        self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cancellation_mid_loop_leaves_cancelled_not_a_verdict(self):
        stop = threading.Event()
        backend = AgentDocker()
        original = backend.logs

        def logs(name):
            if AgentDocker._is_step(name):
                stop.set()
            return original(name)

        backend.logs = logs
        path, trials, _ = run(backend=backend, stop=stop)
        self.assertTrue(trials)
        for trial in trials:
            self.assertEqual(trial["status"], "cancelled")
            self.assertNotIn("verdict", trial.get("verification", {}))

    def test_interruption_never_fabricates_a_final_verdict(self):
        stop = threading.Event()
        stop.set()
        path, trials, backend = run(stop=stop)
        self.assertEqual(trials, [])
        manifest = json.loads((path / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "cancelled")


class ReportTests(unittest.TestCase):
    def test_report_regenerates_offline_without_the_cassette_file(self):
        path, trials, backend = run()
        moved = Path(tempfile.mkdtemp()) / "snapshot.json"
        original = EXPERIMENTS / "cassettes/offline-sum-v1.json"
        raw = original.read_bytes()
        try:
            original.unlink()
            summary = generate(path)
        finally:
            original.write_bytes(raw)
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["agency"]["provider"]["entries"], 4)

    def test_a_mutated_cassette_snapshot_is_rejected(self):
        path, trials, backend = run()
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["provider_snapshot"]["cassette_sha256"] = "0" * 64
        (path / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(ValueError) as caught:
            generate(path)
        self.assertIn("contract hashes", str(caught.exception))

    def test_a_missing_provider_snapshot_is_rejected(self):
        path, trials, backend = run()
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["provider_snapshot"] = None
        (path / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(ValueError) as caught:
            generate(path)
        self.assertIn("recorded provider snapshot", str(caught.exception))

    def test_report_states_zero_actual_charge(self):
        path, trials, backend = run()
        page = (path / "report.html").read_text()
        self.assertIn("No provider request was made", page)
        self.assertIn("simulated", page)
        summary = json.loads((path / "summary.json").read_text())
        self.assertEqual(summary["agency"]["ledger"]["actual_charged_micros"], 0)
        self.assertEqual(summary["agency"]["ledger"]["provider_requests_sent"], 0)

    def test_ledger_totals_match_the_recorded_steps(self):
        path, trials, backend = run()
        summary = json.loads((path / "summary.json").read_text())
        ledger = summary["agency"]["ledger"]
        self.assertEqual(ledger["calls_admitted"], 6)
        self.assertEqual(ledger["provider_attempts"], 8)
        self.assertEqual(ledger["tool_steps_admitted"], 4)


class RecoveryNameTests(unittest.TestCase):
    def test_expected_names_cover_every_bounded_step_plus_the_verifier(self):
        from evalnoise.recovery import expected_names, stage_image
        path, trials, backend = run()
        manifest = json.loads((path / "manifest.json").read_text())
        names = expected_names(manifest)
        run_id = manifest["run_id"]
        for repeat in ("r000", "r001"):
            for index in range(1, 5):
                key = f"evalnoise-{run_id}-{repeat}-p00-t000-s{index:02d}"
                self.assertEqual(names[key], ("read-and-sum", "agent_step"))
            self.assertIn(f"evalnoise-{run_id}-{repeat}-p00-t000-verify", names)
            self.assertNotIn(f"evalnoise-{run_id}-{repeat}-p00-t000", names)
        self.assertEqual(len(names), 10)
        self.assertEqual(stage_image(manifest, "read-and-sum", "agent_step"),
                         manifest["images"]["evalnoise-tools:local"]["id"])

    def test_an_unusable_step_bound_is_refused(self):
        from evalnoise.recovery import RecoveryError, expected_names
        path, trials, backend = run()
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["config"]["tasks"][0]["agent"]["max_steps"] = 999
        with self.assertRaises(RecoveryError):
            expected_names(manifest)


if __name__ == "__main__":
    unittest.main()
