# M2 Measurement Fidelity: Design And Checklist

Working document for the v0.3.0 slice. It records what was built and what each part does
*not* claim. The permanent documents (`data-contract.md`, `architecture.md`, `security.md`,
`testing.md`, `validation.md`) have since been updated from the measured runs recorded below;
where they disagree with this working document, they are authoritative.

## Delivered

### 1. Pinned endpoint and daemon identity (`endpoint.py`, `docker.py`)

Resolution order matches the documented Docker CLI rules: `DOCKER_CONTEXT` overrides
`DOCKER_HOST`, which overrides the context chosen by `docker context use`, which falls back
to `unix:///var/run/docker.sock`. A failure to read the selected context is an error, never a
silent fall-through to the default socket.

Resolution happens once. **Every** later `docker` invocation is pinned: a local Unix socket
(including one derived from a context) is pinned by explicit `--host`, so a later
`docker context use` cannot leave the CLI and the stats socket pointing at different engines.
Only a remote context is pinned by `--context <name>`, to keep its TLS material, and such a
context is revalidated before operations — if its host changed after pinning, work stops.

The process environment is snapshotted at backend construction and `DOCKER_CONTEXT` /
`DOCKER_HOST` are removed from the child environment, because an inherited `DOCKER_CONTEXT`
conflicts with an explicit `--host`.

Only `skip_tls_verify` is retained from a context. Certificate paths, key paths, and
`TLSMaterial` are never read into an artifact.

Engine identity is the daemon `ID` from `/info` (the `engine-id` UUID), plus name, server
version, cgroup version, and CPU count. The daemon ID is re-read at the end of every run;
a change or an unreachable daemon sets `engine_identity_final.stable = false` and appends a
manifest warning. No trial is reclassified on that basis.

### 2. Raw bounded streaming telemetry (`endpoint.StatsStream`)

`GET /v{pinned}/containers/{full-id}/stats?stream=true` over `AF_UNIX` using `http.client`
with a `connect()` override. Version negotiation uses the unversioned `/_ping` endpoint,
`HEAD` first with a `GET` fallback, reading the `Api-Version` header.

The pinned version is the daemon's own advertised version. We never invent a maximum below
the daemon's minimum. If the daemon advertises below `STATS_API_FLOOR` (1.41) or below its
own reported minimum, streaming is refused explicitly and telemetry stays on the CLI
snapshot path with a recorded reason.

Bounds: `MAX_FRAME_BYTES` 1 MiB, `MAX_SAMPLES` 3600, `MAX_TOTAL_BYTES` 8 MiB. All three are
enforced on **received** data, before the retention filter, so frames dropped by retention
still count against the caps. A single syntactically valid but oversized record is rejected
too, not only an incomplete one. Exceeding a bound sets `truncated` and stops collection; it
never changes a workload outcome.

The CLI snapshot fallback (`runner.CliSampler`) is held to the same contract. It buffers into
private lock-guarded lists instead of appending straight into the trial record, caps retained
samples and recorded faults, and `close()` joins for a bounded 8 s — chosen above the 5 s
`docker stats --no-stream` subprocess timeout so a healthy sampler never reports a false leak —
and then *detaches* inside the same critical section that copies the buffers. A thread that is
still alive keeps running but can no longer record anything, so it cannot touch evidence that
was already reported or persisted, and cleanup never waits on it. A leak sets `thread_leaked`
and is reported; it never changes a workload outcome. The snapshot path reports formatted
display strings rather than raw integer counters, so its `derived` block is `null` with a note
saying why, never a fabricated zero.

Bytes are fed to an incremental UTF-8 decoder, so a multi-byte code point split across two
socket reads is decoded correctly rather than replaced. Frames may arrive split across reads
or several per read. End of stream with an empty buffer is normal termination; end of stream
inside a partial record is recorded as an error.

Cancellation: the owning thread calls `close()`, which sets a stop flag and then repeatedly
shuts the connection down until the reader exits or a bounded deadline passes — this also
closes a connection the reader opened *after* the first shutdown, which previously could hang
for the full 30 s socket timeout. `close()` runs **before** container removal, so a live
stream can never turn into a cleanup failure. `closed_before_cleanup` is only true if the
thread actually stopped; otherwise `thread_leaked` is set. Returned evidence is a copy, so a
leaked thread cannot mutate an artifact after `close()` returns.

Samples are stored verbatim under `raw`, with `received_elapsed_s` (host monotonic),
`engine_read`/`engine_preread` (daemon clock), and `degraded`. A `degraded` sample is a real
engine record: the daemon publishes an otherwise-empty response when its own collection
fails. Those are counted but excluded from derived values.

`sample_interval_s` is a **retention** filter over engine `read` timestamps, not a request
for a rate. The daemon controls cadence. Dropped samples are counted; nothing is
interpolated or resampled.

### 3. Throttling and availability without invented zeros

`cpu_stats.throttling_data.{periods,throttled_periods,throttled_time}` are stored raw.
`derived` holds first-versus-last deltas only, and is `null` whenever fewer than two usable
samples exist or a counter went backwards. `throttled_fraction` is `null` when
`periods_delta` is zero rather than reported as `0.0`.

Counters are read with `type(v) is int`, so a JSON `true` is not silently used as `1`.
Observed maxima (`memory_usage_max_observed`, `pids_max_observed`) are reported from a single
usable sample; only the deltas require two.

`telemetry_support` lists fields that do not exist on the observed cgroup version
(`percpu_usage`, `max_usage`, most `blkio_stats` arrays on v2) and the fields whose meaning
changed (`failcnt` is the `memory.events` OOM count on v2). Absent fields are `null` and
listed; they are never zero. An unrecognised cgroup version asserts nothing.

### 4. Resource enforcement, two independent layers

Layer A, always on: after `create` and before `start`, `HostConfig` is echoed for all
workload *and* verifier containers and compared with the profile. A mismatch produces a new
`enforcement_error` status; the container is never started. This is a request echo, not a
controller audit.

Layer B, optional (`evalnoise probe --trust-config`): a separate reviewed image (`probes/`)
started with a profile's exact flags reads its own `cpu.max`, `memory.max`, `memory.swap.max`,
`pids.max`, `cpuset.cpus`, `cpuset.cpus.effective`, and `cpu.stat`, and reports them through
the existing bounded `json-env-v1` envelope as `evalnoise_probe`. Candidate workloads and
verifiers are never modified or instrumented. Without a cgroup v2 unified hierarchy the probe
is `inconclusive`, never a false pass.

The probe executes a configured image, so it requires `--trust-config` like `run`. It is not a
read-only command. It runs through the ordinary `execute()` path: hexadecimal run ID, engine
lock, orphan refusal, architecture and affinity validation, a real manifest and plan, the same
container naming, hard stop after any cleanup failure, end-of-run engine identity check, and a
persisted `probe-summary.json`. A probe run is therefore diagnosable and cleanable by the
normal recovery commands. There is no auto-resume.

Neither layer claims a CPU reservation. `CLAIM` states this in the artifact.

### 5. Optional CPU affinity

Optional `cpuset_cpus` per profile. Grammar is validated at parse time (indices and ascending
ranges, no duplicates), and the mask must contain at least `ceil(cpus)` entries. At run time
an index at or beyond the engine's reported `NCPU` is refused; if `NCPU` is unusable the run
is refused with an explicit "cannot be validated" message rather than a guess. The refusal
text states that `NCPU` is a count, not proof that indices `0..NCPU-1` are all schedulable on
a constrained host; only the probe's `cpuset.cpus.effective` can confirm the real mask.

### 6. Advisory per-engine coordination (`coordination.py`)

`flock` on `${EVALNOISE_STATE_DIR:-${XDG_STATE_HOME:-~/.local/state}}/evalnoise/engine-<sha256(id)[:16]>.lock`,
outside every output directory, so two runs writing to different `--output` paths still
collide. The kernel releases the lock when the holder dies, including under `SIGKILL`, so
there is no stale-PID reaping logic.

Independently, before acquiring, running containers labelled `io.evalnoise.engine=<id>` whose
`io.evalnoise.run` is not ours refuse the start. An unreadable listing refuses rather than
assuming an empty engine.

Scope, stated in the manifest and in every refusal message: cooperation between EvalNoise
processes for one local user against one daemon. Not a distributed lock. Other users, hosts,
tools, and general host load are not excluded.

### 7. Recovery (`recovery.py`)

`evalnoise diagnose <run-dir>` is read-only: it validates the manifest and writes nothing.
Validation fails closed on a missing, non-object, or malformed manifest, a non-hex `run_id`,
an unparseable config, or a `plan` that does not equal the schedule that config and seed
deterministically produce. Genuine older manifests pass because `plan()` is unchanged.

Container existence is established structurally with `docker ps --all --no-trunc --filter
name=^/<name>$`, then inspected. A daemon outage, an unreadable listing, or a failed
inspection propagates as an error; it is never read as "absent", which previously produced
false clean reports.

Ownership requires **all** of: the engine reporting exactly `/<expected-name>`, `io.evalnoise.run`
equal to this run, `io.evalnoise.engine` equal to this engine (now required, not optional), and
the image identity for that **exact stage** — the workload image for a workload container and
the verifier image for a `-verify` container, never a union of both. A missing or malformed
image identity in the manifest fails closed rather than matching anything.

`evalnoise cleanup <run-dir> --confirm` **acquires and holds the engine lock** across the whole
validation and removal, so it cannot race a coordinator, and it refuses while *any* coordinator
is live — including a still-running instance of the same run, which the previous check-then-act
version would have torn down. Every candidate is validated before the first removal, and removal
is by resolved 64-character container ID so a name cannot be re-pointed between check and
delete. It writes `cleanup.json` only, and never touches trial statuses, verdicts, the manifest
status, images, volumes, or networks. There is no resume.

### 8. Sampler overhead harness

`experiments/sampler-overhead.json`: the `cpu-long` workload, fixed seed 7, 10 repeats, two
otherwise-identical profiles differing only in the new per-profile `sample_interval_s`
(0 versus 2). The existing scheduler already randomises profile order per repetition, so the
two arms alternate rather than running as two blocks.

`cpu-long` is a fixed 12,000,000-iteration SHA-256 chain verified against a known digest, not a
sleep and not a timeout. It measures roughly 3.7 s of real processing at one CPU on the
reference host, long enough that a 2 s retention interval can retain samples, while 20 paired
trials stay near two minutes rather than a CI-length run. It is not part of any test job.

Recorded as `sampler-overhead-e60d93977bd0`: 20 of 20 trials passed, `sampler-off` median
3.561533 s and `sampler-on` median 3.5607595 s at n=10 each, 10 complete pairs with no outcome
changes, 40 samples received and 20 retained, zero telemetry errors, truncations, and leaks.
The gap is under a millisecond with the sampled arm nominally faster, which is why this is
written up as an unresolved contrast rather than an overhead measurement. The earlier
`sampler-overhead-e680b81e2d6a` is excluded: it exited cleanly but was collected while the
reader-cancellation defect was live and every reader raised through `threading.excepthook`.

## Checklist status

- [x] Endpoint precedence, pinning, and no TLS material in artifacts
- [x] Daemon ID recorded, re-checked, warned on change or unreachability
- [x] Unix-socket stats streaming with negotiation, bounds, and cancel-before-cleanup
- [x] Raw throttling counters, nullable derived values, availability map
- [x] Request echo audit before start for workloads and verifiers
- [x] Optional reviewed enforcement probe image and command
- [x] Optional validated cpuset with an honest unknown-mask caveat
- [x] Advisory per-engine lock outside output dirs, orphan refusal
- [x] Read-only diagnose, ownership-checked cleanup by full ID, separate audit
- [x] Sampler opt-in by default, per-profile override for the overhead contrast
- [x] Both collectors bounded, detaching, and unable to mutate evidence after close
- [x] CI builds the probe image before the Docker job
- [x] Real `SIGKILL` orphan diagnosis, exact-ID cleanup, unchanged missing trials
- [x] Real refusal of a concurrent second run writing to a different output directory
- [x] Real loaded batch/concurrency ordering from engine container timestamps
- [x] Sampler overhead contrast recorded (`sampler-overhead-e60d93977bd0`)
- [x] Permanent doc updates and validation record

The overhead contrast is recorded as a descriptive result only. It resolves no overhead
figure in either direction, so sampling stays opt-in; that is the conclusion, not a deferral.

## Not delivered, deliberately

No automatic resume. No cross-run aggregation of telemetry. No CPU-percentage figure. No
`cpu-shares`, `memory-reservation`, blkio, or GPU limits. No remote-endpoint streaming. No
statistical inference over throttling counters.
