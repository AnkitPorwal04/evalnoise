# Local Validation Record

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

Remote GitHub Actions, Python versions other than the local interpreter, native-Linux hosts, rootless engines, hard-kill recovery, independent verifier isolation, exact resource telemetry, and model/provider adapters remain unverified or unimplemented as described in the roadmap. No claim of perfect reliability is made.
