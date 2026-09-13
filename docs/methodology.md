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

## Paired Comparison (v0.5, Descriptive Only)

`evalnoise compare` contrasts exactly two **arms**, where an arm is one profile inside one recorded run. **It publishes no uncertainty estimate of any kind.** Every summary carries `bootstrap: null` and `evidence: interval_withheld_pending_methodology_review`, and the CLI has no confidence, resample, seed, or cluster-floor option.

### Why Uncertainty Is Withheld

Three independent reasons, any one of which is sufficient:

1. **The estimand is unsettled.** Resampling tasks presumes the suite is an exchangeable sample from a task population. EvalNoise's suite is a fixed configured set of scripted tasks. If the fixed suite is the target there is no task-sampling variability to quantify; if a population is the target this suite is not a sample from it. Until that is resolved an interval has no defined meaning.
2. **The prototype's adequacy argument was wrong.** It derived a minimum cluster count by requiring `C(2k-1, k) * tail >= 1`, which counts distinct bootstrap multisets as if they were equiprobable. They are not: at k=5 their multinomial probabilities span a 120-fold range (0.00032 to 0.0384) and only 19 of the 126 fit inside a 2.5% tail by mass. The floor was deleted, not repaired.
3. **Measured coverage is poor where it mattered.** At the former floor of k=5, coverage is 0.850 against a nominal 0.95 and the A/A false-positive rate is 0.150, three times nominal. k=8 gives 0.905/0.095. Nominal behaviour is not approached until roughly k=30, and every recorded EvalNoise run has 1 to 3 tasks.

### What Is Reported

Three summaries, always all three, named for exactly what they are:

1. **`jointly_resolved_success_task_weighted`** - candidate minus baseline of a `passed` indicator, averaged within each task then averaged unweighted across tasks, over pairs where both arms recorded a resolved outcome.
2. **`jointly_resolved_infrastructure_error_task_weighted`** - the same construction over an indicator of recorded harness, engine, verifier-execution, agent-loop, or budget failure.
3. **`jointly_passing_duration_task_weighted`** - the same construction over `container_duration_s`, restricted to pairs where both arms passed and both recorded a duration.

Averaging within a task first means tasks weigh equally rather than tasks that happened to retain more repetitions weighing more. Repetitions are replicates sharing the task, host, schedule block, and seed; they are never counted as independent observations.

Each summary records `case_basis`: `complete_pair` when every planned pair contributed, `selected_case` when anything was lost. Under `selected_case` the figures describe a surviving subset whose selection may itself depend on the treatment. The planned denominator is printed beside every included count, per task and overall.

### Contrast Selection Is Not Preregistration

Both arms are named as command arguments. That is **user selection after the runs already existed**, not preregistration, and it carries none of preregistration's guarantees. The tool has no mode that scans for the largest difference, but nothing stops a person doing that by hand. Because no interval or test is published there is no multiplicity correction to apply, and none is implied.

### What Is Held Fixed

Held fixed and checked structurally: the task set, each task's contract hash, the workload and verifier image IDs, the provider/model/cassette identity, the measurement kind, and the schema version. **These are fatal on mismatch and can never be declared as a treatment.**

Varied only when declared with `--treatment`: `cpus`, `memory_mb`, `timeout_s`, `concurrency`, `cpuset_cpus`, `sample_interval_s`, and for cross-run contrasts `repeats`, the experiment-wide sampling interval, the tool version, and the engine `engine_daemon_id`, `engine_server_version`, `engine_cgroup_version`, `engine_ncpu`. Any undeclared difference is refused as a confounder. When several fields differ the result is a joint contrast of all of them.

**The configured seed is identity, not a treatment, and is fatal on mismatch.** Pairing is by `(task, repeat)`, but a repetition index is only a label: the seed is what determines the workload. Two runs with different configured seeds assign different seeds to the same repeat index, so pairing them would contrast two different workloads and report the difference as a resource effect. Beyond the configured seed, every retained trial is checked against the seed its own plan assigned it, and every pair is checked for seed equality between the two arms.

**A declared engine change withdraws the one-host claim.** If any of the engine fields is declared as treatment, the artifact says the arms did not run on one host and records that every difference between them is confounded with that change. `host_scope` states the actual scope in every artifact.

**The whole configuration hash is deliberately not compared**, because the treatment lives inside it. The check is field by field, with identity separated from treatment.

Cross-run contrasts additionally require a complete, stable engine identity in both manifests, read with the lowercase schema `docker.py` writes. A missing, incomplete, or unstable identity refuses the contrast rather than comparing nulls. `engine_identity_final.stable` must be exactly `True`; a truthy non-boolean such as the string `"no"` is refused. **An explicitly unstable engine refuses a within-run contrast too**, because trials spanning a daemon change are not attributable to one engine regardless of scope. Within a run an *absent* stability record is unknown rather than unstable, and is surfaced as a warning, because older artifacts predate the field.

Separate runs share no schedule block, no ordering, and no contemporaneous host state. The dependence note says so explicitly for a cross-run contrast rather than reusing the within-run wording, and a warning records that time-separated execution is itself uncontrolled.

### Refusals

Missing, cancelled, `pending_verification`, `agent_incomplete`, and `unknown` records are excluded from pairs and counted by reason for each arm. Loss is compared **by pair identity**, so two arms losing the same number of pairs on different task/repeat cells is reported as differential rather than balanced. An unknown trial status and a non-finite number are explicit errors, never silently scored. A summary with no contributing pair is `null`, never `0.0`.

## Remaining Statistical Gaps

Uncertainty, time-block sensitivity, multiple-comparison policy, cost/reliability frontiers, and power analysis are **all open**. The methodology has not been independently reviewed. The M4 gate is open.
