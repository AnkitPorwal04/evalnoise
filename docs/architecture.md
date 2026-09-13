# Architecture And Decisions

## Execution Flow

```text
JSON -> strict config -> seeded plan -> engine/image preflight
     -> manifest -> sequential randomized profile batches
     -> bounded worker pool -> create -> start -> inspect/poll
     -> classify -> logs -> remove -> atomic trial JSON
     -> batch barrier -> independent verifiers -> finalized trial JSON
     -> final manifest -> offline JSON / CSV / HTML
```

An agent task replaces the single workload container with a bounded loop. Cassette freeze and plan-level budget admission run before the engine is contacted; the batch barrier, sequential verification, and halt-on-cleanup-failure rules are unchanged.

```text
cassette freeze -> plan budget admission -> (per trial)
  admit call -> recorded completion -> commit usage -> validate tool call
    -> tool container: create/start/audit/classify/logs/remove -> observation
    -> append step -> checkpoint trial JSON -> repeat within max_steps
  -> final_answer -> normalized artifact -> pending_verification
  -> batch barrier -> existing trusted verifier -> finalized trial JSON
```

Image IDs are resolved once before execution. Each task/repetition pair uses the same seed across profiles. Each profile batch has a separate worker pool and finishes before the next batch starts. Concurrency is an intentional experimental setting, not transparent acceleration.

## Modules

- `config.py`: strict parsing, ranges, configuration hashing, and schedule generation.
- `provider.py`: recorded deterministic completions keyed by the full canonical request. No HTTP client, socket use, or credential lookup.
- `subscription.py`: experimental, outside the measurement core. One known-answer `codex exec` smoke check against an authorized ChatGPT subscription, gated by a zero-model preflight that blocks unless the child's tool catalog is verified non-executing. Reads no auth file and holds no API key; the official `codex` binary is the only client.
- `budget.py`: integer micro-USD admission and a run-scoped, mutex-guarded synthetic ledger.
- `agent.py`: the bounded tool/action loop, its fixed tool table, and ATIF-shaped step records.
- `endpoint.py`: endpoint resolution and pinning, Engine API negotiation, bounded cancel-safe stats streaming, cgroup field availability.
- `coordination.py`: advisory per-daemon engine lock and orphan refusal.
- `recovery.py`: read-only diagnosis and ownership-checked cleanup.
- `probe.py`: optional preflight enforcement probe orchestration.
- `docker.py`: argv-only Docker boundary, preflight, immutable image resolution, hardened creation, inspection, optional sampling, logs, cleanup, outcome classification.
- `runner.py`: lifecycle ownership, batch scheduling, cancellation, and experiment artifacts.
- `verification.py`: bounded artifact/verdict parsing and immutable task contract hashes.
- `storage.py`: temporary-file, fsync, atomic-replace JSON persistence.
- `report.py`: identity checks, descriptive aggregation, CSV and escaped offline HTML, and the shared page style.
- `resample.py`: **experimental and unvalidated**, retained as a research utility and imported by nothing in the reporting path. Paired cluster bootstrap and percentile intervals over plain numbers. Every record it returns is marked `validated: False`.
- `compare.py`: arm loading through the existing report validation, fail-closed compatibility and engine-identity checking, pairing and pair-identity loss accounting, the three descriptive task-weighted summaries, and the offline comparison artifacts. It publishes no uncertainty and imports no resampling code.
- `cli.py`: explicit trust acknowledgement and commands. No network service or privileged web API.

## ADR 001: Local Standard-Library Runner

Accepted for v0.1. A small inspectable implementation makes lifecycle correctness testable without a database, queue, web framework, or model-provider dependency. It is not a commitment to a standard-library-only platform forever. A future control plane must justify its operational complexity.

## ADR 002: Separate Creation From Execution And Removal

Accepted. Avoid `docker run --rm`: automatic removal loses exit evidence, and timing out the CLI does not stop the container. Generate the name before creation, record state before cleanup, and attempt removal even if creation returns an error. Missing-container cleanup is idempotent; other cleanup errors stop subsequent batches.

## ADR 003: Profile Batches And Matched Repetitions

Accepted with limitations. Randomize profile order per repetition and task order per repetition; use the same task ordering across profiles in that block. This prevents a fixed universal profile order but does not remove time trends or shared-host interference. No two profile batches overlap within one process. Separate EvalNoise processes are not mutually excluded yet.

## ADR 004: Evidence-First Descriptive Reporting

Accepted. Outcomes, missingness, complete matched pairs, and successful-duration medians are directly inspectable. No p-values or population confidence intervals until the sampling and clustering assumptions are explicitly implemented and tested. Preserve raw evidence alongside summaries.

## ADR 005: Immutable Execution Identity

Accepted. Human-readable tags are allowed in input for convenience but are resolved to local image IDs before execution. Tags are never pulled during measured trials. Record repo digests when available; local images may have no repo digest. The sample Dockerfile pins its base index digest; architecture and the resulting built image still matter.

## ADR 006: Sampling Disabled By Default

Accepted, and still true in v0.3 because the overhead has not been measured. Samples are raw observations, not peak memory, exact CPU quota usage, or a CPU-percentage figure. Superseded in mechanism by ADR 009.

## ADR 009: Pinned Endpoint, Local-Socket Streaming

Accepted for v0.3. The endpoint is resolved once using the documented CLI precedence (`DOCKER_CONTEXT` over `DOCKER_HOST` over the selected context over the default socket) and then pinned onto every command. A local Unix socket is pinned by explicit `--host`, including one derived from a context, so a later `docker context use` cannot leave the CLI and the telemetry socket on different engines. `DOCKER_CONTEXT` and `DOCKER_HOST` are removed from the child environment, and the environment is snapshotted at construction so later mutation cannot redirect a run.

A remote engine is supported through an explicit `DOCKER_HOST`, which is a literal address. A remote *context* is refused for measurement: a context is a mutable name that could be repointed between any two calls, and revalidating before every operation would double the command count for no measurement benefit. A missing default socket is an error, not an endless fallback.

Streaming uses `GET /containers/{id}/stats?stream=true` on the pinned socket, at the API version the daemon itself advertises, never below its reported minimum or below the floor this code was written against. Frames, retained samples, and total bytes are bounded on received data before any retention filter. The reader owns its `HTTPResponse`; a cancelling thread only shuts the socket down, because the response's buffered state is not thread-safe. The stream is closed before container removal, so telemetry can never cause a cleanup failure, and a telemetry fault never changes a workload outcome.

## ADR 010: Advisory Coordination Keyed By Daemon ID

Accepted for v0.3, superseding the "not mutually excluded yet" note in ADR 003 for cooperating processes. The key is the daemon `ID`, not a socket path, and the lock file lives outside every output directory, so two runs writing to different `--output` paths still collide. The lock is acquired before any run directory exists and released only after the final manifest write; orphan containers are rechecked while it is held. The kernel releases it if the holder dies, so there is no stale-PID reaping.

This is cooperation between EvalNoise processes for one local user against one daemon. It is not a distributed lock and does not exclude other users, other tools, or general host load.

## ADR 011: Diagnose And Clean Up Separately, Never Resume

Accepted for v0.3. Diagnosis is read-only and fails closed on a manifest whose `run_id`, configuration, or schedule is not self-consistent. Container existence is established structurally through `docker ps` with an exact name filter; an outage or unreadable response is an error, never "absent". Cleanup holds the engine lock across validation and removal, requires name, run label, engine label, and exact-stage image identity to agree, checks every candidate before removing any, and removes by resolved full container ID so a name cannot be re-pointed between check and delete. Neither command rewrites a trial status, a verdict, or the manifest status, and there is no automatic resume.

## ADR 007: Independent Verification After The Workload Batch

Accepted for v0.2. Optional trusted verifier images receive bounded JSON data, not executable candidate files or host mounts. All workload containers in a batch are removed before sequential verification starts. A successful workload is first persisted as `pending_verification`; only a successful verifier process with a valid positive verdict can finalize it as passed. Workload and verifier budgets and evidence remain separate. This avoids direct verifier/workload overlap, not host-cache or thermal effects between batches.

## ADR 008: Stop Loading After Cleanup Failure

Accepted. Any workload cleanup failure prevents verification and subsequent batches. Any verifier cleanup failure prevents further verification and batches. Pending records remain pending rather than inventing verdicts. A contract hash includes task definitions, resolved workload/verifier image IDs, and protocol identity; reports validate consistency, but hashes are not signatures or proof against coordinated artifact editing.

## ADR 012: Bespoke Agent Loop, Harbor As An Export Vocabulary

Accepted for v0.4. Harbor's current contract was read from its primary sources before deciding: `BaseAgent.run(instruction, environment, context)` is async and drives `BaseEnvironment.exec`; environments are pluggable with many backends; `NetworkMode` already offers `no-network`, `allowlist`, and `public`; and the default model path resolves provider credentials into the agent's environment or proxies them over a bridge.

Adopting Harbor now would mean either running trials through Harbor's environment layer or writing an EvalNoise-backed `BaseEnvironment`. Both are legitimate and the second remains a reasonable future path. Neither is required to deliver a bounded tool loop with independent verification, and both carry review and test burden plus a dependency and Python-floor delta against a runner that currently declares no dependencies. **The decision is bounded integration scope for this slice, not a claim that Harbor must replace the lifecycle, that its dependencies expose a server, or that it is unsuitable.**

What is adopted for free is Harbor's ATIF field vocabulary for steps, tool calls, usage, and agent identity, so a later one-way exporter is a mapping rather than a rewrite. Nothing imports Harbor. Revisit when a real-provider budget is authorized.

## ADR 013: Host-Side Model Boundary, Container-Side Tools

Accepted for v0.4. The model turn happens in the runner process. Every tool action is a fresh hardened container created through the ordinary `trial()` path, so each step keeps the enforcement audit, telemetry, classification, ownership labels, and cleanup rules of a normal workload, and no measured container receives network access or credentials. The bounded handoff reuses the `json-env-v1` transport with a distinct `EVALNOISE_TOOL_CALL_B64` variable; the variable name is restricted to a two-value allowlist at the Docker boundary.

Because each step is a fresh container, tools are pure functions of `(seed, tool_call)` and the tool table is fixed with no expression evaluation, shell, or template execution. Stateful tools would need `docker exec` or a persistent container, which breaks the pre-start enforcement audit, and are deferred behind their own review. An agent trial therefore aggregates steps and records `container_duration_s: null`: it is not a container observation and must not be presented as one.

## ADR 014: Recorded Provider And Synthetic Integer Budget

Accepted for v0.4. A cassette entry is keyed by the SHA-256 of the full canonical request, including the entire message history with every prior observation verbatim, so a replay cannot skip a tool call or accept a different observation. A miss is a hard error; the provider never generates, interpolates, or selects a nearest entry. The cassette is frozen and hashed at preflight, its digest enters the task contract hash, and the manifest keeps a snapshot so reports regenerate offline without the file while a mutation is still refused.

All money is integer micro-USD with ceiling division, so no rounding path can admit more than the declared ceiling. Prices are declared in the configuration because a provider's price list cannot be verified offline. Admission is worst case and happens before the provider call and before the tool container; plan admission runs before the engine is touched. The ledger is run-scoped and mutex-guarded so profile concurrency cannot over-admit. Ceilings are enforced against a committed figure that settles back to the observed one, so the ledger publishes no field claiming to be a retained worst-case total: after settlement such a figure would simply restate the observation under a misleading name. Every settlement path returns its own verdict, and an accounting overrun outranks whatever else ended the call, so the ledger, the step trace, and the trial status always agree. Simulated reported usage is recorded separately from `actual_charged_micros`, which is zero in this slice because nothing is sent.

## ADR 015: Descriptive Comparison, Uncertainty Withheld

Accepted for v0.5, superseding the interval-publishing design that preceded it. `evalnoise compare` aggregates **by task**: repetitions of a task under one treatment share the task, image, host, schedule block, and seed, so they are averaged within the task before anything is combined, and tasks weigh equally regardless of how many repetitions each retained.

**No uncertainty estimate is published.** An earlier design derived a minimum cluster count from the resampling support by requiring `C(2k-1, k) * tail >= 1`. That is invalid: it treats bootstrap multisets as equiprobable, and at k=5 their probabilities span a 120-fold range with only 19 of 126 fitting a 2.5% tail by mass. Measured coverage at that floor is 0.850 against a nominal 0.95, with a 0.150 A/A false-positive rate. Separately, the estimand is unsettled, because resampling tasks presumes an exchangeable draw from a population that a fixed configured suite is not. The floor and the interval were removed rather than rationalised, and the CLI exposes no confidence, resample, seed, or cluster-floor option so the affordance cannot be mistaken for a capability.

The prototype survives at `evalnoise/resample.py` as an unexposed research utility. It is marked `validated: False`, carries an advisory string on every record, and is imported by nothing in the reporting path; a test asserts that `compare.py` does not reference it.

Compatibility **fails closed on identity and open only on declaration**. Task, verifier, image, provider, model, measurement-kind, and schema identity are fatal on mismatch and not declarable. Resource and environment fields differ only when named with `--treatment`. The whole configuration hash is deliberately not compared, because the treatment lives inside it.

Engine identity is read with the lowercase schema `docker.py` actually writes: `id`, `server_version`, `cgroup_version`, `ncpu`. An earlier version read Docker-API casing, so every field resolved to `None` and two different daemons compared equal as `None == None`, silently disabling the check. A cross-run contrast now fails closed on a missing, incomplete, or unstable engine identity, including the end-of-run `engine_identity_final.stable` flag.

The **configured seed is identity and fatal on mismatch**, never declarable. Pairing is by `(task, repeat)`, but a repetition index is only a label and the seed determines the workload, so trial seeds are validated against the plan and between paired arms. `engine_identity_final.stable` must be exactly `True` rather than truthy, and an explicitly unstable engine refuses a within-run contrast as well as a cross-run one. Scope claims are specific: separate runs are recorded as sharing no schedule block, and a declared engine change withdraws the one-host claim from both `host_scope` and the interpretation.

Loss is compared **by pair identity**, not by count, because two arms can lose the same number of pairs on entirely different task/repeat cells. Unknown trial statuses and non-finite numbers are explicit errors rather than silent scoring decisions. Summaries record `selected_case` rather than `complete_pair` whenever anything was lost, and the planned denominator appears beside every included count.

Cross-run contrasts additionally require persisted per-task contract hashes in both manifests and a `contract_sha256` on every used trial. Within one run identity is structural, so older artifacts remain comparable with the weaker attestation recorded in the artifact.

## Reliability Boundaries

Atomic replacement protects individual JSON files from partial writes during normal failures. It is not a transaction across files, directory-fsync power-loss guarantee, or cryptographic attestation. SIGINT/SIGTERM request cooperative cancellation; SIGKILL, a machine crash, or an unreachable engine can leave containers and missing trial artifacts. Reports preserve missingness rather than synthesizing success or failure.
