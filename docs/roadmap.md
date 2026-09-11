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

## M3: Agent And Benchmark Integration

Start with one adapter, preferably through an existing framework such as Harbor after verifying its current contract. Record agent version, model identifier, provider parameters, token/cost observations, retry policy, task version, and verifier identity. Design credentials/network policy before adding API access.

Gate: a small public, reviewed task subset runs end to end with independent verification; no credentials in artifacts; deterministic fake-provider tests plus an explicitly budgeted real-provider test. No paid or credentialed run without authorization.

## M4: Statistical Comparison Engine

Predeclared contrasts, paired task-cluster uncertainty, time-block sensitivity, missingness analysis, multiple-comparison policy, and cost/reliability frontiers. Have the methodology independently reviewed rather than attaching generic error bars to dependent observations.

Gate: synthetic correlated data coverage tests, A/A false-positive characterization, known-effect calibration, and reports that distinguish descriptive results from inferential claims.

## M5: Local Experiment Workbench

Read-only run browser, live progress, outcome matrix, resource timeline, side-by-side provenance differences, inspectable trace/verifier output, filtered exports, and explicit incomplete-run warnings. Add execution controls only with a reviewed local authorization boundary.

Gate: accessible desktop/mobile navigation, no hidden denominator changes, no stale cross-run state, no public Docker control endpoint, and browser regression coverage.

## M6: Distributed And Collaborative Platform

Authenticated workers, isolated execution pools, capability negotiation, artifact storage/retention, experiment ownership, access control, quotas, audit logs, and signed manifests. Define deployment and incident-response requirements before implementation.

Gate: threat review, failure-injection tests, worker-loss recovery, no duplicate accepted results, budget enforcement, and verified multi-user isolation.

## Release Discipline

Every milestone updates requirements, data contracts, tests, security notes, and a measured validation record. Work is complete when its gate passes, not when every planned feature has a stub. Keep optional modules separate from the trusted measurement core.
