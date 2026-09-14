# Fixed-suite block protocol (specified before execution)

The shipped `block-memory.json` design fixes 24 blocks, three reviewed scripted
tasks, seed 1701, and 48 versus 256 MiB. CPU, timeout, and concurrency are identical.
Each existing scheduler repetition is one block: both profiles finish before the
next block, order is seeded-randomized, and both arms share task seeds/order.
No model is involved. No early stopping, discarded blocks, or automatic retries.

## Target and bound

Let X_b be the candidate minus baseline fraction of clean passes across the whole
fixed task suite in block b. X_b lies in [-1, 1]. The target is the average of
E[X_b | F_(b-1)], where F_(b-1) contains the history before block b. This is a
protocol-specific, history-conditional expected contrast, NOT a population of
coding tasks, a stationary long-run mean, or a causal treatment effect. Profile
order/carryover/host drift can be part of this target. Randomized order does not
eliminate them. The pseudo-random schedule is fixed before execution.

Conditional Hoeffding's lemma for a variable with range length two bounds its
centered conditional moment-generating function by exp(lambda^2/2). Iterating
this bound and applying Chernoff to either tail gives
P(|mean(X)-mean(E[X|past])| >= r) <= 2 exp(-n r^2/2).
Therefore r = sqrt(2 log(2/alpha)/n). Clip the endpoints to [-1, 1].
This conservative fixed-horizon martingale bound permits temporal dependence;
it does NOT justify intervals around an unconditional stationary mean. It
requires the bounded outcome definition and a fixed horizon, not normality,
exchangeable tasks, a bootstrap, or an invented minimum cluster count.

References: Hoeffding (1963), *Probability Inequalities for Sums of Bounded Random
Variables*, https://doi.org/10.1080/01621459.1963.10500830; conditional exponential
supermartingale construction in Howard et al., *Time-uniform, nonparametric,
nonasymptotic confidence sequences*, https://arxiv.org/abs/1810.08240.
This implementation uses only a fixed-horizon bound, not a confidence sequence.

## Refusals and interpretation

Require a completed, stable-engine, two-arm non-agent experiment with the exact
persisted deterministic plan and all planned task pairs resolved. Missing,
cancelled, pending, or unknown outcomes withhold analysis; no complete-case
inference. Existing compatibility/seed/contract checks apply. Infrastructure
errors remain resolved non-passes with their original labels visible in the
ordinary report. Timing stays descriptive in `compare`; no timing interval.
Analysis is opt-in via `block-analyze`; legacy comparison intervals stay withheld.

## Validation criteria fixed before the study

Exact analytic checks: finite/range validation, radius formula, confidence
monotonicity, clipping, correct task weighting, incomplete-block refusal, and
seed/plan/engine checks. Simulation: 1,000 studies with 24 blocks per regime,
seed 613; IID null, IID positive contrast, independent heterogeneous blocks,
and a Markov-dependent bounded sequence. Compare coverage to the **known
conditional target**, not to a different stationary mean. Accept no more than
70 misses per 1,000 at nominal 95%; report all regimes and any failure without
tuning. This broad alarm threshold is not proof of coverage: the mathematical
bound supplies the guarantee under its stated assumptions. Also demonstrate
that an interval at n=1 can cover the entire parameter range.

The real Docker study exercises data collection and accounting, not statistical
coverage. Keep its report even if every interval includes zero. An incomplete
run remains incomplete and is not patched into a successful study.
