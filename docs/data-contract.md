# Data Contract v1

## Configuration

Required root fields: `schema_version` (integer 1), `name`, `seed`, `repeats`, `tasks`, `profiles`. Optional `sample_interval_s` defaults to zero; otherwise 2 to 60 seconds. Unknown keys and duplicate JSON object keys are rejected.

Names and IDs match `[a-z][a-z0-9_-]{0,47}`. Seed is 0 through 2^32-1. Repeats are 1 through 100. There are 1 to 100 tasks, 1 to 20 profiles, and at most 10,000 planned trials. Configuration files are at most 1 MB. Booleans are not numbers; non-finite values are rejected.

Task fields: `id`, `image`, `command`; optional `expected_exit` defaults to 0 and allows 0 through 255. Command is an argv array with 1 to 64 string entries; each entry is at most 8192 characters with no NUL. No host shell interpolation occurs. A command can still invoke a shell inside a trusted image: validation is not a substitute for reviewing code.

v0.2 adds optional `verifier` (null/absent means the original exit contract). Its required fields are `image`, `command`, `version` (ID syntax), `cpus`, `memory_mb`, and `timeout_s`; bounds are the same as their task/profile equivalents. The [verification contract](verification.md) defines `json-env-v1` and its 8192-byte payload limit.

v0.3 adds two optional profile fields. `cpuset_cpus` is a `--cpuset-cpus` mask given as indices or ascending ranges; it must contain at least `ceil(cpus)` entries, and an index at or beyond the engine's reported CPU count is refused. A mask restricts where a container may run; it reserves nothing, and on a constrained host the engine's CPU count is not proof that every index below it is schedulable. `sample_interval_s` overrides the experiment-wide value for that profile only, with the same bounds.

Profile fields: `id`, `cpus` (0.1 to 64), `memory_mb` (16 to 65536, integer MiB), `timeout_s` (0.1 to 3600); optional `concurrency` (1 to 16, default 1), `cpuset_cpus`, `sample_interval_s`. These syntactic maxima are not promises that the current engine can support the profile. Effective shared-host capacity remains an operator responsibility.

v0.4 adds optional root `provider` and `budget` blocks and an optional task `agent` block. `provider` requires `kind` (only `recorded`) and a `cassette` path that must be relative, free of `..`, at most four components, and resolve inside the configuration file's directory. `budget` requires integer `max_calls`, `max_attempts`, `max_tool_steps`, `max_input_tokens`, `max_output_tokens`, `max_cost_micros`, and a `prices` table of 1 to 20 models with integer `input_micros_per_mtok` and `output_micros_per_mtok`. All money is integer micro-USD; there is no float path.

`agent` requires `name`, `version`, `model`, `max_steps` (1 to 20), and `parameters`, with optional `max_attempts` (1 to 5, default 1). Model IDs match `[a-z0-9][a-z0-9._/-]{0,63}`. `parameters` is a closed whitelist of `max_output_tokens` (required) plus optional `temperature` and `top_p`; no environment, credential, URL, or free-form field is accepted. An agent task must also declare a `verifier`, because a candidate must never grade its own answer, and must have a declared price and a root provider and budget. For an agent task, `image` and `command` describe the tool container.

Because recovery enumerates every expected container name from the plan, an experiment is limited to 10,000 planned containers as well as 10,000 planned trials. An unset `agent`, `provider`, or `budget` is omitted from the configuration and contract hashes, so v0.3 digests are unchanged.

## Artifact Directory

`runs/<experiment-name>-<random-run-id>/` contains:

- `manifest.json`: schema/tool versions, canonical config hash, config, intended schedule, timestamps, status, image identities, selected engine/client metadata, caveats, final planned/recorded counts.
- `trials/<trial-id>.json`: task/profile/repeat/batch/seed identity, container name/ID, classification, state inspection and requested resource echo, lifecycle duration, optional samples, bounded logs, evidence errors, and cleanup errors.
- `summary.json`: profile outcomes, recorded/missing counts, pass rates, successful-duration medians, matched-pair transitions and deltas.
- `trials.csv`: flat core outcomes and timing for inspection.
- `report.html`: self-contained escaped evidence notebook.
- `verification/trials/<trial-id>-verify.json`: independent verifier execution records for verifier-enabled tasks. These are also embedded in finalized parent trials; they are not additional workload observations.
- `cleanup.json`: written only by `evalnoise cleanup --confirm`. It records what was examined, removed, and refused. It never alters trial statuses, verdicts, or the manifest status.
- `subscription-smoke.json`: written only by `evalnoise codex-check --confirm-subscription-use`, into its own output directory rather than an experiment run. `artifact_type` is `subscription_smoke` and `schema_version` is 1. It records `status` (`passed`, `failed`, or `blocked`), `model_called`, `codex_version`, `auth_method`, `config_fingerprint`, a zero-model `preflight` block, `event_counts` by type name, allowlisted `usage` token counts, the schema-checked `answer`, and `caveats`. `subscription_cost_usd` is always `null`, never `0`: no per-call USD price exists for a subscription and quota consumption is not measured here, so `null` reads as *unknown* while `0` would falsely claim the call was free. A `blocked` record sent no model request, so it carries `model_called: false`, no `execution` block, `null` `usage` and `answer`, and makes no claim that any quota was consumed; a regression test asserts that no spend phrasing reaches the artifact. No reasoning text, transcript, or rendered prompt is persisted; the prompt rendering is represented only by `preflight.prompt_input_sha256`. This artifact is not a trial, not a benchmark, and never mutates a `RecordedProvider`, cassette, or budget ledger.
- `probe-summary.json`: written only by `evalnoise probe`. A probe run is an ordinary owned run with its own manifest and plan, so it is diagnosable and cleanable by the normal commands.

v0.3 manifests add `environment.endpoint` (source, context name, host, scheme, and a `skip_tls_verify` boolean only — never certificate paths or TLS material), `environment.engine_identity` (daemon `ID`, name, server version, cgroup version, CPU count), `environment.telemetry` (pinned and server API versions, `telemetry_source`, and a `reason` when streaming is unavailable), `environment.telemetry_support` (cgroup fields that do not exist on this version and those whose meaning changed), `coordination` (lock path, owner token, scope statement), and `engine_identity_final` (the daemon ID re-read at the end of the run). A changed or unreachable daemon sets `stable: false` and adds a manifest warning; no trial is reclassified because of it.

v0.3 trials add `telemetry_source` (`engine_stream`, `cli_snapshot`, or `disabled`), `telemetry_meta` (received and retained counts, bytes, `truncated`, `thread_leaked`, `closed_before_cleanup`, retention policy, and nullable `derived` counter deltas), and `resource_audit` (the requested limits, the `HostConfig` echo, and any mismatches). Both telemetry paths emit `telemetry_meta`; on the `cli_snapshot` path `derived` is always `null` and accompanied by `derived_note`, because that path returns formatted display strings rather than the raw integer counters the stream exposes. `thread_leaked` true means the collector could not be stopped within its bound and was detached: the counts in that record exclude anything the detached thread observed afterwards, and the trial's own outcome is unaffected. A `telemetry_meta` of `{"close_failed": true}` means the collector's own shutdown raised, which is recorded as a `runner_error` rather than being hidden. Each telemetry sample keeps the engine record verbatim under `raw`, plus the host-monotonic `received_elapsed_s`, the daemon-clock `engine_read`/`engine_preread`, and `degraded` for the otherwise-empty record the daemon emits when its own collection fails. Samples are not peaks. A counter that does not exist on this cgroup version is `null` and listed in `telemetry_support`; it is never zero.

`summary.json` gains a `fidelity` block with the same evidence: per-profile effective sampling interval and its source, CPU affinity, telemetry sources, received and retained totals, error, truncation and leak counts, enforcement audit counts, per-trial rows, and the engine identity warning. Absent measurements are null there too.

v0.4 manifests add `provider_snapshot` (kind, protocol, model, cassette path and SHA-256, entry count, provider label, recorded seeds, and a scope note), `budget_admission` (the planned provider calls and tool containers accepted before the run started), and `budget_ledger` written at the end of the run. The ledger records declared limits, the price table, calls admitted, provider attempts, tool steps admitted, simulated reported token counts and their priced `cost_micros`, a `committed` block, `actual_charged_micros`, `provider_requests_sent`, every refusal with its reason, and every `accounting_errors` entry. In the offline slice `actual_charged_micros` and `provider_requests_sent` are always zero: no request leaves the host.

`committed` holds `input_tokens`, `output_tokens`, `cost_micros`, `attempts`, and `calls_outstanding`. It is the **live commitment** that every ceiling is enforced against: a worst-case reservation for each call still in flight plus the observed figures for each settled call. Because settling replaces a reservation with what was observed, `committed.cost_micros` equals the observed simulated cost once nothing is outstanding. **It is therefore not a peak-worst-case record and not a spend figure**, and there is deliberately no separate "worst case admitted" field, because after settlement such a field would restate the observed total under a name that claims otherwise. A prompt estimate is a local character heuristic, not a provider tokenizer, and is not a guarantee about any real provider's billed cost.

v0.4 agent trials record `measurement_kind: "agent_step_aggregate"` with `container_name` and `container_duration_s` null, because an agent trial has no parent container and must not impersonate one. Their `agent` block holds the protocol, agent name and version, model name and provider label, parameters, bounds, tool image ID, `steps`, `termination`, `final_artifact`, `provider_attempts`, and the separate clock domains `provider_s_total`, `tool_container_s_total`, and `agent_wall_s`. Each step uses Harbor's ATIF field names: `step_id`, `source`, `timestamp`, `model_name`, `message`, `tool_calls`, `observation`, `metrics`, `llm_call_count`, and `extra`. `metrics` carries `prompt_tokens`, `completion_tokens`, `cache_tokens`, `simulated_reported_micros`, and `price_source`. `extra` carries the request digest, the admission reservation, every provider attempt outcome, and the complete raw tool container trial record, which is also persisted under `agent/trials/<trial-id>-sNN.json`. Tool failures retain that raw record rather than collapsing to a message.

New manifests include `task_contracts`, `measurement_kind`, and `verification_schedule`. Each new trial includes `contract_sha256` and `execution_status`. Verified trials retain the available `artifact` and `verification` evidence (version, protocol, nested trial, accepted verdict or error), plus `verification_finished_at`; failures before a stage omit evidence that was never produced. Hashes are recomputed/checked when present; older M0 manifests without them remain readable.

Per-trial files are the source of truth for recorded counts when rebuilding. Missing files remain missing observations. The reporter refuses duplicate, unexpected, or task/profile/repetition-mismatched records. Artifacts are trusted local inputs, not a public untrusted-upload format; comprehensive hostile-artifact schema validation is future work.

## Trial Statuses

`passed`: exited with expected code and no observed OOM or runtime error; a verifier-enabled task additionally requires successful verifier execution and a valid positive verdict.

`pending_verification`: successful execution awaiting independent verification; not a pass and excluded from complete paired contrasts.

`verification_failed`: valid negative trusted verdict. `artifact_error`: missing/invalid candidate artifact. `verifier_error`: verifier process/protocol/cleanup failure, not an incorrect-answer verdict. See the separate `execution_status` and nested verifier record.

`agent_incomplete`: a durable checkpoint written before and between agent steps. It is never a final outcome of a completed run; after SIGKILL it is the honest record that the loop was interrupted, and no verdict is invented for it.

`agent_error`: the bounded loop failed. The `agent.termination.reason` distinguishes `provider_error`, `provider_attempts_exhausted`, `invalid_tool_call`, `invalid_final_artifact`, `no_tool_call`, `tool_container_failed`, `tool_cleanup_failed`, `observation_protocol_error`, and `observation_too_large`. This is not an incorrect answer and not a workload exit-code failure.

`budget_exhausted`: admission refused a provider call or a tool container. Never a pass, and the verifier is never launched.

`step_limit_reached`: the loop ended without a final answer inside `max_steps`. Not a pass and not an incorrect answer.

`workload_failed`: exited with a different code; not an inferred model reasoning failure.

`oom_killed`: OOMKilled observed with a non-expected exit.

`oom_observed_expected_exit`: OOMKilled observed despite an expected exit, such as a killed child process.

`timeout` / `cancelled`: explicit runner intervention. Raw state remains available even if another event also occurred.

`enforcement_error`: the engine's `HostConfig` echo did not match the requested limits. The container is removed without being started, so this is never a measured observation.

`setup_error` / `runtime_error`: creation or subsequent Docker-operation failure respectively; state-level engine errors also map to runtime error.

`unknown`: evidence does not establish a supported terminal outcome.

`runner_error`: unexpected implementation or payload-processing error. Diagnostics are persisted, cleanup is attempted, and the experiment aborts rather than hiding the error.

Classification precedence: cancellation, timeout, OOM observation, state error, exited expected/non-expected, unknown. Evidence/log failures and cleanup failures are separate fields rather than quietly changing an otherwise observed workload exit.

## Run Statuses

`running`, `completed`, `cancelled`, `cleanup_failed`, `interrupted`. Completed means scheduling finished, not that every task passed. A handled Docker failure can be a completed experiment with failed trials. CLI exit zero means the experiment completed; it does not assert all workload outcomes passed.

## Evolution

Version 1 is the initial local format. Future incompatible changes require a new schema version and explicit reader handling. There is no legacy migration or remote compatibility promise yet. Do not silently reinterpret old outcome meanings. Image config IDs and registry manifest/index digests are different identities and retain separate fields.
