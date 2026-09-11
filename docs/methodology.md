# Experiment Methodology

## Define The Question Before Running

Specify the workload population, treatment settings, outcome contract, repetition unit, timing scope, and exclusions. For v0.1, the population is the fixed configured scripted suite, and a clean pass means the expected process exit without an observed OOM event. This is not independently verified task correctness.

## Controls

The memory calibration is a positive control: a known allocation exceeds the restrictive budget but fits the roomy budget. The identical-profile experiment is an A/A control. Run them separately on the same otherwise-idle engine. Do not combine both experiments concurrently and then interpret latency differences as isolated resource effects.

Before comparative measurement, prepare images, inspect their provenance, confirm native architecture, check VM capacity, and decide whether to perform a separate warm-up. v0.1 does not flush page caches or discard automatic warm-ups. Both the image cache and host page cache policy are recorded honestly as pre-existing/uncontrolled.

## Assignment And Dependence

Each repetition randomizes profile order. Each profile receives each task once, with a matched repetition seed. Task order is randomized within the repetition and shared across its profiles. Profiles run in sequential batches; tasks can overlap within a batch up to its concurrency limit.

The repeated records are dependent through tasks, host state, time, and scheduling. Concurrency changes actual contention only when sufficient tasks overlap. Profile comparisons should not pool native and emulated execution, different VM backends, or different workload versions as interchangeable observations.

## Current Estimands

Recorded pass rate: clean passed records / all recorded records for a profile. Missing trials are not in this denominator and remain separately visible. Cancellation records are included in the recorded denominator but are not passes.

Paired pass delta: average of candidate-pass minus baseline-pass over task/repetition pairs where both records exist and neither is cancelled. Setup/runtime/unknown and OOM-observed records are non-passes in this descriptive quantity. It is not a pure reasoning-success estimate. The first configured profile is the baseline, independent of execution order.

Successful-duration median: median daemon start-to-finish duration among clean passes with valid timestamps. A profile can have a different set of successful tasks. Comparing these medians alone has survivor bias and does not establish a speedup.

## Timing And Intervention

The host deadline starts immediately before the Docker start request, excluding create and image preparation. The runner polls inspect state and explicitly kills a still-running container after observing the deadline. Inspect/start/cleanup CLI calls have their own timeouts; deadlines are not precise real-time limits. A start-command timeout is currently a runtime error, not evidence that the workload itself exceeded its execution budget. A task that finishes before a late observation is classified from its exit state, even if its duration exceeds the nominal deadline.

`lifecycle_s` is host monotonic elapsed time including creation, evidence collection, and cleanup. `container_duration_s` uses the daemon's start/finish timestamps only. Do not subtract timestamps from different clock domains. Clock discontinuity detection inside the daemon is not implemented.

## Failure Interpretation

OOMKilled is evidence that Docker observed an OOM kill, not proof of which limit caused it or whether an agent would otherwise succeed. Exit 137 without OOMKilled is an unattributed workload failure. Expected exit plus OOMKilled is its own non-clean outcome. Timeout and cancellation are runner interventions; their resulting kill exit code must not be relabeled as OOM merely because it is 137.

## Future Statistical Gate

Before adding confidence intervals, specify a task sampling population, paired task-cluster resampling or another justified dependence model, time-block handling, missingness sensitivity, multiple-comparison policy, and minimum effective sample requirements. Validate interval behavior on simulated correlated data and known controls. An A/A result is evidence to inspect, not a guarantee of zero false positives. Predefine the primary contrast instead of selecting the largest observed difference after running many profiles.
