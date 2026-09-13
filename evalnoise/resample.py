"""Experimental cluster resampling. NOT VALIDATED. NOT USED BY ANY REPORT.

**Nothing in this module may be published as an uncertainty estimate.** It is
retained as a research utility so the methodology work can continue and so the
characterisation tests have something to measure. `evalnoise compare` does not
import it, and no comparison artifact contains an interval produced by it.

Why it is withheld rather than shipped:

1. An earlier version derived a minimum cluster count from the size of the
   resampling support, `C(2k-1, k)`, by requiring `C(2k-1, k) * tail >= 1`. That
   derivation is **wrong**. It counts distinct multisets as if they were
   equiprobable, and they are not: at k=5 the 126 multisets carry multinomial
   probabilities spanning a 120-fold range (0.00032 to 0.0384), and only 19 of
   them fit inside a 2.5% tail by probability mass. The floor it produced has no
   justification and has been removed rather than rationalised.
2. Measured coverage at that former floor is bad. Over 2000 simulations of
   independent Gaussian clusters at a nominal 95% level, k=5 gives 0.850
   coverage and a 0.150 A/A false-positive rate, three times nominal. k=8 gives
   0.905/0.095. Nominal behaviour is not approached until roughly k=30.
3. The estimand itself is unresolved. EvalNoise's suite is a fixed configured
   set of tasks. Resampling tasks presumes they are exchangeable draws from a
   population, which contradicts treating the fixed suite as the target. Until
   the target of inference is settled, an interval has no defined meaning
   regardless of its coverage.

Consequently `required_clusters` and `evidence_policy` were deleted, not fixed:
both existed only to assert an adequacy that was never established.

Primary sources consulted for the resampling mechanics themselves (these remain
correct; the error was in the adequacy argument layered on top): Evan Miller,
"Adding Error Bars to Evals", arXiv:2411.00640, sections 2.2, 3.1 and 4.2; the
`scipy.stats.bootstrap` documentation for the percentile procedure and its
degenerate-distribution behaviour; and the CPython `random` documentation for
seed reproducibility and the percentile index convention.
"""

from math import ceil, comb, floor, isfinite
import random
import statistics


class ResampleError(ValueError):
    pass


MIN_RESAMPLES = 200
MAX_RESAMPLES = 20000
MIN_CONFIDENCE = 0.50
MAX_CONFIDENCE = 0.999
NULL_VALUE = 0.0

UNVALIDATED = (
    "Experimental and unvalidated. This figure is not a confidence interval, has no "
    "established coverage, and must not be published as an uncertainty estimate. See "
    "the resample module docstring for the measured coverage failures and the "
    "unresolved estimand.")


def support_size(clusters):
    """Count of distinct multisets drawable from k clusters: C(2k-1, k).

    A combinatorial fact and nothing more. It is **not** a measure of how finely
    the resampling distribution resolves a tail, because the multisets are not
    equiprobable; see the module docstring. It is kept only so a test can
    demonstrate that non-equiprobability directly.
    """
    if not isinstance(clusters, int) or isinstance(clusters, bool) or clusters < 0:
        raise ResampleError("Cluster count must be a non-negative integer")
    return 0 if clusters == 0 else comb(2 * clusters - 1, clusters)


def check_confidence(confidence):
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ResampleError("Confidence level must be a number")
    if not isfinite(confidence):
        raise ResampleError("Confidence level must be finite")
    if not MIN_CONFIDENCE <= confidence <= MAX_CONFIDENCE:
        raise ResampleError(f"Confidence level must be between {MIN_CONFIDENCE} and {MAX_CONFIDENCE}")
    return float(confidence)


def check_resamples(resamples):
    if isinstance(resamples, bool) or not isinstance(resamples, int):
        raise ResampleError("Resample count must be an integer")
    if not MIN_RESAMPLES <= resamples <= MAX_RESAMPLES:
        raise ResampleError(f"Resample count must be between {MIN_RESAMPLES} and {MAX_RESAMPLES}")
    return resamples


def tail_index(confidence, size):
    """Rank of the lower endpoint: `floor(tail * size)`, guarded against binary drift.

    `(1 - 0.90) / 2 * 100` evaluates to 4.999999999999999 in binary floating point,
    so a bare `int()` would return rank 4 and shift both endpoints outward by one
    resample. The epsilon snaps a value within 1e-9 of an integer back onto it,
    far smaller than any real gap between adjacent ranks.
    """
    return floor((1 - confidence) / 2 * size + 1e-9)


def interval(distribution, confidence):
    """Percentile endpoints using the index convention in the CPython random docs.

    For B ordered resample statistics the endpoints are at ranks `floor(tail*B)`
    and `B-1-floor(tail*B)`. With B = 100 and a 90% level that is `ordered[5]`
    and `ordered[94]`, the recipe published in the standard library docs.
    """
    confidence = check_confidence(confidence)
    ordered = sorted(distribution)
    if not ordered:
        raise ResampleError("An empty bootstrap distribution has no interval")
    index = tail_index(confidence, len(ordered))
    if index < 1:
        needed = ceil(1 / ((1 - confidence) / 2))
        raise ResampleError(
            f"{len(ordered)} resamples put no resample in a {(1 - confidence) / 2:.4g} "
            f"tail, so the endpoints would be the smallest and largest resample rather "
            f"than a {confidence:g} interval. Use at least {needed} resamples.")
    if index * 2 >= len(ordered):
        raise ResampleError("Too few resamples to separate the two interval endpoints")
    return ordered[index], ordered[len(ordered) - 1 - index]


def cluster_bootstrap(values, *, confidence=0.95, resamples=2000, seed=0, label=None):
    """Resample whole clusters with replacement. EXPERIMENTAL, see module docstring.

    The returned record always carries `validated: False` and an `advisory`
    string. Callers must not present any part of it as an uncertainty estimate.
    """
    confidence = check_confidence(confidence)
    resamples = check_resamples(resamples)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
        raise ResampleError("Bootstrap seed must be an integer in 0..2^32-1")
    values = list(values)
    if any(value is None for value in values):
        raise ResampleError(
            "A cluster with no complete pair has an undefined difference. Drop it "
            "and report the reduced cluster count; do not resample it as zero.")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ResampleError("Cluster values must be numbers")
        if not isfinite(value):
            raise ResampleError("Cluster values must be finite; NaN and infinity are refused")
    clusters = len(values)
    record = {
        "label": label, "validated": False, "advisory": UNVALIDATED,
        "method": "paired_cluster_percentile_bootstrap",
        "resampling_unit": "task_cluster",
        "clusters": clusters, "resamples": resamples,
        "confidence_level": confidence, "seed": seed,
        "support_size": support_size(clusters),
        "null_value": NULL_VALUE,
        "point": None, "low": None, "high": None,
        "standard_error": None, "spread": None,
        "degenerate": None, "collapsed_at": None,
    }
    if clusters == 0:
        record["undefined_reason"] = "no cluster had a complete pair"
        return record
    rng = random.Random(seed)
    distribution = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(clusters):
            total += values[rng.randrange(clusters)]
        distribution.append(total / clusters)
    spread = max(distribution) - min(distribution)
    degenerate = spread == 0.0
    record.update(point=statistics.fmean(values), spread=spread, degenerate=degenerate,
                  standard_error=statistics.stdev(distribution) if resamples > 1 else None)
    if degenerate:
        record["collapsed_at"] = distribution[0]
        record["degenerate_reason"] = (
            "Every resample of these clusters produced an identical value, so the "
            "resampling distribution is a point mass.")
        return record
    low, high = interval(distribution, confidence)
    record.update(low=low, high=high)
    return record
