# Testing And Validation

## Layers

Unit tests use a fake Docker boundary to check validation, deterministic scheduling, classification, timeouts, cancellation, evidence errors, cleanup, missingness, report escaping, and atomic JSON persistence. These tests cannot prove engine behavior.

Opt-in integration tests use real reviewed fixtures to exercise OOM state, configured resource echoes, successful memory allocation, ordinary nonzero exits, exit 137 without OOM, timeout intervention, raw sampling, and container removal. Build the image first.

```sh
python3 -m unittest discover -s tests -v
EVALNOISE_DOCKER_TESTS=1 python3 -m unittest discover -s tests -v
python3 -m compileall -q evalnoise
```

The GitHub Actions definition covers Python 3.11 through 3.14 and a real Docker job. A workflow file is not evidence that CI has run. Keep local and remote verification claims separate.

## Experiment Checks

Run calibration and A/A sequentially. Verify exact trial counts, missingness, raw OOM flags, immutable image IDs, profile echoes, and cleanup errors. A successful CLI return alone is insufficient. Inspect the generated HTML on desktop and a narrow viewport; expand both manifest and trial evidence.

Rebuild a report with Docker stopped to exercise offline reconstruction. Test missing trial artifacts with isolated copies, never by deleting evidence from the original run. Any intentional fault-injection artifact must be clearly labeled.

## Future Tests

Native Linux and rootless variants; daemon disconnect during creation/start/cleanup; SIGKILL recovery; concurrent-process interference; controller enforcement; long-running log bounds; telemetry overhead; disk-full persistence; independent verifier isolation; provider timeout/retry attribution; statistical interval calibration.

## Evidence Policy

Record environment, command, run ID, counts, and limitations for measured results. Do not describe scripted fixtures as model evaluations, mocked tests as real engine tests, or successful local tests as a passed remote CI pipeline. Generated runs stay local and ignored by default because future workloads may contain sensitive data.
