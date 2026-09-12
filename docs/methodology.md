# Experiment Methodology

## Define The Question Before Running

v0.3 telemetry does not change any outcome contract. Samples are raw engine counters, retained by comparing the daemon's own sample timestamps against the configured interval; the daemon controls the cadence, and nothing is interpolated or resampled. Reported deltas are first-to-last differences of raw counters between usable samples, not peaks, not exact quota accounting, and not evidence of a CPU reservation. A counter that does not exist on the observed cgroup version is null, never zero, so a null must not be read as "no throttling". The enforcement audit and the optional probe establish which limits the engine applied, not that the scheduler behaved correctly or that the host was otherwise idle. Sampler overhead has not been measured, so any comparison between a sampled and an unsampled profile is confounded until that record exists.

Specify the workload population, treatment settings, outcome contract, repetition unit, timing scope, and exclusions. The population remains the fixed configured scripted suite. Exit-only tasks require the expected process exit without an observed OOM event. Optional v0.2 verified tasks additionally require a valid positive verdict from a separately executed trusted verifier. Neither contract establishes LLM capability or general correctness beyond its configured checks.

## Controls

The memory calibration is a positive control: a known allocation exceeds the restrictive budget but fits the roomy budget. The identical-profile experiment is an A/A control. Run them separately on the same otherwise-idle engine. Do not combine both experiments concurrently and then interpret latency differences as isolated resource effects.

Before comparative measurement, prepare images, inspect their provenance, confirm native architecture, check VM capacity, and decide whether to perform a separate warm-up. The runner does not flush page caches or discard automatic warm-ups. Both the image cache and host page cache policy are recorded honestly as pre-existing/uncontrolled.

## Assignment And Dependence

Each repetition randomizes profile order. Each profile receives each task once, with a matched repetition seed. Task order is randomized within the repetition and shared across its profiles. Profiles run in sequential batches; tasks can overlap within a batch up to its concurrency limit.

The repeated records are dependent through tasks, host state, time, and scheduling. Concurrency changes actual contention only when sufficient tasks overlap. Profile comparisons should not pool native and emulated execution, different VM backends, or different workload versions as interchangeable observations.

## Current Estimands

Recorded pass rate: final passed records / all recorded records for a profile. Missing trials are not in this denominator and remain separately visible. Cancelled and pending-verification records are included in the recorded denominator but are not passes. Task-level outcome rows distinguish exit-only and independent-verification contracts.

Paired pass delta: average of candidate-pass minus baseline-pass over task/repetition pairs where both records exist and neither is cancelled or pending verification. Setup/runtime/unknown, artifact/verifier errors, negative verdicts, and OOM-observed records are non-passes in this descriptive quantity. It measures end-to-end recorded success, not incorrect answers alone or pure reasoning success. The first configured profile is the baseline, independent of execution order.

Successful-duration median: median workload daemon start-to-finish duration among final passes with valid timestamps. It excludes verifier duration. A profile can have a different set of successful tasks. Comparing these medians alone has survivor bias and does not establish a speedup.

Verified tasks execute only after the entire workload batch has finished and been cleaned up. Verifiers have separate resource budgets, run sequentially, and retain their own timing and intervention evidence. This avoids direct concurrent interference but does not erase cache, thermal, or time effects on subsequent batches.

## Timing And Intervention

The host deadline starts immediately before the Docker start request, excluding create and image preparation. The runner polls inspect state and explicitly kills a still-running container after observing the deadline. Inspect/start/cleanup CLI calls have their own timeouts; deadlines are not precise real-time limits. A start-command timeout is currently a runtime error, not evidence that the workload itself exceeded its execution budget. A task that finishes before a late observation is classified from its exit state, even if its duration exceeds the nominal deadline.

`lifecycle_s` is host monotonic elapsed time including creation, evidence collection, and cleanup. `container_duration_s` uses the daemon's start/finish timestamps only. Do not subtract timestamps from different clock domains. Clock discontinuity detection inside the daemon is not implemented.

## Agent Trials And Clock Domains

An agent trial is an aggregate of bounded steps, not a container observation. Its `container_name` and `container_duration_s` are null on purpose, and `measurement_kind` is `agent_step_aggregate`. Three clocks are kept separate and are never summed into a container measurement: `provider_s_total` is host-side model time, `tool_container_s_total` sums the step containers' own lifecycles, and `agent_wall_s` is the host monotonic span of the loop. Each step container additionally retains its own ordinary trial record with its own timing, enforcement audit, and classification.

In the offline slice the provider is a recorded cassette, so `provider_s_total` is a replay lookup cost of roughly zero. It is not a latency measurement and must not be compared to a real provider. A real provider would move a large, variable, network-dependent quantity into the middle of the loop, which is one of the reasons the full M3 gate stays open.

Recorded pass rates for agent tasks use the same denominators as everything else. `agent_error`, `budget_exhausted`, and `step_limit_reached` are recorded non-passes and are not incorrect answers. A budget refusal is a property of the declared ceiling, not of the model.

The in-repo fixture answer is a sum of squares up to a seed-derived limit and is arithmetically derivable once the limit is known. **No claim is made that the answer is impossible without tools, and none about model capability.** What the recorded provider establishes is narrower: the cassette key covers the whole message history, so a replay is faithful to the interaction that was recorded and cannot silently skip a tool call; a miss is a hard error rather than a generated reply; and the answer is still checked by the independent trusted verifier.

## Failure Interpretation

OOMKilled is evidence that Docker observed an OOM kill, not proof of which limit caused it or whether an agent would otherwise succeed. Exit 137 without OOMKilled is an unattributed workload failure. Expected exit plus OOMKilled is its own non-clean outcome. Timeout and cancellation are runner interventions; their resulting kill exit code must not be relabeled as OOM merely because it is 137.

## Future Statistical Gate

Before adding confidence intervals, specify a task sampling population, paired task-cluster resampling or another justified dependence model, time-block handling, missingness sensitivity, multiple-comparison policy, and minimum effective sample requirements. Validate interval behavior on simulated correlated data and known controls. An A/A result is evidence to inspect, not a guarantee of zero false positives. Predefine the primary contrast instead of selecting the largest observed difference after running many profiles.
