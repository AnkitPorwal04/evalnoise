# Independent Verification: json-env-v1

## Purpose And Scope

A successful process can return a wrong answer. An incorrect answer is also different from a crashed verifier. EvalNoise v0.2 makes these distinctions for **small JSON-artifact tasks**. The existing scripted CPU/memory fixtures retain their explicit exit-code contracts.

This is not yet a repository/patch artifact protocol, a hidden-test service, or a general coding-agent harness. Task setup consists of reviewed, prebuilt images. No candidate filesystem is mounted into the verifier, no archive is extracted, and the host never evaluates candidate code. Each verifier must independently check the task, rather than trusting a candidate's claimed result.

## Task Configuration

Add `verifier` to a task. Its required fields are `image`, argv-array `command`, `version`, `cpus`, `memory_mb`, and `timeout_s`. Version is a nonempty ID such as `sum-v1`; it is not inferred from the image tag. Image/command and resource bounds follow the ordinary task/profile validation rules. Omit the field or use null for the legacy exit-code contract.

```json
{
  "id": "squares",
  "image": "evalnoise-workloads:local",
  "command": ["python", "/opt/evalnoise/candidate.py", "correct"],
  "verifier": {
    "image": "evalnoise-verifier:local",
    "command": ["python", "/opt/verifier/verifier.py"],
    "version": "sum-v1",
    "cpus": 1,
    "memory_mb": 128,
    "timeout_s": 5
  }
}
```

Both images resolve to immutable local image IDs before the experiment starts. Task configuration, both image IDs, verifier version/resources/argv, and protocol name form the SHA-256 task contract. Per-trial contract hashes must match the manifest; reporting also recalculates the manifest hashes. These detect inconsistent artifacts, not an attacker who rewrites every hash. There is no cross-run comparison command or signature/attestation yet.

## Candidate Output

Emit exactly one single-line record in the captured logs:

```json
{"evalnoise_artifact":{"version":1,"payload":{"sum_of_squares":5}}}
```

Other ordinary log lines are allowed, including plain-text diagnostic mentions of protocol names. The normalized inner JSON object must be at most 8192 UTF-8 bytes; it must contain exactly `version` and `payload`. Duplicate keys, non-finite numbers, missing/multiple records, extra envelope fields, invalid versions, and parser-visible truncation are rejected. Integer version 1 is not interchangeable with boolean true. Malformed JSON-shaped protocol records are errors, not ignored diagnostics. Log collection requests one extra line to detect overflow of its 2000-line retained window; prior Docker log rotation is still not detectable from this window.

The transport uses the existing bounded Docker log capture: up to 2000 retained lines and 262144 characters, subject to engine rotation. It does not guarantee delivery of output discarded by the engine. A verifier is launched only after a valid retained artifact is available; missing/corrupt output is `artifact_error`. This is not a signed, complete-output transcript. Do not put secrets in these artifacts.

## Verifier Input And Output

The fresh verifier container receives the normalized inner object as base64 in `EVALNOISE_ARTIFACT_B64` and the paired trial seed in `EVALNOISE_SEED`. The argument vector contains a fixed environment-variable name and encoded data, never a generated shell command. Docker administrators and host process inspection may see this value: it is not a confidential channel.

The trusted verifier parses the data and emits exactly one record:

```json
{"evalnoise_verdict":{"version":1,"passed":true,"reason":"Matches the independent closed-form answer"}}
```

`passed` must be a boolean and `reason` a string of at most 1000 characters. The same envelope size and strict JSON rules apply. A verdict is accepted only if the verifier also exits 0 without observed OOM, timeout, cancellation, runtime error, or cleanup failure. A nonzero exit is a verifier error, even if a positive verdict was printed. Candidate-emitted verdicts are ignored; only the separate verifier's logs are read for this decision.

The supplied `sum-v1` verifier checks a seed-derived sum of squares using a closed-form formula, while the candidate fixture uses a loop. It validates payload shape and integer type. This transparent known-answer fixture is not secret-test protection and makes no claim about LLM ability.

## Scheduling And Persistence

1. Execute all workloads in a profile batch with its configured concurrency.
2. Persist successful verifier-enabled workload results as `pending_verification`, preserving `execution_status: passed`.
3. After the entire batch ends and workload cleanup succeeds, run its verifiers sequentially using their own fixed budgets. Never overlap verification with measured workloads.
4. Persist each verifier's complete trial record under `verification/trials/`, then embed it and the accepted verdict in the parent trial and atomically replace that parent record.
5. Halt future scheduling on any cleanup failure. Cancellation/interruption leaves explicit pending, cancelled, or error outcomes, never provisional passes.

Verifier activity can still change host caches or thermal state before the next workload batch. No physical host reservation is implied. Workload `finished_at`, `lifecycle_s`, and `container_duration_s` remain workload measurements. `verification_finished_at` and the nested verifier record describe verification separately.

## Final Outcomes

- `passed`: execution succeeded and a valid trusted positive verdict was accepted.
- `verification_failed`: execution succeeded; the trusted verifier returned a valid negative verdict.
- `artifact_error`: candidate artifact was unavailable, invalid, ambiguous, or oversized.
- `verifier_error`: verifier execution, output protocol, or cleanup failed. The nested status preserves timeout/OOM/runtime detail when available.
- `pending_verification`: durable workload success that has not been verified; never counted as a pass.
- `cancelled`: cancellation prevented or interrupted verification; the prior execution result remains available.

Non-successful workload execution retains its original status and does not launch a verifier. Legacy tasks keep their exit-code meaning. Reports label both contract kinds and retain task-level outcomes instead of calling all non-passes incorrect answers.

## Authoring Checklist

- Keep trusted verifier code in a separate reviewed image and bump its version when semantics change.
- Define and validate the candidate payload schema; do not execute payload strings or deserialize unsafe formats.
- Set explicit verifier budgets large enough for the check, independent of the treatment profile.
- Include correct, incorrect, missing, malformed, oversized, and self-asserted-verdict fixtures.
- Test verifier nonzero exit, timeout, malformed verdict, cancellation, and cleanup failures.
- Use a separate protocol/design review before adding repository files, archives, network access, hidden tests, or paid model calls.
