import math
import json
from pathlib import Path
import random
import tempfile
import unittest

from evalnoise.blocks import analyze, bounded_mean, write
from evalnoise.compare import CompareError
from test_compare import all_pass, build_run, raw_config


class BoundTests(unittest.TestCase):
    def test_formula_and_clipping(self):
        value = bounded_mean([0.25] * 24)
        self.assertAlmostEqual(value["radius"], math.sqrt(2 * math.log(40) / 24))
        self.assertEqual(bounded_mean([0])["lower"], -1)
        self.assertEqual(bounded_mean([0])["upper"], 1)
        self.assertGreater(bounded_mean([0] * 24, .01)["radius"], value["radius"])

    def test_invalid_values(self):
        for values in ([], [True], [float("nan")], [float("inf")], [1.01], ["0"], [10**1000]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                bounded_mean(values)
        for alpha in (0, 1, -1, True, float("nan"), float("inf"), 10**1000):
            with self.subTest(alpha=alpha), self.assertRaises(ValueError):
                bounded_mean([0], alpha)

    def test_prespecified_conditional_coverage(self):
        rng = random.Random(613)
        for regime in ("null", "positive", "heterogeneous", "markov"):
            misses = 0
            for _ in range(1000):
                values, expected = [], []
                previous = 0
                for block in range(24):
                    probability = {"null": .5, "positive": .7,
                                   "heterogeneous": .2 if block % 2 else .8,
                                   "markov": .9 if previous == 1 else .1}[regime]
                    expected.append(2 * probability - 1)
                    previous = 1 if rng.random() < probability else -1
                    values.append(previous)
                result = bounded_mean(values)
                target = sum(expected) / 24
                misses += not result["lower"] <= target <= result["upper"]
            self.assertLessEqual(misses, 70, (regime, misses))


class BlockArtifactTests(unittest.TestCase):
    def fixture(self, directory, outcome=all_pass, **kwargs):
        return build_run(directory, raw_config(tasks=3, repeats=24), outcome, **kwargs)

    def test_full_fixed_suite_and_output(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.fixture(Path(temp) / "run",
                                lambda task, repeat, profile: "workload_failed"
                                if profile == "baseline" and task == "task-00" else "passed")
            result = analyze(path, "baseline", "candidate", ["memory_mb"])
            self.assertEqual(result["planned_pairs"], 72)
            self.assertEqual(len(result["blocks"]), 24)
            self.assertAlmostEqual(result["interval"]["point"], 1 / 3)
            self.assertEqual(result["blocks"][0]["seed"], 7)
            output = Path(temp) / "analysis"
            report = write(result, output)
            self.assertIn("Content-Security-Policy", Path(report).read_text())
            with self.assertRaises(FileExistsError):
                write(result, output)

    def test_no_missing_unresolved_or_unstable_study(self):
        for state in (None, "cancelled", "pending_verification"):
            with tempfile.TemporaryDirectory() as temp:
                path = self.fixture(Path(temp), lambda t, r, p: state if r == 0 else "passed")
                with self.assertRaises(CompareError):
                    analyze(path, "baseline", "candidate", ["memory_mb"])
        with tempfile.TemporaryDirectory() as temp:
            path = self.fixture(Path(temp), stable=False)
            with self.assertRaises(CompareError):
                analyze(path, "baseline", "candidate", ["memory_mb"])

    def test_incomplete_and_same_arm_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.fixture(Path(temp), status="interrupted")
            with self.assertRaises(CompareError):
                analyze(path, "baseline", "candidate", ["memory_mb"])
        with tempfile.TemporaryDirectory() as temp:
            path = self.fixture(Path(temp))
            with self.assertRaises(CompareError):
                analyze(path, "baseline", "baseline")

    def test_plan_and_trial_seed_tampering_refused(self):
        for target in ("plan", "seed"):
            with tempfile.TemporaryDirectory() as temp:
                path = self.fixture(Path(temp))
                file = path / "manifest.json" if target == "plan" else next((path / "trials").glob("*.json"))
                data = json.loads(file.read_text())
                if target == "plan":
                    data["plan"].reverse()
                else:
                    data["seed"] += 1
                file.write_text(json.dumps(data))
                with self.assertRaises(CompareError):
                    analyze(path, "baseline", "candidate", ["memory_mb"])

    def test_report_escapes_untrusted_values(self):
        with tempfile.TemporaryDirectory() as temp:
            result = {"untrusted": "<script>alert(1)</script>"}
            report = Path(write(result, Path(temp) / "out")).read_text()
            self.assertNotIn("<script>", report)
            self.assertIn("&lt;script&gt;", report)
