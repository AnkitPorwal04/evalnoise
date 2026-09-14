import unittest

from evalnoise.config import parse, plan
from scripts.prepare_demo import configuration
from scripts.analyze_demo import evidence
from verifiers.aggregation import accepts
from workloads.aggregation import aggregate


class DemonstrationTests(unittest.TestCase):
    def test_extract_does_not_copy_logs_host_identifiers_or_paths(self):
        source = {"manifest": {"run_id": "demo", "private_host": "private"},
                  "trials": [{"id": "one", "task": "wrong", "profile": "baseline",
                              "repeat": 0, "status": "verification_failed",
                              "execution_status": "passed", "logs": {"text": "secret"},
                              "inspection": {"state": {"ExitCode": 0, "OOMKilled": False}},
                              "verification": {"verdict": {"passed": False}}}]}
        result = evidence(source)
        self.assertNotIn("secret", str(result))
        self.assertNotIn("private", str(result))
        self.assertEqual(result["trials"][0]["exit_code"], 0)
        self.assertIs(result["trials"][0]["verifier_passed"], False)

    def test_independent_formula_matches_both_algorithms(self):
        for seed in (0, 1, 30, 31, 2026):
            for rows in (1, 7, 100):
                for mode in ("stream", "batch"):
                    with self.subTest(seed=seed, rows=rows, mode=mode):
                        self.assertTrue(accepts(aggregate(mode, seed, rows), seed, rows))
                self.assertFalse(accepts(aggregate("wrong", seed, rows), seed, rows))

    def test_wrong_shape_and_boolean_are_not_answers(self):
        for payload in ({}, None, {"rows": True, "sum_of_squares": 1},
                        {"rows": 1, "sum_of_squares": True},
                        {"rows": 1, "sum_of_squares": 1, "extra": 0}):
            self.assertFalse(accepts(payload, 0, 1))
        with self.assertRaises(ValueError):
            aggregate("unknown", 0)

    def test_study_is_fixed_and_each_contrast_changes_one_condition(self):
        experiment = parse(configuration("sha256:" + "a" * 64, "sha256:" + "b" * 64))
        self.assertEqual(sum(len(batch["trials"]) for batch in plan(experiment)), 48)
        self.assertEqual(experiment.repeats, 3)
        profiles = configuration("x", "y")["profiles"]
        for profile, changed in zip(profiles[1:], ("memory_mb", "cpus", "concurrency")):
            self.assertEqual({key for key in profile if key != "id" and
                              profile[key] != profiles[0][key]}, {changed})
