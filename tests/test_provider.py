"""Recorded-provider determinism, cassette strictness, and integer budget admission."""

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from evalnoise.budget import (BudgetError, BudgetRefused, Ledger, admit_plan, cost_micros,
                              estimate_prompt_tokens)
from evalnoise.config import ConfigError, parse
from evalnoise.provider import (ProviderError, ProviderExhausted, RecordedProvider, load_cassette,
                                parse_cassette, request_sha256)

ROOT = Path(__file__).resolve().parents[1]
CASSETTE = ROOT / "experiments/cassettes/offline-sum-v1.json"


def config(**overrides):
    value = json.loads((ROOT / "experiments/agent-offline.json").read_text())
    value.update(overrides)
    return value


def cassette():
    return json.loads(CASSETTE.read_text())


def budget_and_prices(value=None):
    value = value or config()
    experiment = parse(value)
    prices = experiment.budget["prices"]
    return {k: v for k, v in experiment.budget.items() if k != "prices"}, prices


class DigestTests(unittest.TestCase):
    def test_digest_is_canonical_over_the_whole_conversation(self):
        messages = [{"role": "system", "content": "a"}, {"role": "user", "content": "b"}]
        reordered = [{"content": "a", "role": "system"}, {"content": "b", "role": "user"}]
        parameters = {"max_output_tokens": 8, "temperature": 0}
        self.assertEqual(request_sha256("m", parameters, messages),
                         request_sha256("m", dict(reversed(list(parameters.items()))), reordered))

    def test_any_transcript_difference_changes_the_digest(self):
        base = [{"role": "system", "content": "a"}]
        parameters = {"max_output_tokens": 8}
        digest = request_sha256("m", parameters, base)
        for changed in (
                ("m", parameters, base + [{"role": "tool", "content": "observed"}]),
                ("m", {"max_output_tokens": 9}, base),
                ("other", parameters, base)):
            with self.subTest(changed=changed[0]):
                self.assertNotEqual(digest, request_sha256(*changed))

    def test_dropping_an_observation_changes_the_digest(self):
        with_tool = [{"role": "system", "content": "a"},
                     {"role": "assistant", "tool_call": {"function_name": "list_files"}},
                     {"role": "tool", "content": "{\"files\":[]}"}]
        without = [with_tool[0], with_tool[1]]
        self.assertNotEqual(request_sha256("m", {}, with_tool), request_sha256("m", {}, without))


class CassetteTests(unittest.TestCase):
    def test_shipped_cassette_loads_and_snapshots_its_own_digest(self):
        provider = load_cassette("cassettes/offline-sum-v1.json", ROOT / "experiments")
        self.assertEqual(provider.snapshot["kind"], "recorded")
        self.assertEqual(provider.snapshot["entries"], 4)
        self.assertEqual(provider.snapshot["cassette_sha256"],
                         __import__("hashlib").sha256(CASSETTE.read_bytes()).hexdigest())

    def test_unknown_request_is_refused_never_generated(self):
        provider = load_cassette("cassettes/offline-sum-v1.json", ROOT / "experiments")
        with self.assertRaises(ProviderError) as caught:
            provider.complete("fixture/sum-reader-v1", {"max_output_tokens": 1},
                              [{"role": "user", "content": "never recorded"}], 3)
        self.assertIn("never generates", str(caught.exception))

    def test_recorded_attempts_are_replayed_then_the_response(self):
        document = cassette()
        entry = next(e for e in document["entries"] if e.get("attempts"))
        snapshot, entries = parse_cassette(document, "d" * 64)
        provider = RecordedProvider(snapshot, entries)
        replayed = entries[entry["request_sha256"]]
        self.assertEqual([a["outcome"] for a in replayed["failed_attempts"]], ["transient_error"])

    def test_retry_budget_exhaustion_raises_with_attempts_preserved(self):
        document = cassette()
        document["entries"] = [{"request_sha256": "a" * 64,
                                "attempts": [{"outcome": "transient_error", "error": "one"},
                                             {"outcome": "timeout", "error": "two"}]}]
        snapshot, entries = parse_cassette(document, "d" * 64)
        provider = RecordedProvider(snapshot, entries)
        provider._entries = {request_sha256("m", {}, []): entries["a" * 64]}
        with self.assertRaises(ProviderExhausted) as caught:
            provider.complete("m", {}, [], 5)
        self.assertEqual([a.outcome for a in caught.exception.attempts],
                         ["transient_error", "timeout"])

    def test_max_attempts_is_enforced_below_the_recorded_sequence(self):
        document = cassette()
        document["entries"] = [{"request_sha256": "a" * 64,
                                "attempts": [{"outcome": "transient_error", "error": "one"},
                                             {"outcome": "transient_error", "error": "two"}],
                                "response": {"stop_reason": "final_text", "text": "late",
                                             "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                                       "cache_tokens": 0}}}]
        snapshot, entries = parse_cassette(document, "d" * 64)
        provider = RecordedProvider(snapshot, entries)
        provider._entries = {request_sha256("m", {}, []): entries["a" * 64]}
        with self.assertRaises(ProviderExhausted):
            provider.complete("m", {}, [], 2)

    def test_strict_cassette_schema(self):
        for mutate, label in (
                (lambda d: d.update(schema_version=2), "schema"),
                (lambda d: d.update(protocol="other"), "protocol"),
                (lambda d: d.update(kind="live"), "kind"),
                (lambda d: d.update(entries=[]), "empty"),
                (lambda d: d["entries"].append(d["entries"][0]), "duplicate request"),
                (lambda d: d["entries"][0].update(request_sha256="XY"), "bad digest"),
                (lambda d: d["entries"][0].update(extra=1), "unknown entry key"),
                (lambda d: d["entries"][0]["response"].update(stop_reason="invented"), "stop reason"),
                (lambda d: d["entries"][0]["response"]["usage"].update(prompt_tokens=-1), "negative"),
                (lambda d: d["entries"][0]["response"]["usage"].update(cache_tokens=10 ** 9), "cache"),
                (lambda d: d["entries"][0]["response"].update(tool_call=None), "missing tool call")):
            document = cassette()
            mutate(document)
            with self.subTest(label=label), self.assertRaises(ConfigError):
                parse_cassette(document, "d" * 64)

    def test_cassette_path_limits(self):
        for reference, label in (("/etc/passwd", "absolute"),
                                 ("../secrets.json", "traversal"),
                                 ("a/b/c/d/e.json", "too deep"),
                                 ("", "empty")):
            with self.subTest(label=label), self.assertRaises(ConfigError):
                load_cassette(reference, ROOT / "experiments")

    def test_oversized_cassette_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "big.json"
            path.write_bytes(b"{" + b" " * 1_048_600 + b"}")
            with self.assertRaises(ConfigError) as caught:
                load_cassette("big.json", directory)
            self.assertIn("exceeds", str(caught.exception))

    def test_duplicate_cassette_keys_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dupe.json"
            path.write_text('{"kind": "recorded", "kind": "recorded"}')
            with self.assertRaises(ConfigError) as caught:
                load_cassette("dupe.json", directory)
            self.assertIn("Duplicate", str(caught.exception))

    def test_provider_module_declares_no_network_client(self):
        source = (ROOT / "evalnoise/provider.py").read_text()
        for forbidden in ("import socket", "http.client", "urllib", "requests", "api_key",
                          "Authorization", "os.environ"):
            self.assertNotIn(forbidden, source)


class CostTests(unittest.TestCase):
    def test_cost_uses_integer_ceiling_division(self):
        self.assertEqual(cost_micros(1, 300), 1)
        self.assertEqual(cost_micros(1_000_000, 300), 300)
        self.assertEqual(cost_micros(0, 300), 0)
        self.assertIsInstance(cost_micros(7, 13), int)

    def test_no_float_appears_in_a_ledger_record(self):
        budget, prices = budget_and_prices()
        ledger = Ledger(budget, prices)
        ledger.admit_call("fixture/sum-reader-v1", [{"role": "user", "content": "hi"}], 16, 1)
        data = ledger.data()

        def walk(value, path="ledger"):
            if isinstance(value, float):
                raise AssertionError(f"float found at {path}: {value!r}")
            if isinstance(value, dict):
                for key, item in value.items():
                    walk(item, f"{path}.{key}")
            if isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, f"{path}[{index}]")

        walk(data)
        self.assertEqual(data["actual_charged_micros"], 0)
        self.assertEqual(data["provider_requests_sent"], 0)

    def test_estimate_is_documented_as_a_heuristic_not_a_guarantee(self):
        from evalnoise.budget import SCOPE
        self.assertIn("not a provider tokenizer", SCOPE)
        self.assertIn("actual charge is zero", SCOPE)
        self.assertGreater(estimate_prompt_tokens([{"role": "user", "content": "x" * 400}]), 100)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.budget, self.prices = budget_and_prices()

    def test_worst_case_refusal_even_when_observed_cost_would_fit(self):
        budget = {**self.budget, "max_cost_micros": 1}
        ledger = Ledger(budget, self.prices)
        with self.assertRaises(BudgetRefused) as caught:
            ledger.admit_call("fixture/sum-reader-v1", [{"role": "user", "content": "x"}], 256, 1)
        self.assertIn("cost_limit", str(caught.exception))
        self.assertEqual(ledger.refusals[0]["reason"], "cost_limit")

    def test_undeclared_price_is_refused(self):
        ledger = Ledger(self.budget, self.prices)
        with self.assertRaises(BudgetError):
            ledger.admit_call("unlisted/model", [], 1, 1)

    def test_call_and_step_limits_are_separate(self):
        ledger = Ledger({**self.budget, "max_calls": 1, "max_tool_steps": 1}, self.prices)
        ledger.admit_call("fixture/sum-reader-v1", [], 16, 1)
        with self.assertRaises(BudgetRefused):
            ledger.admit_call("fixture/sum-reader-v1", [], 16, 1)
        ledger.admit_step()
        with self.assertRaises(BudgetRefused):
            ledger.admit_step()

    def test_plan_admission_refuses_an_unaffordable_plan(self):
        experiment = parse(config())
        from evalnoise.config import plan
        budget = {**self.budget, "max_calls": 2}
        with self.assertRaises(BudgetRefused) as caught:
            admit_plan(experiment, plan(experiment), budget, self.prices)
        self.assertIn("provider calls", str(caught.exception))

    def test_plan_admission_refuses_a_zero_ceiling_against_a_real_price(self):
        experiment = parse(config())
        from evalnoise.config import plan
        budget = {**self.budget, "max_cost_micros": 0}
        with self.assertRaises(BudgetRefused) as caught:
            admit_plan(experiment, plan(experiment), budget, self.prices)
        self.assertIn("ceiling", str(caught.exception))

    def test_concurrent_admission_never_exceeds_the_call_ceiling(self):
        ledger = Ledger({**self.budget, "max_calls": 20}, self.prices)
        refused, admitted = [], []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            for _ in range(5):
                try:
                    admitted.append(ledger.admit_call("fixture/sum-reader-v1", [], 16, 1))
                except BudgetRefused as error:
                    refused.append(error)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(len(admitted), 20)
        self.assertEqual(len(refused), 20)
        self.assertEqual(ledger.calls, 20)


class ReservationTests(unittest.TestCase):
    """Ceilings must hold against reservations, not against observed totals."""

    def setUp(self):
        self.budget, self.prices = budget_and_prices()
        self.model = "fixture/sum-reader-v1"

    def usage(self, prompt=10, completion=10, cache=0):
        from evalnoise.provider import Usage
        return Usage(prompt, completion, cache)

    def test_outstanding_reservation_blocks_a_second_admission(self):
        ledger = Ledger({**self.budget, "max_output_tokens": 300}, self.prices)
        ledger.admit_call(self.model, [], 256, 1)
        with self.assertRaises(BudgetRefused) as caught:
            ledger.admit_call(self.model, [], 256, 1)
        self.assertIn("output_token_limit", str(caught.exception))

    def test_settling_releases_the_unused_reservation(self):
        ledger = Ledger({**self.budget, "max_output_tokens": 300}, self.prices)
        reservation = ledger.admit_call(self.model, [], 256, 1)
        ledger.commit_call(self.model, self.usage(completion=4), 1, reservation)
        self.assertEqual(ledger.committed_output_tokens, 4)
        self.assertEqual(ledger.completion_tokens, 4)
        ledger.admit_call(self.model, [], 256, 1)

    def test_input_token_ceiling_uses_reservations_sequentially(self):
        messages = [{"role": "user", "content": "x" * 400}]
        estimate = estimate_prompt_tokens(messages)
        ledger = Ledger({**self.budget, "max_input_tokens": estimate * 2}, self.prices)
        ledger.admit_call(self.model, messages, 1, 1)
        ledger.admit_call(self.model, messages, 1, 1)
        with self.assertRaises(BudgetRefused) as caught:
            ledger.admit_call(self.model, messages, 1, 1)
        self.assertIn("input_token_limit", str(caught.exception))

    def test_concurrent_admission_never_exceeds_the_token_ceiling(self):
        ledger = Ledger({**self.budget, "max_output_tokens": 10 * 16, "max_calls": 1000,
                         "max_attempts": 10_000, "max_cost_micros": 10 ** 9}, self.prices)
        admitted, refused = [], []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            for _ in range(6):
                try:
                    admitted.append(ledger.admit_call(self.model, [], 16, 1))
                except BudgetRefused:
                    refused.append(1)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(len(admitted), 10)
        self.assertEqual(ledger.committed_output_tokens, 160)
        self.assertLessEqual(ledger.committed_output_tokens, self.budget["max_output_tokens"])

    def test_attempts_are_reserved_before_the_call_and_bound_the_failure_path(self):
        ledger = Ledger({**self.budget, "max_attempts": 5}, self.prices)
        first = ledger.admit_call(self.model, [], 16, 3)
        self.assertEqual(first["reserved_attempts"], 3)
        with self.assertRaises(BudgetRefused) as caught:
            ledger.admit_call(self.model, [], 16, 3)
        self.assertIn("attempt_limit", str(caught.exception))
        ledger.commit_failed_call(3, first)
        self.assertEqual(ledger.attempts, 3)
        self.assertEqual(ledger.committed_attempts, 3)

    def test_failed_attempts_never_escape_the_attempt_ceiling(self):
        ledger = Ledger({**self.budget, "max_attempts": 6}, self.prices)
        for _ in range(2):
            reservation = ledger.admit_call(self.model, [], 16, 3)
            ledger.commit_failed_call(3, reservation)
        self.assertEqual(ledger.attempts, 6)
        with self.assertRaises(BudgetRefused):
            ledger.admit_call(self.model, [], 16, 3)
        self.assertLessEqual(ledger.committed_attempts, 6)

    def test_a_hard_provider_error_releases_its_whole_reservation(self):
        ledger = Ledger(self.budget, self.prices)
        reservation = ledger.admit_call(self.model, [], 256, 3)
        ledger.commit_failed_call(0, reservation)
        self.assertEqual(ledger.committed_attempts, 0)
        self.assertEqual(ledger.committed_output_tokens, 0)
        self.assertEqual(ledger.committed_micros, 0)
        self.assertEqual(ledger.outstanding, 0)

    def test_concurrent_attempt_ceiling_holds_across_failures(self):
        ledger = Ledger({**self.budget, "max_attempts": 12, "max_calls": 1000,
                         "max_cost_micros": 10 ** 9, "max_output_tokens": 10 ** 7}, self.prices)
        settled = []
        barrier = threading.Barrier(6)

        def worker():
            barrier.wait()
            for _ in range(5):
                try:
                    reservation = ledger.admit_call(self.model, [], 16, 3)
                except BudgetRefused:
                    continue
                ledger.commit_failed_call(3, reservation)
                settled.append(1)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(ledger.attempts, len(settled) * 3)
        self.assertLessEqual(ledger.attempts, 12)
        self.assertLessEqual(ledger.committed_attempts, 12)

    def test_excess_recorded_usage_is_retained_with_an_explicit_error(self):
        ledger = Ledger(self.budget, self.prices)
        reservation = ledger.admit_call(self.model, [], 16, 1)
        metrics = ledger.commit_call(self.model, self.usage(prompt=10 ** 6, completion=10 ** 6),
                                     1, reservation)
        self.assertIsNotNone(metrics["accounting_error"])
        self.assertEqual(metrics["prompt_tokens"], 10 ** 6)
        self.assertEqual(ledger.prompt_tokens, 10 ** 6)
        self.assertEqual(ledger.completion_tokens, 10 ** 6)
        self.assertGreater(ledger.reported_micros, 0)
        self.assertEqual(ledger.data()["accounting_errors"][0]["call_index"], 1)
        self.assertTrue(any("completion_tokens" in text
                            for text in metrics["accounting_error"]["overruns"]))

    def test_an_accounting_failure_never_discards_observed_usage(self):
        ledger = Ledger({**self.budget, "max_output_tokens": 20}, self.prices)
        reservation = ledger.admit_call(self.model, [], 16, 1)
        metrics = ledger.commit_call(self.model, self.usage(completion=19), 1, reservation)
        self.assertEqual(ledger.completion_tokens, 19)
        self.assertEqual(metrics["completion_tokens"], 19)
        self.assertEqual(metrics["attempts"], 1)

    def test_more_attempts_than_reserved_is_an_explicit_accounting_error(self):
        ledger = Ledger(self.budget, self.prices)
        reservation = ledger.admit_call(self.model, [], 16, 1)
        metrics = ledger.commit_failed_call(4, reservation)
        self.assertEqual(ledger.attempts, 4)
        self.assertEqual(metrics["attempts"], 4)
        self.assertTrue(any("attempts" in text
                            for text in ledger.data()["accounting_errors"][0]["overruns"]))

    def test_failed_call_metrics_carry_the_settlement_verdict(self):
        ledger = Ledger(self.budget, self.prices)
        reservation = ledger.admit_call(self.model, [], 16, 1)
        metrics = ledger.commit_failed_call(4, reservation)
        recorded = ledger.data()["accounting_errors"]
        self.assertIsNotNone(metrics["accounting_error"],
                             "commit_failed_call must report the verdict it settled, not None")
        self.assertEqual(metrics["accounting_error"], recorded[-1])

    def test_failed_call_within_its_reservation_reports_no_error(self):
        ledger = Ledger(self.budget, self.prices)
        reservation = ledger.admit_call(self.model, [], 16, 3)
        metrics = ledger.commit_failed_call(2, reservation)
        self.assertIsNone(metrics["accounting_error"])
        self.assertEqual(ledger.data()["accounting_errors"], [])

    def test_ledger_publishes_no_misnamed_worst_case_field(self):
        ledger = Ledger(self.budget, self.prices)
        reservation = ledger.admit_call(self.model, [{"role": "user", "content": "hi"}], 256, 1)
        outstanding = ledger.data()
        self.assertNotIn("worst_case_admitted_micros", outstanding)
        # While a call is in flight the commitment is the reservation, which is strictly
        # above what this call will actually report.
        self.assertGreater(outstanding["committed"]["cost_micros"],
                           outstanding["simulated_reported"]["cost_micros"])
        self.assertEqual(outstanding["committed"]["calls_outstanding"], 1)
        ledger.commit_call(self.model, self.usage(prompt=5, completion=7), 1, reservation)
        settled = ledger.data()
        self.assertNotIn("worst_case_admitted_micros", settled)
        self.assertEqual(settled["committed"]["cost_micros"],
                         settled["simulated_reported"]["cost_micros"])
        self.assertEqual(settled["committed"]["calls_outstanding"], 0)
        self.assertIn("not a spend figure", settled["reconciliation"])
        self.assertIn("not a record of peak worst-case admission", settled["reconciliation"])

    def test_settled_ledger_reconciles_committed_to_observed(self):
        ledger = Ledger(self.budget, self.prices)
        for _ in range(3):
            reservation = ledger.admit_call(self.model, [{"role": "user", "content": "hi"}], 64, 2)
            ledger.commit_call(self.model, self.usage(prompt=5, completion=7), 1, reservation)
        data = ledger.data()
        self.assertEqual(data["committed"]["calls_outstanding"], 0)
        self.assertEqual(data["committed"]["input_tokens"], data["simulated_reported"]["prompt_tokens"])
        self.assertEqual(data["committed"]["output_tokens"], data["simulated_reported"]["completion_tokens"])
        self.assertEqual(data["committed"]["cost_micros"], data["simulated_reported"]["cost_micros"])
        self.assertEqual(data["committed"]["attempts"], data["provider_attempts"])
        self.assertEqual(data["accounting_errors"], [])

    def test_plan_admission_checks_attempts_and_output_tokens(self):
        from evalnoise.config import plan
        experiment = parse(config())
        for field, label in (("max_attempts", "provider attempts"),
                             ("max_output_tokens", "worst-case output tokens")):
            with self.subTest(field=field):
                budget = {**self.budget, field: 1}
                with self.assertRaises(BudgetRefused) as caught:
                    admit_plan(experiment, plan(experiment), budget, self.prices)
                self.assertIn(label, str(caught.exception))

    def test_plan_admission_reports_what_it_accepted(self):
        from evalnoise.config import plan
        experiment = parse(config())
        accepted = admit_plan(experiment, plan(experiment), self.budget, self.prices)
        self.assertEqual(accepted, {"planned_provider_calls": 8, "planned_tool_containers": 8,
                                    "planned_provider_attempts": 24,
                                    "planned_worst_case_output_tokens": 2048})


class AgentConfigTests(unittest.TestCase):
    def test_agent_requires_a_verifier(self):
        value = config()
        value["tasks"][0].pop("verifier")
        with self.assertRaises(ConfigError) as caught:
            parse(value)
        self.assertIn("grade its own answer", str(caught.exception))

    def test_agent_requires_provider_and_budget(self):
        for missing in ("provider", "budget"):
            value = config()
            value.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(ConfigError):
                parse(value)

    def test_agent_model_needs_a_declared_price(self):
        value = config()
        value["budget"]["prices"] = {"other/model": {"input_micros_per_mtok": 1,
                                                     "output_micros_per_mtok": 1}}
        with self.assertRaises(ConfigError) as caught:
            parse(value)
        self.assertIn("no price", str(caught.exception))

    def test_agent_parameters_are_a_closed_whitelist(self):
        for key, item in (("api_key", "sk-secret"), ("env", {"OPENAI_API_KEY": "x"}),
                          ("base_url", "https://example.invalid"), ("tools", [])):
            value = config()
            value["tasks"][0]["agent"]["parameters"][key] = item
            with self.subTest(key=key), self.assertRaises(ConfigError):
                parse(value)

    def test_agent_bounds_are_enforced(self):
        for key, item in (("max_steps", 0), ("max_steps", 21), ("max_attempts", 6),
                          ("model", "Bad Model"), ("name", "../escape"), ("version", 1)):
            value = config()
            value["tasks"][0]["agent"][key] = item
            with self.subTest(key=key), self.assertRaises(ConfigError):
                parse(value)

    def test_only_the_recorded_provider_is_accepted(self):
        value = config()
        value["provider"]["kind"] = "openai"
        with self.assertRaises(ConfigError) as caught:
            parse(value)
        self.assertIn("no live client exists", str(caught.exception))

    def test_planned_container_bound_covers_agent_steps(self):
        value = config()
        value["repeats"] = 100
        value["tasks"][0]["agent"]["max_steps"] = 20
        value["profiles"] = [{"id": f"p{i}", "cpus": 1, "memory_mb": 64, "timeout_s": 5}
                             for i in range(10)]
        value["budget"]["max_calls"] = 100_000_000
        with self.assertRaises(ConfigError) as caught:
            parse(value)
        self.assertIn("planned containers", str(caught.exception))

    def test_unset_agent_fields_do_not_change_v03_hashes(self):
        legacy = json.loads((ROOT / "experiments/calibration.json").read_text())
        experiment = parse(legacy)
        self.assertNotIn("agent", experiment.data()["tasks"][0])
        self.assertNotIn("provider", experiment.data())
        self.assertNotIn("budget", experiment.data())

    def test_v03_config_digests_are_byte_identical(self):
        from evalnoise.config import load
        # Recorded from the v0.3 tree at commit c8ea9ca. v0.4 adds optional fields, so an
        # unset field must never enter a hash or every v0.3 run directory becomes unreadable.
        for name, expected in (
                ("calibration", "f31e06168a6341e37a2661338dc2883bde80b28637c5666ba23350791ba83eb9"),
                ("verified", "ea5305dfb0422f1fc50fcbb1729a1c56ba05dc5215cd3e62f127b2244ff05af2"),
                ("aa-control", "c0591c8b095775dc9edfa48586e21c0c107ae24780a1f7fe2790d0d8f8d9dee3"),
                ("sampler-overhead", "3d70db5a40d2e87ff2228a51c8a80b185ddb922de9a56eee71a49ce97aa90a14"),
                ("enforcement-probe", "5b7987472fd2ec947252e45e53a5e48492ed931231005006117442204acde144")):
            with self.subTest(name=name):
                self.assertEqual(load(ROOT / f"experiments/{name}.json").digest(), expected)

    def test_v03_contract_hashes_are_byte_identical(self):
        from evalnoise.config import load
        from evalnoise.verification import contract_hash
        images = {"evalnoise-workloads:local": {"id": "sha256:" + "a" * 64},
                  "evalnoise-verifier:local": {"id": "sha256:" + "b" * 64}}
        experiment = load(ROOT / "experiments/verified.json")
        self.assertEqual([contract_hash(task, images)[:16] for task in experiment.data()["tasks"]],
                         ["b483e6280d19011a", "6c70517b1109daba", "c31fda62ef9bf28b"])


if __name__ == "__main__":
    unittest.main()
