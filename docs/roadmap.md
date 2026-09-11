# Platform Roadmap

This is a staged engineering plan, not a claim that the entire platform exists. Each milestone has a deliverable and an acceptance gate. Avoid building a large UI around unverified measurement semantics.

## M0: Measurement Foundation - Implemented

Strict configs, deterministic plans, hardened trusted-workload execution, image/engine provenance, per-trial evidence, descriptive reporting, known-positive controls, and local tests.

Gate: real memory pressure, nonzero exit, exit-137, timeout, and cleanup tests; complete and incomplete reports; clear limits. Remote CI and additional host validation remain external checks even after local acceptance.

## M1: Task And Verifier Contract - Next

Separate task setup, workload/agent execution, and trusted verification. Define task content/version hashes, verifier version, input/output schemas, verifier resources, and artifact transfer rules. Add genuinely task-specific success checks instead of relying only on an exit contract.

Gate: verifier failure cannot become agent failure or success; malicious task output cannot modify the verifier; fixture outcomes remain deterministic; version mismatches refuse comparison.

## M2: Measurement Fidelity

Streaming raw Engine/cgroup telemetry, throttling counters, resource-enforcement probes, explicit Docker endpoint identity, host/VM metadata, same-engine run coordination, crash recovery, and optional affinity policies. Quantify sampler-on versus sampler-off overhead before enabling it by default.

Gate: sampled versus exact/available metrics clearly distinguished; unsupported fields remain null; enforcement probes catch deliberately unsupported setups; no orphan cleanup crosses run ownership; interference tests demonstrate batch/concurrency semantics.

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
