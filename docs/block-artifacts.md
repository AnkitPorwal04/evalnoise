# Block analysis artifact v1

`block-analyze` reads, but does not mutate, a completed run. It uses existing
report validation and verifies the deterministic schedule, both arms' seeds,
statuses, treatment declarations and stable final engine identity. Exactly two
profiles are required; agent tasks and any incomplete planned pair are refused.

`blocks.json` has `kind: fixed_suite_block_analysis`, schema version 1, run ID,
baseline/candidate, structural compatibility record, SHA-256 of the canonical
source manifest plus sorted loaded trial records, planned/included pair counts,
status counts per arm, and one record per fixed repetition. Each block records
its repetition index, workload seed, scheduled profile order, task count and
candidate-minus-baseline clean-pass fraction. Resolved infrastructure failures
remain non-passes; they are not relabelled incorrect answers.

`interval` contains the point, clipped lower/upper endpoints, untruncated radius,
number of blocks, alpha, method, target and scope. The conditional expectation
target is described in [the protocol](m4-block-protocol.md). There is no
bootstrap, p-value, significance label, timing interval, or simultaneous
multiple-comparison guarantee. Choose the horizon and alpha before outcomes.

`blocks.html` is a self-contained escaped representation with restrictive CSP,
no scripts or external assets. The output directory must not already exist.
Hashes detect content differences, not malicious coordinated edits; artifacts
are local evidence, not signed attestations. Absence of missing records does not
prove the study was prospectively planned or immune to selective reporting.
