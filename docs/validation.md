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

### Remote CI

The published M2 commit `e8ab031` passed GitHub Actions run [`34594917203`](https://github.com/AnkitPorwal04/evalnoise/actions/runs/34594917203) on all five jobs: Python 3.11-3.14 unit jobs and the Ubuntu Docker job. The Docker job built all three images — `evalnoise-workloads:local`, `evalnoise-verifier:local`, and `evalnoise-probe:local` — and ran **199 tests in 57.9 s with no skips and no failures**. The four unit jobs each ran the same 199 tests with 16 skipped, as expected without `EVALNOISE_DOCKER_TESTS=1`. The run's logs contain zero `threading.excepthook` invocations and zero thread tracebacks.

This is a single remote observation on GitHub-hosted amd64 runners, so it confirms the suite is portable off this workstation but is not repeated evidence across hosts, nor a claim about the intermittency discussed above. Remote runners are a different kernel, engine version, and cgroup driver than the macOS Docker Desktop VM used for the fidelity records, and the timing figures in this document come from the local host, not from CI. The workflow also emits a non-blocking warning that `actions/checkout@v4` and `actions/setup-python@v5` target the deprecated Node.js 20 and are forced onto Node.js 24; no job failed because of it and no action was pinned or upgraded in response.

## v0.4 Agent Offline Slice

All records below are **offline**. No provider was contacted, no credential was read, and every ledger records `provider_requests_sent: 0` and `actual_charged_micros: 0`. Host: the same macOS Docker Desktop VM, Docker Server 29.1.5, Python 3.14.

### Deterministic Replay

Runs `agent-offline-12116cd6a8cf` and `agent-offline-c1be0f804675`, both `evalnoise run experiments/agent-offline.json --trust-config`, both `completed`, 2 of 2 trials recorded, both `passed` with a positive trusted verdict.

Comparing status, execution status, final artifact, and every step's tool calls, observation, metrics, request digest, admission reservation, and provider attempt outcomes across the two runs gives **identical evidence** (`True`), digest `e751946b0f6dca6745301dec06883cfc`. Both produced task contract hash `d64abcf816784e33…`, 6 admitted calls, 8 provider attempts, and 12 micro-USD of *simulated* reported cost.

Each trial ran the recorded sequence `list_files -> read_file -> final_answer`, so two tool containers plus one host-side answer per trial, four step containers per run under `agent/trials/`. Step 2 replayed a recorded `transient_error` before its `ok`, so the retry path is exercised rather than merely declared. Seed 42 answered 5 and seed 43 answered 14, each confirmed by the independent `sum-v1` verifier.

Run IDs differ between the two runs, as do timestamps and container identities; those are excluded from the comparison by design. Determinism is a property of the replay and the fixture, **not** a claim about any real provider.

### Fixture Correctness, Not An Impossibility Claim

Run `agent-offline-lazy-179fb16efb8a` replays `cassettes/offline-lazy-v1.json`, whose scripted policy answers immediately without reading the workspace. Result: 2 of 2 trials `verification_failed`, recorded pass rate 0.0, one step recorded per trial, **zero step containers created**, and the trusted verifier reporting `Incorrect sum or payload schema`.

**What this shows:** the loop genuinely depends on the tool observations, the cassette key covers the whole transcript, and a wrong answer is caught by independent verification rather than by the candidate. **What this does not show:** that the answer is impossible to reach without tools. The fixture answer is a sum of squares up to a seed-derived limit and is arithmetically derivable once that limit is known. Nothing here is evidence about model capability, and `tools/` and `cassettes/` are reviewed in-repo fixtures, not a public benchmark dataset.

### Budget, Recovery, And Isolation

These are covered by tests rather than by a standalone recorded run, and each is listed in [the testing notes](testing.md):

- Plan-level admission refuses before the run directory exists and before any container is created; the fake engine records zero `create` calls and the output directory is empty.
- Worst-case admission refuses a call whose *observed* cost would have fitted, and a shared ledger under eight concurrent workers admits exactly its ceiling and refuses the rest.
- Ceilings are enforced against committed figures, so an outstanding reservation blocks a second admission and a settled call releases its unused remainder. Eight concurrent workers cannot pass the output-token ceiling, and six concurrent workers driving only *failed* calls cannot pass the attempt ceiling.
- Provider attempts are reserved before the call, so an exhausted retry sequence settles into the ledger instead of escaping it; a hard provider error releases its whole reservation.
- Recorded usage above its reservation is retained in both the ledger and the step metrics with an explicit `accounting_errors` entry, and the run stops spending rather than discarding the observation. A failed call reports the same verdict, and an overrun on either path ends the trial as `budget_exhausted` with reason `usage_overran_reservation` while preserving the original provider cause in the detail.
- The ledger publishes no field claiming to hold a retained worst-case total. While a call is outstanding `committed.cost_micros` is strictly above the observed cost; once settled the two are equal, which is asserted in both states.
- No float appears anywhere in a ledger record; a recursive walk asserts it.
- Fake `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` values in the environment appear in no container argv, trial record, manifest, or report, and neither do their names.
- A full replay completes with `socket.socket` patched to raise.
- Real step containers echo `NetworkMode: none` and pass the enforcement audit; a real tool container run with `--network none` reports a failed connection from inside itself.
- A `SIGKILL`ed agent run leaves an owned orphan whose stage is `agent_step`, which `diagnose` finds among the plan-derived names and `cleanup --confirm` removes by exact ID, leaving the manifest status untouched.

### Test Suite

Local: **293 tests, 43.7-46.3 s, no skips and no failures** with `EVALNOISE_DOCKER_TESTS=1` against all four images and a `python3.11` on `PATH`. Without those, 293 tests with 21 skipped. v0.3 was 199 tests. The 199 M0-M2 tests are unchanged in semantics; the only edits to an existing test file were adding `**kwargs` to a fake backend's `create`, passing the new `max_attempts` argument to `Ledger.admit_call`, and adding one `Docker.call` diagnostics regression described below.

The unit suite was additionally run under **real CPython 3.11.13, 3.12.14, and 3.14.2**: 292 tests passing on each at the time of that sweep, 22 skipped on 3.11 because its tokenizer predates PEP 701 and the detector self-test does not apply there. That three-interpreter sweep predates the 293rd test and **has not been repeated**; the added test is interpreter-independent, but this paragraph is a record of what was run, not an inference about what would run.

#### A Known, Undiagnosed Docker-Suite Intermittency

**The intermittency is real, is not fixed, and is not claimed to be fixed.** The full Docker suite has now failed on **three separate occasions**.

The **first two** reported `FAILED (errors=2)`, once before and once after the budget reconciliation work. On neither occasion was the traceback captured before the run cleared, so both remain **entirely unattributed** — the error count of exactly two is the only characterisation available for them, and it is not evidence that they share a cause with each other or with the third.

The **third was captured**, and is the only failure with a log:

- The first two `test_agent_docker.py` methods errored in `setUpClass`-adjacent image resolution: `docker image inspect evalnoise-tools:local` exited **1** with `Error: No such image: evalnoise-tools:local`. **Later lookups of that same tag in the same run succeeded**, and the image was present in `docker images` before and after. A tag that is absent for two lookups and present for the rest is not explained by anything this repository controls, and **the cause is unproven**. It is *not* asserted to be a Docker Desktop image-store race, a daemon restart, or a test-ordering defect; none of those were demonstrated.
- A **separate subcase**, the verifier-crash path, failed differently: a runtime `inspect` error, recorded alongside a **large divergence between the wall-clock and monotonic deltas** for that interval. That gap is *consistent with* the host suspending mid-run. It **does not confirm** a suspend: no host power-management event was correlated against it, and the divergence is equally consistent with severe scheduling starvation. It is recorded because the measurement exists, not because it settles anything.

Because the two captured symptoms differ in both failing call and failure mode, they are **not** assumed to be one defect, and neither is assumed to explain the earlier unattributed pair.

**No mitigation was applied.** No retry, no wait loop, no image pre-warm, and no relaxed assertion was added anywhere in response to this. Weakening a test until it stops reporting a real environmental fault would destroy the only signal available. The one change made is diagnostic and non-behavioural: `Docker.call` now reports the process exit code and substitutes an explicit `no stderr diagnostics` marker for empty stderr, so a future recurrence cannot produce the unattributable bare `docker <verb> failed: ` that made the first two occurrences unanalysable. That is covered by a regression test confirmed to fail against the prior message.

Counting only full Docker runs since the third failure, the suite has passed **two consecutive runs**, both recorded for this commit: 292 tests in 44.2 s before the regression test was added, and **293 tests in 44.1 s** after, each with no skips and no failures. Two passes are not a diagnosis and are not offered as one. Treat the Docker suite as carrying a known, uncharacterised intermittency on the order of **three failures across roughly sixteen full runs**, always in the real-container tests and never in the unit tests. Capture the log if it recurs; a cleared terminal is why two of the three are permanently unattributable.

### Report Browser Inspection

The generated agent report was opened in a browser and inspected at 1440 px desktop and 390 px mobile. The **Agent provenance and budget** section rendered at both widths with no horizontal overflow, no clipped table content, and no browser console errors, and the no-provider-request notice and the tool-call chain were visible.

That inspection was performed against run `agent-offline-dc795cea69b1`, whose report was generated **before** the misnamed `worst_case_admitted_micros` ledger field was removed. That run directory is retained unedited rather than rewritten, because a persisted manifest is evidence. The removal does not invalidate the inspection: the field occurred exactly twice in that report and both occurrences were inside collapsed `<pre>` blocks, which was verified by character offset against the parsed `<pre>` ranges, and it never appeared in a table cell. The visible layout that was checked is therefore unchanged. **No browser inspection has been performed on the post-fix reports**, and the newer runs are recorded on their JSON evidence alone. The post-fix report for run `agent-offline-12116cd6a8cf` is the one queued for that inspection; until it is actually opened and checked at both widths, this section makes no rendering claim about it.

### Evidence Defects Found And Fixed

Two v0.4 evidence defects were found in review and fixed before publication, each with a regression test confirmed to fail against the defective code:

1. `budget.data()` published `worst_case_admitted_micros`, assigned from `committed_micros`. Because settling replaces a reservation with the observation, after settlement that field simply restated the observed cost under a name asserting it was a worst case. The field is removed rather than renamed; `committed` is documented as a live commitment that is neither a peak record nor a spend figure. There is no compatibility cost because the field never appeared in a released schema.
2. `Ledger.commit_failed_call` discarded the verdict `_settle` returned and hardcoded `accounting_error: None`, so on the failed-call path the step trace would claim no error while the ledger recorded one. The verdict is now propagated, and every settlement path in the agent loop routes through one helper so an accounting overrun outranks whatever else ended the call and produces `budget_exhausted` / `usage_overran_reservation` consistently.

The second defect is currently **latent in normal operation**: `RecordedProvider.complete` caps attempts at the configured `max_attempts`, so a real cassette cannot overrun its attempt reservation. It is nonetheless a live contract for any future provider, so the failure is injected at the provider boundary in the tests rather than left unexercised.

### Python 3.11 Compatibility Defects Found And Fixed

Two publication blockers were found in v0.4 code before release, both PEP 701 syntax that only Python 3.12+ accepts while `pyproject.toml` declares `>=3.11` and CI runs 3.11:

1. `report.py` used a nested same-quote f-string, `f'{row['provider_s_total']:.3f}'`. Real 3.11 reports `SyntaxError: f-string: unmatched '['`.
2. `report.py` used a backslash inside a replacement field, `{agent_rows or '<tr><td colspan=\"9\">…'}`. Real 3.11 reports `SyntaxError: f-string expression part cannot include a backslash`.

**`ast.parse(source, feature_version=(3, 11))` detected neither.** It was tested directly against both constructs and against the offending file, and accepted all of them, because the 3.12+ tokenizer handles f-strings before `feature_version` applies. That approach is a false negative and is not used. The tokenizer-based detector in `test_compat.py` catches both, and a real 3.11 interpreter is invoked when available; the tokenizer heuristic alone caught only the first defect, and the real interpreter is what surfaced the second.

Two regression tests pin the five v0.3 configuration digests and the three v0.3 `verified.json` contract hashes, recorded from the v0.3 tree at commit `c8ea9ca`, because v0.4 adds optional fields that must never enter a hash when unset.

### v0.4 Remote CI

The published v0.4 commit `69fb72b` passed GitHub Actions run [`34680930513`](https://github.com/AnkitPorwal04/evalnoise/actions/runs/34680930513) on all five jobs. The Docker job built all four images — `evalnoise-workloads:local`, `evalnoise-verifier:local`, `evalnoise-probe:local`, and `evalnoise-tools:local` — and ran **293 tests in 65.7 s, `OK (skipped=1)`**. The single skip is `test_compat.RealInterpreterTests.test_python311_compiles_every_package`, skipped as `no python3.11 interpreter on PATH`: the Docker job installs no second interpreter, so the real-3.11 compile check does not run there.

The four unit jobs each ran the same **293 tests with `OK (skipped=22)`** — the 21 Docker methods plus that same real-interpreter check. It is skipped even in the `unit (3.11)` job, because that job exposes its interpreter as `python`, not as `python3.11`, and the check looks for the latter on `PATH`. **The real-3.11 compile check therefore did not execute anywhere in CI**, on any job. Remote CI proves the suite passes *under* 3.11, which is the stronger property, but the specific guard added for the PEP 701 defects is currently exercised only on this workstation. Do not read a green CI as evidence that that guard ran.

Job times were 5.0 s (3.11), 8.8 s (3.12), 7.9 s (3.13), 7.7 s (3.14), and 65.7 s (Docker). The Docker job is markedly slower than the 44.1 s local run, which is expected on a cold shared runner and is not a measurement of anything.

This is a single remote observation on GitHub-hosted amd64 runners. It confirms the suite is portable off this workstation; it is **not** independent evidence about the intermittency recorded above, which has only ever been observed locally, and one green remote run is not a sample. The workflow again emitted the non-blocking warning that `actions/checkout@v4` and `actions/setup-python@v5` target the deprecated Node.js 20 and are forced onto Node.js 24; no job failed because of it and no action was pinned or upgraded in response.

### v0.4 Remaining Gaps

The full M3 gate is **open**. Not validated: any real provider call, real retry, timeout, or rate-limit attribution, a price table against an actual invoice, a public reviewed task subset, stateful or multi-tool turns, and a Harbor environment adapter. Provider latency in these records is a cassette lookup and is not a latency measurement. The budget ledger is synthetic and its prompt estimate is a character heuristic, so it must not be relied on as a spending control for a real provider.

### Remaining Gaps

Two exited containers are on the engine and were both left in place, because neither has a surviving run directory, so no manifest can authorise the ownership-checked cleanup path and removing either by hand would be exactly the unscoped sweep this project refuses.

- `evalnoise-e0a8eb826040-stream`, exit 0, from an earlier aborted session that predates this work.
- `evalnoise-638ff5d52397-r000-p00-t000-verify`, exit 1, created during the v0.4 working period and carrying this workstation's engine, owner, and run labels. Its name and nonzero exit are **consistent with** the verifier-crash subcase of the captured suite failure described above, but run `638ff5d52397` left no directory, so the association is **not proven** and is not asserted. `diagnose` reports nothing for it, which is correct: without a manifest there is no plan to derive expected names from. It is recorded here rather than removed.

Beyond those two, an engine query after testing found no EvalNoise-labelled containers from any run recorded here.

Still not validated: native-Linux and rootless hosts, repeated overhead records across hosts or intervals, any statistical characterisation of sampler cost, interference from *other* tenants sharing the daemon, hostile-code isolation, repository-file artifact transfer, and browser rendering beyond the single engine and two viewports recorded above. No claim of perfect reliability is made.
