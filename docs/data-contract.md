# Data Contract v1

## Configuration

Required root fields: `schema_version` (integer 1), `name`, `seed`, `repeats`, `tasks`, `profiles`. Optional `sample_interval_s` defaults to zero; otherwise 2 to 60 seconds. Unknown keys and duplicate JSON object keys are rejected.

Names and IDs match `[a-z][a-z0-9_-]{0,47}`. Seed is 0 through 2^32-1. Repeats are 1 through 100. There are 1 to 100 tasks, 1 to 20 profiles, and at most 10,000 planned trials. Configuration files are at most 1 MB. Booleans are not numbers; non-finite values are rejected.

Task fields: `id`, `image`, `command`; optional `expected_exit` defaults to 0 and allows 0 through 255. Command is an argv array with 1 to 64 string entries; each entry is at most 8192 characters with no NUL. No host shell interpolation occurs. A command can still invoke a shell inside a trusted image: validation is not a substitute for reviewing code.

v0.2 adds optional `verifier` (null/absent means the original exit contract). Its required fields are `image`, `command`, `version` (ID syntax), `cpus`, `memory_mb`, and `timeout_s`; bounds are the same as their task/profile equivalents. The [verification contract](verification.md) defines `json-env-v1` and its 8192-byte payload limit.

Profile fields: `id`, `cpus` (0.1 to 64), `memory_mb` (16 to 65536, integer MiB), `timeout_s` (0.1 to 3600); optional `concurrency` (1 to 16, default 1). These syntactic maxima are not promises that the current engine can support the profile. Effective shared-host capacity remains an operator responsibility.

## Artifact Directory

`runs/<experiment-name>-<random-run-id>/` contains:

- `manifest.json`: schema/tool versions, canonical config hash, config, intended schedule, timestamps, status, image identities, selected engine/client metadata, caveats, final planned/recorded counts.
- `trials/<trial-id>.json`: task/profile/repeat/batch/seed identity, container name/ID, classification, state inspection and requested resource echo, lifecycle duration, optional samples, bounded logs, evidence errors, and cleanup errors.
- `summary.json`: profile outcomes, recorded/missing counts, pass rates, successful-duration medians, matched-pair transitions and deltas.
- `trials.csv`: flat core outcomes and timing for inspection.
- `report.html`: self-contained escaped evidence notebook.
- `verification/trials/<trial-id>-verify.json`: independent verifier execution records for verifier-enabled tasks. These are also embedded in finalized parent trials; they are not additional workload observations.

New manifests include `task_contracts`, `measurement_kind`, and `verification_schedule`. Each new trial includes `contract_sha256` and `execution_status`. Verified trials retain the available `artifact` and `verification` evidence (version, protocol, nested trial, accepted verdict or error), plus `verification_finished_at`; failures before a stage omit evidence that was never produced. Hashes are recomputed/checked when present; older M0 manifests without them remain readable.

Per-trial files are the source of truth for recorded counts when rebuilding. Missing files remain missing observations. The reporter refuses duplicate, unexpected, or task/profile/repetition-mismatched records. Artifacts are trusted local inputs, not a public untrusted-upload format; comprehensive hostile-artifact schema validation is future work.

## Trial Statuses

`passed`: exited with expected code and no observed OOM or runtime error; a verifier-enabled task additionally requires successful verifier execution and a valid positive verdict.

`pending_verification`: successful execution awaiting independent verification; not a pass and excluded from complete paired contrasts.

`verification_failed`: valid negative trusted verdict. `artifact_error`: missing/invalid candidate artifact. `verifier_error`: verifier process/protocol/cleanup failure, not an incorrect-answer verdict. See the separate `execution_status` and nested verifier record.

`workload_failed`: exited with a different code; not an inferred model reasoning failure.

`oom_killed`: OOMKilled observed with a non-expected exit.

`oom_observed_expected_exit`: OOMKilled observed despite an expected exit, such as a killed child process.

`timeout` / `cancelled`: explicit runner intervention. Raw state remains available even if another event also occurred.

`setup_error` / `runtime_error`: creation or subsequent Docker-operation failure respectively; state-level engine errors also map to runtime error.

`unknown`: evidence does not establish a supported terminal outcome.

`runner_error`: unexpected implementation or payload-processing error. Diagnostics are persisted, cleanup is attempted, and the experiment aborts rather than hiding the error.

Classification precedence: cancellation, timeout, OOM observation, state error, exited expected/non-expected, unknown. Evidence/log failures and cleanup failures are separate fields rather than quietly changing an otherwise observed workload exit.

## Run Statuses

`running`, `completed`, `cancelled`, `cleanup_failed`, `interrupted`. Completed means scheduling finished, not that every task passed. A handled Docker failure can be a completed experiment with failed trials. CLI exit zero means the experiment completed; it does not assert all workload outcomes passed.

## Evolution

Version 1 is the initial local format. Future incompatible changes require a new schema version and explicit reader handling. There is no legacy migration or remote compatibility promise yet. Do not silently reinterpret old outcome meanings. Image config IDs and registry manifest/index digests are different identities and retain separate fields.
