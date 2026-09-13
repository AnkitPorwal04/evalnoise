# Product Requirements

## Vision

An evaluation engineer should be able to define execution conditions, run a reproducible comparison, inspect every outcome, and explain exactly what the experiment does and does not support. The eventual product is an evaluation-reliability workbench, not another leaderboard or a generic monitoring dashboard.

## Primary Users

- Evaluation engineers checking whether infrastructure changes alter benchmark outcomes.
- Agent-framework maintainers testing execution backends and resource policies.
- Research teams comparing runs with explicit provenance and uncertainty.

## User Journeys

1. Validate a known workload under a restrictive and a permissive memory profile. Inspect OOM state, exit status, logs, and actual requested engine configuration.
2. Repeat identical profiles to check instrumentation consistency before interpreting a treatment effect.
3. Compare a reviewed task suite across resource or concurrency settings without automatic retries hiding failures.
4. Reconstruct a report from stored evidence after Docker is unavailable or a run is interrupted.
5. Eventually compare an agent under controlled infrastructure settings with an independent verifier and provider provenance.

## v0.1 Acceptance Criteria

- Reject ambiguous, unbounded, or unsupported configuration fields before execution.
- Persist the complete intended schedule before creating trial containers.
- Record a final artifact for handled trial failures and distinguish missing trials after interruption.
- Inspect engine state before removal, and halt future batches on cleanup failure.
- Run only pre-existing, native-architecture Linux images by resolved image ID.
- Never label exit 137 alone as OOM or convert unavailable measurements to zero.
- Render an offline report without executing workload-controlled markup or scripts.
- Demonstrate real OOM, ordinary failure, exit-137, timeout, and cleanup behavior in integration tests.

## Platform Requirements, Not Yet Delivered

Agent and benchmark adapters; repository/patch artifact transfer; provider/version provenance; raw resource telemetry; host-isolation policies; **independently reviewed** uncertainty estimates; experiment comparison history; live progress; reviewable share bundles; remote worker authentication; artifact retention and access control.

Uncertainty estimates now exist as of v0.5 but are **not independently reviewed**, which is why that item stays on this list rather than moving off it. Nothing in the current implementation has been checked by a statistician outside this project.

## v0.2 JSON Verification Acceptance Criteria

- Successful candidate execution is durably pending until a separate trusted check accepts it.
- Verifier errors remain distinct from incorrect answers and never produce a pass.
- Candidate data cannot modify verifier code or supply its own accepted verdict.
- Hashes bind both image identities and task/verifier definitions; inconsistent records cannot be merged into a report.
- Verification runs outside workload batches with explicit budgets and retained lifecycle evidence.
- Existing M0 configs/reports remain usable with their original exit-contract interpretation.

## v0.5 Paired Comparison Acceptance Criteria

- Summaries aggregate by task; repetitions are averaged within a task and never counted as independent observations.
- **No uncertainty estimate is published**, and the command exposes no confidence, resample, seed, or cluster-floor option.
- Task, verifier, image, provider, model, measurement-kind, and schema identity are fatal on mismatch and can never be declared as a treatment.
- A resource or environment field that differs without an explicit declaration is refused as a confounder, naming the flag that would declare it.
- Engine identity is read with the schema the runner actually writes; a cross-run contrast fails closed on a missing, incomplete, or unstable identity, `stable` must be exactly `True`, and an explicitly unstable engine refuses a within-run contrast too.
- The configured seed is identity and fatal on mismatch; trial seeds are validated against the plan and between paired arms.
- Scope claims are specific: no shared schedule block is claimed across runs, and no one-host claim survives a declared engine change.
- Differential loss is detected by pair identity, so equal loss counts on different cells are still reported as differential.
- Unknown trial statuses and non-finite numbers are explicit errors, never silently scored.
- Summaries are labelled `selected_case` rather than `complete_pair` whenever anything was lost, and the planned denominator is always visible.
- An undefined summary is null rather than zero.
- Comparison reports are offline, escaped, script-free, and contain no interval, confidence, significance, or causal language.
- A comparison never writes into, mutates, or reclassifies either source run.

## Non-Goals

No claim of perfect measurement, universal resource recommendations, automatic blame attribution, secure public submission execution, replacement of all benchmark frameworks, or evaluation with private employer datasets. Avoid scope expansion that does not improve a named user journey or measurable reliability property.

## Success Measures

Measurement correctness comes first: fixture classifications, cleanup completeness, provenance completeness, artifact reconstruction, and honest missingness. Later milestones measure observer overhead, effect-estimate calibration on synthetic fixtures, adapter reproducibility, and usability. Stars, dashboard screenshots, and code volume are not substitutes for those measures.
