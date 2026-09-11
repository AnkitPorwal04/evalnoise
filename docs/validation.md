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
