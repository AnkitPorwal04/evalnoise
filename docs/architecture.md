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

Image IDs are resolved once before execution. Each task/repetition pair uses the same seed across profiles. Each profile batch has a separate worker pool and finishes before the next batch starts. Concurrency is an intentional experimental setting, not transparent acceleration.

## Modules

- `config.py`: strict parsing, ranges, configuration hashing, and schedule generation.
- `endpoint.py`: endpoint resolution and pinning, Engine API negotiation, bounded cancel-safe stats streaming, cgroup field availability.
- `coordination.py`: advisory per-daemon engine lock and orphan refusal.
- `recovery.py`: read-only diagnosis and ownership-checked cleanup.
- `probe.py`: optional preflight enforcement probe orchestration.
- `docker.py`: argv-only Docker boundary, preflight, immutable image resolution, hardened creation, inspection, optional sampling, logs, cleanup, outcome classification.
- `runner.py`: lifecycle ownership, batch scheduling, cancellation, and experiment artifacts.
- `verification.py`: bounded artifact/verdict parsing and immutable task contract hashes.
- `storage.py`: temporary-file, fsync, atomic-replace JSON persistence.
- `report.py`: identity checks, descriptive aggregation, CSV and escaped offline HTML.
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

## Reliability Boundaries

Atomic replacement protects individual JSON files from partial writes during normal failures. It is not a transaction across files, directory-fsync power-loss guarantee, or cryptographic attestation. SIGINT/SIGTERM request cooperative cancellation; SIGKILL, a machine crash, or an unreachable engine can leave containers and missing trial artifacts. Reports preserve missingness rather than synthesizing success or failure.
