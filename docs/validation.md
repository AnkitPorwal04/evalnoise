# Validation Record

## M0 Baseline

The following baseline observations describe v0.1. The v0.2 milestone record follows below; it does not retroactively change these experiments.

## Scope

Validated on September 11, 2026 using Python 3.14.2 and Docker 29.1.5 on macOS ARM64 with a Linux Docker Desktop VM. The recorded engine reported 12 CPUs, 8,217,100,288 bytes of memory, and cgroup v2. This is not native-Linux or cross-platform validation.

The fixture image was built locally from the pinned Python base. Executed image config ID: `sha256:f7125aadcb866fd73ae20c41a98ecc0405fead2678773dcb381f625bba65ca1a`. Full engine/image metadata is in each run manifest. No model APIs, credentials, private datasets, or paid services were used.

## Automated Checks

`EVALNOISE_DOCKER_TESTS=1 python3 -m unittest discover -s tests -v`: 40 tests passed, comprising 35 unit/lifecycle/report tests and five real Docker integration tests.

The integration tests exercised memory-limit OOM, successful allocation with sufficient memory, ordinary exit 7 with stderr capture, exit 137 without an OOM flag, and timeout intervention with sampling. Each checked successful removal of its own container.

Python source compilation passed. Language-server diagnostics scanned all eight core Python files with no errors after correcting validated-integer type annotations at construction boundaries.

## Real Experiments

Run `memory-calibration-2ca4fc278ad1`: 18 of 18 planned trials recorded. The 48 MiB profile had six clean passes and three OOM failures. The 256 MiB profile had nine clean passes. All three matched memory-fixture attempts changed from OOM failure to pass. The fixed suite's descriptive paired pass difference was 33.3 percentage points; this is deliberately induced fixture behavior, not an LLM capability result.

Run `identical-profile-control-72734be5469b`: 12 of 12 planned trials recorded. Both identical profiles passed all six attempts, with zero paired pass difference. This does not prove the runner has no noise; it checks this small control experiment.

Earlier development runs are retained separately. No run artifacts were committed or published. The two final experiments ran sequentially, not concurrently with one another or the integration suite. An engine query after testing found no remaining EvalNoise-labeled containers.

## Report Verification

Reconstructed the calibration report with Docker excluded from the process PATH. Browser checks at 1440 x 1000 and 390 x 844 confirmed both profile rows, all 18 trial detail sections, working expandable evidence, and no page-wide horizontal overflow. The table scrolls within its own container on mobile. Both calibration and A/A reports loaded without browser page errors.

Reports are served temporarily on loopback port 4177 for local inspection. The generated HTML itself also works as an offline file. This is not a deployed application or execution API.

## Review And Remaining Gaps

Five read-only review passes covered scope, lifecycle, security, statistical reporting, and tests. Actionable findings led to collision-free trial IDs, escaped edited-manifest fields, structural missing-container checks, unexpected-error diagnostics, and additional cancellation/concurrency/denominator regressions. This was not an external security audit or statistical validation of a model benchmark.

At the M0 validation checkpoint, remote GitHub Actions, other Python versions, native-Linux hosts, independent verifiers, and the later roadmap items were not yet validated. No claim of perfect reliability was made.

## M1 Independent Verification

Version 0.2.0 passed 61 tests locally using the same Python 3.14.2 / ARM64 Docker Desktop environment: 55 unit/lifecycle/protocol/report tests and six real Docker test methods. The new Docker method exercises seven cases: correct answer, wrong answer, candidate-spoofed verdict, missing artifact, verifier crash, malformed verdict, and verifier timeout. Compilation checks also passed.

Run `independent-verification-68965e334466` recorded all six planned trials. Correct answers passed twice; wrong answers and spoofed candidate verdicts each produced two `verification_failed` outcomes. All candidate processes exited successfully, demonstrating that successful execution alone no longer implies verified correctness. The fixture checks a known sum-of-squares answer, not an agent benchmark.

Executed workload image config ID: `sha256:d940b4e35a9f2c854e2c2f80dcf750d3d0ffbe2a3cad7918e3e078c44d0323ce`. Trusted verifier image config ID: `sha256:8be9e75798cc1f0496724d6e3e882621da7c1213442abfaa1077da5fe458fc5b`. Both identities and task contract hashes are retained in the manifest. The experiment ran separately from Docker integration tests.

Browser checks at 1440 x 1000 and 390 x 844 verified task-level outcomes, expandable evidence, no page-wide horizontal overflow, and no page errors. The report is available locally under the run directory, not published with the source.

The baseline diagnostic commit `ecf5598` passed GitHub Actions run `34579741704`, including Python 3.11-3.14 unit jobs and Ubuntu Docker integration. An earlier memory-pressure check failed because the observed process failure lacked the expected OOM classification; the following run passed. The revised test accepts allocation failure without inventing an OOM flag and checks classification against the actual evidence. This is not a demonstrated root cause for the intermittency. The published M1 commit `de54d74` then passed GitHub Actions run `34581353517` on all five jobs: Python 3.11-3.14 unit jobs and the Ubuntu Docker job, which built both images and ran all 61 tests with no skips. This is a single remote observation on GitHub-hosted amd64 runners, not repeated evidence across hosts or a resolved intermittency claim.

Remaining gaps include repository-file artifact transfer, hostile-code isolation, rootless enforcement validation, hard-kill recovery, exact resource telemetry, statistical inference, and model/provider adapters. See the roadmap for acceptance gates rather than treating this bounded milestone as the complete platform.

## M2 Measurement Fidelity

The M0 and M1 records above are historical and unchanged by this section.

### Environment

Python 3.14.2, macOS ARM64, Docker Desktop 29.1.5 in a Linux VM. Engine API 1.52 (daemon minimum 1.44), cgroup v2, 12 reported CPUs, daemon ID `51d53897-aea4-430d-940e-4635e5a17651`. Endpoint resolved from a Docker context over a local Unix socket. Workload image config ID `sha256:58ad86cdd8e458bf56c6f2bc25e6c20a8f0c30779367b30fbb6d08b8a7d10404`. No model APIs, credentials, private datasets, or paid services were used. This is not native-Linux, rootless, or cross-platform validation.

### Automated Checks

`EVALNOISE_DOCKER_TESTS=1 python3 -m unittest discover -s tests -v`: **199 tests passed in 39.6 s with no skips and no failures** — 183 unit/protocol/endpoint/coordination/telemetry/report tests plus 16 real Docker test methods. Without the opt-in variable the same discovery runs 199 tests with 16 skipped. `python3 -m compileall -q evalnoise probes tests workloads verifiers` passes. The Docker tests were run sequentially and alone, never beside another measurement, and the run's stderr was read line by line: zero thread tracebacks and zero `threading.excepthook` invocations.

The real-engine tests exercised endpoint and daemon identity pinning, API negotiation against the daemon's advertised version, a live stats stream carrying integer `throttling_data` counters with the reader closed before removal, cgroup v2 field availability, the enforcement probe reading `cpu.max`, `memory.max`, `memory.swap.max` and `pids.max`, an affinity mask confirmed through `cpuset.cpus.effective`, and a diagnose/cleanup cycle on a real run.

### Hard-Kill Recovery, Coordination, And Loaded Ordering

`tests/test_recovery_docker.py` adds three real-engine gates that start real containers and remove only fixtures they created, through the ownership-checked cleanup path. No prune or sweep is used anywhere. All three passed in 11.2 s:

- **SIGKILL orphan.** A child process is `SIGKILL`ed mid-trial. `diagnose` is read-only and reports the survivor as `owned` and `running`, with 0 recorded and exactly one missing trial, `engine_matches` true, and `live_coordinator` null — confirming the kernel released the `flock` on process death with no stale-PID reaping. `cleanup --confirm` then removed exactly the one resolved 64-character container ID with no failures. A second `diagnose` showed no containers, the **same** missing trial, and run status still `running`. A byte-level comparison of every file in the run directory before and after proved only `cleanup.json` was added: no trial status, verdict, or manifest status was rewritten.
- **Concurrent second run across different output directories.** While the first run holds the engine, a second `execute()` into a *different* `--output` directory is refused with a `CoordinationError` naming the holder's run ID and stating "Not a distributed lock", and the refused run creates no output directory at all. After `SIGKILL` of the holder, the lock is free again.
- **Real CPU-loaded batch and concurrency ordering.** Four `cpu-long` trials across two `concurrency: 2` profiles. Engine-reported `StartedAt`/`FinishedAt` spans show the two trials *within* each profile genuinely overlapped, while the two profile batches did not overlap each other. Every trial passed with an enforced resource echo and no cleanup error.

### Telemetry Reader And Snapshot Sampler

A reader-thread cancellation defect found in an earlier overhead run, where `close()` raced `HTTPResponse` and every reader raised `AttributeError` in `http.client._close_conn`, is fixed and covered: the cancelling thread now only shuts the socket down and the owning reader performs `connection.close()`. A 30-iteration close-race stress at varied timings produced zero reader tracebacks, and a regression test asserts `threading.excepthook` is never invoked. Passing exit codes alone were insufficient to detect this, so telemetry runs must be read for thread tracebacks.

Streaming also refuses to start without a resolved endpoint. When telemetry has negotiated `engine_stream` but no endpoint is pinned, `Docker.stream` raises `DockerError("Streaming requires a pinned Docker endpoint")` rather than reaching for a socket path of `None`. Two regression tests cover the guard: one asserts the raise, and one asserts that `cli_snapshot` telemetry still declines streaming by returning `None` without raising, so the guard cannot silently swallow the ordinary fallback path. The raising test was mutation-checked by deleting the guard, which fails it.

The CLI snapshot fallback sampler received the same treatment. It previously joined its thread without a bound and appended directly into the live trial record. It now buffers privately under a lock and `close()` waits a bounded 8 s — deliberately longer than the 5 s `docker stats --no-stream` subprocess timeout — then **detaches**, so a wedged thread keeps running but is permanently barred from recording. Cleanup, persistence, and the workload verdict are unaffected; a leak is reported as `thread_leaked` metadata. Both properties were mutation-tested: removing the detach guard fails the acceptance test with a late sample appearing in the buffer, and restoring the unbounded join hangs the suite past 40 s.

### Recorded Sampler Overhead Contrast

Run `sampler-overhead-e60d93977bd0`, config SHA-256 `3d70db5a40d2e87ff2228a51c8a80b185ddb922de9a56eee71a49ce97aa90a14`, 2026-09-11T10:59:46Z to 11:01:02Z. **20 of 20 planned trials recorded, all `passed`.** Two otherwise-identical profiles differ only in `sample_interval_s`, and the scheduler alternates them rather than running two blocks.

| Arm | `sample_interval_s` | Telemetry source | Successful median | n | Samples received / retained |
| --- | --- | --- | --- | --- | --- |
| `sampler-off` | 0 | disabled | 3.561533 s | 10 | not measured / not measured |
| `sampler-on` | 2 | engine_stream | 3.5607595 s | 10 | 40 / 20 |

Across 10 complete pairs there were 0 non-pass-to-pass and 0 pass-to-non-pass changes. The sampled arm recorded 0 telemetry-error trials, 0 truncated trials, and **0 leaked reader threads**. Both arms had 10 of 10 trials resource-audited with 0 enforcement mismatches. Engine identity was stable across the run.

**What this does and does not support.** The two medians differ by 0.8 ms out of roughly 3.56 s, and the *sampled* arm's median is the lower of the two. That ordering is itself the point: the difference is smaller than this harness can resolve, so no direction and no magnitude of sampler overhead is established. **This is not a zero-overhead claim, not a no-overhead-detected claim in any inferential sense, and carries no statistical test, confidence interval, or significance statement.** It is a descriptive record of one 20-trial run, one workload, one host, and one sampling interval. Ten paired observations cannot characterise a distribution, and a host with other uncontrolled load could produce a different record. **Sampling therefore remains opt-in and off by default.**

Run `sampler-overhead-e680b81e2d6a` is **deliberately excluded** from this record. It also completed 20 of 20 trials with 40 samples received and 20 retained, and its exit code was clean, but it was collected while the reader-cancellation defect above was live and every reader thread raised through `threading.excepthook`. Its timings are not reported here and must not be compared with the retained run. It is retained only as an example of why exit codes are insufficient evidence for telemetry runs.

### Enforcement Probe

Fresh run `probe-3e9cb31d8dae` via `evalnoise probe experiments/enforcement-probe.json --trust-config`: status `completed`, `conclusive: true`, `enforced_as_requested: true`, engine identity stable.

- `unpinned` (1 CPU, 128 MiB, no mask): `cpu.max` `100000 100000`, `memory.max` `134217728`, `memory.swap.max` `0`, `pids.max` `128`, `cpuset.cpus.effective` `0-11`, 12 online CPUs.
- `pinned` (2 CPUs, 256 MiB, `cpuset_cpus: "0-1"`): `cpu.max` `200000 100000`, `memory.max` `268435456`, `memory.swap.max` `0`, `pids.max` `128`, requested mask `0-1` with `cpuset.cpus.effective` `0-1` and **2 online CPUs**, so the mask was confirmed from inside the container rather than assumed from the request echo.

The probe reads a separate reviewed image's own cgroup files. It shows the limits this engine applied to a container created with this profile. It does not prove dedicated CPUs, scheduler fairness, freedom from host or VM interference, or that limits stay constant during later trials.

### Report Verification

`python3 -m evalnoise report runs/sampler-overhead-e60d93977bd0` was re-run against the retained evidence under the shipped code. The regenerated summary is identical to the persisted one except for the newly added descriptive `affinity_configured` field, and an MD5 of every trial artifact was unchanged before and after, so only `summary.json` and `report.html` were rewritten. Reports are served temporarily on loopback port 4177 for local inspection; the generated HTML also works as an offline file. This is not a deployed application or execution API.

The regenerated report was then opened in a browser at `http://127.0.0.1:4177/sampler-overhead-e60d93977bd0/report.html` and inspected at two viewports, 1440 px desktop and 390 px mobile. The measurement fidelity section rendered at both widths with no horizontal overflow, no clipped table content, and no browser console errors. This is a visual check of one generated report on one browser engine; it is not cross-browser, accessibility, or print-layout validation.

### Remaining Gaps

An exited container `evalnoise-e0a8eb826040-stream` from an earlier aborted session predates this work and was left in place: its run directory no longer exists, so no manifest can authorise the ownership-checked cleanup path, and removing it by hand would be exactly the unscoped sweep this project refuses. Beyond that, an engine query after testing found no EvalNoise-labelled containers from any run recorded here.

Still not validated: native-Linux and rootless hosts, remote CI for v0.3, repeated overhead records across hosts or intervals, any statistical characterisation of sampler cost, interference from *other* tenants sharing the daemon, hostile-code isolation, repository-file artifact transfer, and browser rendering beyond the single engine and two viewports recorded above. No claim of perfect reliability is made.
