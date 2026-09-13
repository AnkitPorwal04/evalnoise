"""Characterisation of the EXPERIMENTAL resampler. None of this validates it.

These tests record what the prototype does, including where it fails. They are
deliberately not phrased as adequacy or coverage gates: the measured numbers
below are the evidence that the method is **not** fit to publish, which is why
`evalnoise compare` does not use it.
"""

from itertools import combinations_with_replacement
from math import factorial
import random
import statistics
import unittest

from evalnoise.resample import (MAX_RESAMPLES, MIN_RESAMPLES, ResampleError, UNVALIDATED,
                                cluster_bootstrap, interval, support_size)


class SupportTests(unittest.TestCase):
    def test_support_size_counts_distinct_multisets(self):
        self.assertEqual([support_size(k) for k in range(6)], [0, 1, 3, 10, 35, 126])

    def test_multisets_are_not_equiprobable_so_counting_them_proves_nothing(self):
        """The refutation of the deleted cluster floor, computed rather than asserted."""
        clusters = 5
        probabilities = []
        for draw in combinations_with_replacement(range(clusters), clusters):
            counts = {}
            for item in draw:
                counts[item] = counts.get(item, 0) + 1
            ways = factorial(clusters)
            for count in counts.values():
                ways //= factorial(count)
            probabilities.append(ways / clusters**clusters)
        self.assertEqual(len(probabilities), support_size(clusters))
        self.assertAlmostEqual(sum(probabilities), 1.0)
        self.assertGreater(max(probabilities) / min(probabilities), 100)
        inside, mass = 0, 0.0
        for probability in sorted(probabilities):
            if mass + probability > 0.025:
                break
            mass += probability
            inside += 1
        self.assertLess(inside, support_size(clusters))

    def test_the_invalid_adequacy_helpers_are_gone_rather_than_repaired(self):
        import evalnoise.resample as module
        for removed in ("required_clusters", "evidence_policy"):
            self.assertFalse(hasattr(module, removed), removed)

    def test_negative_or_non_integer_cluster_counts_are_refused(self):
        for bad in (-1, 1.5, True, "3"):
            with self.assertRaises(ResampleError):
                support_size(bad)


class IntervalTests(unittest.TestCase):
    def test_index_convention_matches_the_standard_library_recipe(self):
        self.assertEqual(interval(list(range(100)), 0.90), (5, 94))

    def test_an_empty_tail_is_refused_rather_than_reported_as_min_to_max(self):
        with self.assertRaises(ResampleError) as caught:
            interval(list(range(100)), 0.999)
        self.assertIn("at least 2000 resamples", str(caught.exception))
        self.assertEqual(interval(list(range(2000)), 0.999), (1, 1998))

    def test_empty_distribution_has_no_interval(self):
        with self.assertRaises(ResampleError):
            interval([], 0.95)


class BootstrapTests(unittest.TestCase):
    values = [0.1, -0.2, 0.4, 0.0, 0.3, -0.1, 0.25]

    def test_every_record_is_marked_unvalidated(self):
        record = cluster_bootstrap(self.values, seed=11, resamples=500)
        self.assertFalse(record["validated"])
        self.assertEqual(record["advisory"], UNVALIDATED)
        self.assertIn("not a confidence interval", record["advisory"])

    def test_same_seed_reproduces_every_field(self):
        self.assertEqual(cluster_bootstrap(self.values, seed=11, resamples=500),
                         cluster_bootstrap(self.values, seed=11, resamples=500))

    def test_no_clusters_gives_an_undefined_estimate_and_never_zero(self):
        record = cluster_bootstrap([], seed=1)
        self.assertIsNone(record["point"])
        self.assertIsNone(record["low"])
        self.assertIn("undefined_reason", record)

    def test_a_cluster_without_a_complete_pair_is_refused_rather_than_scored_zero(self):
        with self.assertRaises(ResampleError) as caught:
            cluster_bootstrap([0.1, None, 0.2], seed=1)
        self.assertIn("do not resample it as zero", str(caught.exception))

    def test_non_finite_cluster_values_are_refused(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ResampleError) as caught:
                cluster_bootstrap([0.1, bad, 0.2], seed=1)
            self.assertIn("finite", str(caught.exception))

    def test_all_equal_clusters_collapse_to_a_point_mass(self):
        record = cluster_bootstrap([0.0] * 8, seed=1, resamples=500)
        self.assertTrue(record["degenerate"])
        self.assertIsNone(record["low"])
        self.assertEqual(record["collapsed_at"], 0.0)

    def test_bounds_are_enforced(self):
        for kwargs in ({"resamples": MIN_RESAMPLES - 1}, {"resamples": MAX_RESAMPLES + 1},
                       {"confidence": 0.4}, {"confidence": 1.0}, {"seed": -1},
                       {"resamples": 1000.0}, {"confidence": True},
                       {"confidence": float("nan")}):
            with self.assertRaises(ResampleError):
                cluster_bootstrap(self.values, **kwargs)


def independent_clusters(rng, clusters, effect, spread=0.30):
    return [rng.gauss(effect, spread) for _ in range(clusters)]


class MeasuredInadequacyTests(unittest.TestCase):
    """Records WHY the method is withheld. These are failures, not acceptance gates."""

    simulations = 600
    resamples = 399

    def characterise(self, clusters, effect):
        rng = random.Random(20260913)
        covered = excluded = 0
        for index in range(self.simulations):
            values = independent_clusters(rng, clusters, effect)
            record = cluster_bootstrap(values, seed=index % 4096, resamples=self.resamples)
            if record["degenerate"]:
                continue
            if record["low"] <= effect <= record["high"]:
                covered += 1
            if record["low"] > 0 or record["high"] < 0:
                excluded += 1
        return covered / self.simulations, excluded / self.simulations

    def test_coverage_at_five_clusters_is_far_below_nominal(self):
        coverage, _ = self.characterise(5, 0.25)
        self.assertLess(coverage, 0.90)

    def test_aa_false_positive_rate_at_five_clusters_far_exceeds_nominal(self):
        _, excluded = self.characterise(5, 0.0)
        self.assertGreater(excluded, 0.10)

    def test_coverage_improves_with_many_more_clusters_but_is_not_thereby_validated(self):
        small, _ = self.characterise(5, 0.25)
        large, _ = self.characterise(40, 0.25)
        self.assertGreater(large, small)
        self.assertLess(large, 0.99)

    def test_the_point_estimate_itself_is_close_to_unbiased(self):
        rng = random.Random(31337)
        points = [statistics.fmean(independent_clusters(rng, 20, 0.25)) for _ in range(400)]
        self.assertAlmostEqual(statistics.fmean(points), 0.25, delta=0.02)


if __name__ == "__main__":
    unittest.main()
