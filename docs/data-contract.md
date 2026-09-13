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
- `comparison.json` / `comparison.csv` / `comparison.html`: written only by `evalnoise compare --output`, into a directory of the caller's choosing rather than into either run. A comparison reads two runs and **never writes into, mutates, or reclassifies either one**. `artifact_type` is `paired_comparison` and `schema_version` is 1.

## Comparison Artifact v1

`artifact_type` is `paired_comparison`, `schema_version` 1. A comparison reads two runs and **never writes into, mutates, or reclassifies either one**; `--output` writes into a directory the caller names.

**`uncertainty` is always `{"published": false, "status": "interval_withheld_pending_methodology_review", "detail": ...}`.** No field in this artifact is a confidence interval, standard error, p-value, or significance statement, and `bootstrap` is always `null`. See [the methodology](methodology.md#why-uncertainty-is-withheld).

`contrast` names both run IDs, directories, profile IDs, and resolved profile resources, plus `scope` (`within_run` or `cross_run`) and `selection`, which states that the arms were **user-selected after the runs existed** and that this is not a preregistration.

`compatibility` records `contract_attestation` (`per_trial_contract_sha256`, or the weaker `manifest_structural` available within one run), `declared_treatment`, every `observed_differences` entry with both values and an `equal` flag, `differing_keys`, `declared_but_identical`, and `checked_identity`. Identity mismatches never reach an artifact: they raise first.

`design` records `aggregation_unit` (always `task`), the task IDs, `repetitions_per_task`, `case_basis`, `held_fixed`, `varied`, `host_scope`, and the `dependence_note`, `seed_note`, `population_note`, `case_basis_note`, and `causal_note` statements. `host_scope` is `one run on one host`, `two separate runs against the same recorded engine identity, executed at different times`, or a statement that the arms ran on two different engines when an engine field was declared as treatment. The `dependence_note` claims a shared schedule block only for a within-run contrast.

`coverage` records `planned_pairs`, `complete_pairs`, `complete_pair_fraction`, `excluded_pairs`, `exclusions` keyed by reason, per-arm `statuses` and `categories`, `baseline_only_records`, `candidate_only_records`, and the loss fields below. A pair counts only when both arms recorded a **resolved** outcome; `pending_verification`, `cancelled`, `agent_incomplete`, and `unknown` are unresolved and are excluded, counted, and explained rather than scored.

Loss is reported **by pair identity, not by count**: `pairs_lost_in_baseline_only` and `pairs_lost_in_candidate_only` list the `{task, repeat}` cells usable in only one arm, `pairs_lost_both_arms` counts cells lost in both, `loss_counts_balanced` says whether the two arms lost equal totals, and `differential_missingness` is true whenever the *identities* differ. Two arms losing the same number of pairs on different cells is differential loss, and `loss_counts_balanced: true` with `differential_missingness: true` is the expected way to see it.

`descriptives` holds jointly-resolved counts, per-arm passes and pass rates over the identical denominator, the four transition counts, and per-arm infrastructure-error counts.

`estimates` is a list of three records named `jointly_resolved_success_task_weighted`, `jointly_resolved_infrastructure_error_task_weighted`, and `jointly_passing_duration_task_weighted`. Each carries `estimand`, `title`, `definition`, `point`, `tasks_contributing`, `tasks_total`, `cluster_detail` (per task: `planned_pairs`, `included_pairs`, and the within-task mean difference, `null` when the task contributed nothing), `planned_pairs`, `included_pairs`, `case_basis`, `bootstrap` (always `null`), `evidence`, and `evidence_detail`. `evidence` is `interval_withheld_pending_methodology_review`, `no_jointly_resolved_pairs` when nothing contributed, or `no_jointly_passing_duration_pairs` for the duration summary specifically, whose eligibility is joint passing rather than joint resolution. The duration record adds `excluded_from_duration` keyed by reason and `unit`.

`case_basis` is `complete_pair` when every planned pair contributed and `selected_case` otherwise. Under `selected_case` the figures describe a surviving subset whose selection may depend on the treatment. The planned denominator is always shown beside the included count.

An undefined summary is `null`, never `0.0`. An unknown trial status and a non-finite number are refused as errors rather than scored; unknown statuses are sorted by `repr` so a mixed-type set cannot raise an uncontrolled `TypeError`.

The **configured seed is identity and fatal on mismatch**, never a declarable treatment. Every retained trial is additionally checked against the seed its plan assigned it, and every pair is checked for seed equality across the two arms, because a repetition index is a label while the seed determines the workload.

`pairs` lists every planned task/repetition pair with both statuses, `complete`, and `exclusion`. `warnings` covers coverage, differential loss by identity, unresolved statuses, non-completed run status, joint treatments, unequal repetition counts, and A/A contrasts. `multiplicity` and `interpretation` state the limits in the artifact itself.

`comparison.csv` is one row per summary with `estimand`, `evidence`, `point`, `case_basis`, `tasks_contributing`, `tasks_total`, `included_pairs`, `planned_pairs`, and `uncertainty_published` (always `False`). Absent quantities are empty, never zero.

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
