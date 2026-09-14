# Platform Roadmap

This is a staged engineering plan, not a claim that the entire platform exists. Each milestone has a deliverable and an acceptance gate. Avoid building a large UI around unverified measurement semantics.

## M0: Measurement Foundation - Implemented

Strict configs, deterministic plans, hardened trusted-workload execution, image/engine provenance, per-trial evidence, descriptive reporting, known-positive controls, and local tests.

Gate: real memory pressure, nonzero exit, exit-137, timeout, and cleanup tests; complete and incomplete reports; clear limits. Remote CI and additional host validation remain external checks even after local acceptance.

## M1: Task And Verifier Contract - JSON Artifact Slice Implemented

Independent trusted verifier containers, bounded JSON handoff, task/image/verifier hashes, explicit verifier resources/version, durable pending checkpoints, separate execution/correctness outcomes, and known-answer checks are implemented. See [the protocol](verification.md). Setup is prebuilt-image based; repository/patch transfer and a separate dynamic setup stage remain deferred, not silently implied by this milestone.

Gate for this slice: verifier failure is neither an incorrect answer nor a pass; candidate-emitted verdicts cannot replace the trusted check; fixtures exercise real separate containers; inconsistent task/verifier hashes refuse report reconstruction. Cross-run comparisons and adversarial-code isolation are not delivered.

## M2: Measurement Fidelity - Gate Passed

Implemented in v0.3: pinned Docker endpoint identity and daemon ID, streaming raw Engine/cgroup telemetry over a local Unix socket with bounded frames/samples/bytes and cancel-before-cleanup, raw throttling counters with nullable derived deltas and a cgroup availability map, a request-echo enforcement audit before every workload and verifier start, an optional reviewed in-container cgroup probe, optional validated CPU affinity, advisory per-daemon run coordination, and read-only diagnosis with ownership-checked cleanup.

Gate checklist, each item backed by a named run or test in [the validation log](validation.md):

- [x] Sampled versus available metrics distinguished; retention semantics documented in the report
- [x] Unsupported cgroup fields remain null and are listed, never zero
- [x] Enforcement audit refuses a rewritten limit before the container starts
- [x] Probe reports `inconclusive` rather than a false pass without cgroup v2; `probe-3e9cb31d8dae` confirmed a requested `0-1` mask through `cpuset.cpus.effective` with 2 online CPUs
- [x] Cleanup crosses no run ownership and holds the engine lock while it validates and removes
- [x] Telemetry is opt-in and its faults never change a workload outcome; both the stream reader and the CLI snapshot sampler close under a bound and detach rather than mutating evidence late
- [x] Hard-kill recovery: a `SIGKILL`ed run leaves a diagnosable owned orphan, `cleanup --confirm` removes exactly that container ID, and missing trials stay missing
- [x] A concurrent second run against the same engine is refused even when writing to a different output directory
- [x] Interference tests demonstrating batch/concurrency semantics under real CPU load: within-profile trials overlap, profile batches do not
- [x] Sampler-on versus sampler-off overhead contrast recorded as `sampler-overhead-e60d93977bd0`

The overhead item is recorded as a **descriptive contrast, not a measurement of overhead**. The observed medians differ by less than this harness can resolve and the sampled arm is nominally the faster of the two, so no direction or magnitude is established and no statistical claim is attached. **Sampling therefore stays opt-in and off by default**, and that default is a deliberate consequence of the evidence rather than a pending task.

Not in scope for M2: remote-endpoint streaming, auto-resume after a crash, cross-run telemetry aggregation, any CPU-percentage or peak-memory figure, and any inferential claim about sampler cost.

## M3: Agent And Benchmark Integration - Offline Slice Implemented, Gate Open

Implemented in v0.4: a bounded tool/action loop whose model turn runs in the runner process and whose every tool action runs as a fresh hardened container through the ordinary trial path; a recorded provider keyed by the full canonical request; a synthetic integer micro-USD budget ledger with admission before each provider call and each tool container; agent provenance in the task contract hash; and recovery that enumerates every bounded agent step container. See [the plan](m3-plan.md) and [the protocol](verification.md).

Harbor's current contract was verified against its primary sources before choosing. Harbor's `BaseEnvironment` is pluggable, so an EvalNoise-backed Harbor environment remains a reasonable future option; the decision here was bounded integration scope for this slice, not a judgement that Harbor is unsuitable. Harbor's ATIF field names are reused so a later exporter is a mapping rather than a rewrite. See ADR 012.

Offline slice gate, each item backed by a named test or run in [the validation log](validation.md):

- [x] Replay is deterministic across runs and a cassette miss is a hard error, never a generated reply
- [x] The cassette key covers the whole message history, so a transcript missing an observation cannot be replayed
- [x] Budget admission refuses before the provider call and before the tool container; an unaffordable plan is refused before any directory or container exists
- [x] Money is integer micro-USD end to end; a float anywhere in the ledger fails a test
- [x] Simulated reported usage is reported separately from the zero actual charge and zero requests sent
- [x] No credential environment name or value reaches a container argv, an artifact, or a report
- [x] Every agent step container keeps `--network none` and passes the enforcement audit
- [x] Provenance recorded: agent name/version, model, parameters, per-attempt retry outcomes, per-step token and simulated cost, tool image ID, cassette digest, contract hash
- [x] A report regenerates offline from the manifest snapshot without the cassette file, while a mutated snapshot is refused
- [x] Step limit, tool failure, protocol failure, budget refusal, and cancellation each produce explicit non-pass statuses and never a fabricated verdict
- [x] Agent clock domains stay separate and the parent record claims no container duration
- [x] `diagnose` and `cleanup --confirm` cover agent step containers after a real `SIGKILL`

**The full M3 gate remains OPEN.** It still requires an explicitly authorized and budgeted real-provider run, a small public reviewed task subset that is not this in-repo fixture, measured rather than simulated retry and timeout attribution, and a price table checked against an actual invoice. No live client or credential lookup exists in the measurement core, and EvalNoise reads no auth file anywhere; the experimental `codex-check` preflight delegates subscription auth to the official `codex` binary. The `tools/` and `cassettes/` fixtures are reviewed in-repo material, not a public benchmark dataset, and establish nothing about model capability. `evalnoise codex-check` does **not** close any item on this gate: it is one known-answer smoke prompt against an authorized ChatGPT subscription, not a provider benchmark, and on Codex CLI 0.153.4 it deliberately blocks before the model call because the effective tool catalog is not verifiable in advance. See [the M3 plan](m3-plan.md#evalnoise-codex-check--a-smoke-check-not-a-gate-item).

## M4: Statistical Comparison Engine - Descriptive Slice Only, Gate Open

Implemented in v0.5: `evalnoise compare`, an offline two-arm paired contrast summarised task by task, with fail-closed compatibility validation, full coverage and missingness accounting by pair identity, and offline JSON/CSV/HTML artifacts. Both within-run profile pairs and cross-run selected profiles are supported. No Docker, provider, or network access is involved.

**No uncertainty estimate is published.** Every estimand reports `bootstrap: null` and `evidence: interval_withheld_pending_methodology_review`. The CLI exposes no confidence, resample, seed, or cluster-floor option, because none of them would mean anything yet.

Descriptive slice gate, each item backed by a named test in [the validation log](validation.md):

- [x] Aggregation is task-weighted: repetitions are averaged within a task first and are never counted as independent observations
- [x] Identity mismatch in task, verifier, image, provider, model, measurement kind, or schema is fatal and not declarable as a treatment
- [x] An undeclared resource difference is refused as a confounder and names the exact flag that would declare it
- [x] Engine identity is read with the schema `docker.py` actually writes (`id`, `server_version`, `cgroup_version`, `ncpu`); a cross-run contrast fails closed on a missing, incomplete, or unstable identity, `stable` must be exactly `True` rather than truthy, and an explicitly unstable engine refuses a within-run contrast too
- [x] The configured seed is identity and fatal on mismatch, every trial seed is checked against its plan, and every pair is checked for seed equality across arms
- [x] Scope language is specific: a cross-run contrast claims no shared schedule block, and a declared engine change withdraws the one-host claim
- [x] Loss is detected by pair identity, so two arms losing the same number of pairs on different task/repeat cells is still reported as differential
- [x] Unknown trial statuses and non-finite numbers are explicit errors, never silently scored
- [x] Estimand names state what they are: jointly resolved, task-weighted, and `selected_case` rather than `complete_pair` whenever anything was lost
- [x] The planned denominator is shown beside every included count, per task and overall
- [x] An undefined summary is `null`, never `0.0`
- [x] Reports are offline, escaped, CSP-restricted, script-free, and contain no interval or confidence language

**Fixed-suite block extension implemented and executed:** `block-analyze` supplies
a fixed-horizon conditional Hoeffding bound, not a resampling interval. Its target
is the average history-conditional expected block contrast. The complete
144-trial run and four simulation regimes are recorded in
[the execution record](m4-execution-record.md). The mathematical assumptions,
pre-specified validation criteria, and exclusions are in
[the block protocol](m4-block-protocol.md). `compare` itself remains descriptive.
This addresses the fixed-suite uncertainty and temporal-dependence slice, not
stationary-mean inference or a population of tasks. Broader M4 remains open for
independent review of this new method, multiple-comparison policy, power planning,
and cost/reliability frontiers; those are not claimed complete by this run.

The prototype resampler is retained at `evalnoise/resample.py` as an unexposed research utility, marked `validated: False`, imported by nothing in the reporting path. Its own characterisation is why it is withheld: the cluster floor it used was derived by treating bootstrap multisets as equiprobable when their probabilities span a 120-fold range at k=5, and measured coverage at that floor is 0.850 against a nominal 0.95 with a 0.150 A/A false-positive rate.

## M5: Local Experiment Workbench

Read-only run browser, live progress, outcome matrix, resource timeline, side-by-side provenance differences, inspectable trace/verifier output, filtered exports, and explicit incomplete-run warnings. Add execution controls only with a reviewed local authorization boundary.

Gate: accessible desktop/mobile navigation, no hidden denominator changes, no stale cross-run state, no public Docker control endpoint, and browser regression coverage.

## M6: Distributed And Collaborative Platform

Authenticated workers, isolated execution pools, capability negotiation, artifact storage/retention, experiment ownership, access control, quotas, audit logs, and signed manifests. Define deployment and incident-response requirements before implementation.

Gate: threat review, failure-injection tests, worker-loss recovery, no duplicate accepted results, budget enforcement, and verified multi-user isolation.

## Release Discipline

Every milestone updates requirements, data contracts, tests, security notes, and a measured validation record. Work is complete when its gate passes, not when every planned feature has a stub. Keep optional modules separate from the trusted measurement core.
