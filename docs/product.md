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

Independent verifier isolation; agent and benchmark adapters; provider/version provenance; raw resource telemetry; host-isolation policies; statistically reviewed uncertainty estimates; experiment comparison history; live progress; reviewable share bundles; remote worker authentication; artifact retention and access control.

## Non-Goals

No claim of perfect measurement, universal resource recommendations, automatic blame attribution, secure public submission execution, replacement of all benchmark frameworks, or evaluation with private employer datasets. Avoid scope expansion that does not improve a named user journey or measurable reliability property.

## Success Measures

Measurement correctness comes first: fixture classifications, cleanup completeness, provenance completeness, artifact reconstruction, and honest missingness. Later milestones measure observer overhead, effect-estimate calibration on synthetic fixtures, adapter reproducibility, and usability. Stars, dashboard screenshots, and code volume are not substitutes for those measures.
