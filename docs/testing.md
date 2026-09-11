# Testing And Validation

## Layers

Unit tests use a fake Docker boundary to check validation, deterministic scheduling, classification, timeouts, cancellation, evidence errors, cleanup, missingness, report escaping, and atomic JSON persistence. These tests cannot prove engine behavior.

Opt-in integration tests use real reviewed fixtures to exercise memory-pressure failure, configured resource echoes, successful memory allocation, ordinary nonzero exits, exit 137 without OOM, timeout intervention, raw sampling, and container removal. The pressure fixture accepts observed OOM, an explicit Python MemoryError, or exit 137 without claiming the latter proves OOM; engine/platform timing can change the available evidence. Classifier unit tests independently require OOM labels to follow observed flags only.

Verification tests cover strict protocol parsing, bounded environment transport, content hashes, pending checkpoints, cancellation, cleanup failures, and verifier scheduling. Real container cases cover correct/wrong/spoofed/missing candidates and crashed, malformed, and timed-out verifiers. Build both images before integration tests.

v0.3 adds four suites. `test_endpoint.py` covers endpoint precedence and pinning, TLS material never reaching an artifact, environment snapshotting, refusal of a mutable remote context and of a missing default socket, API negotiation against a real chunked HTTP server on a Unix socket, the stream's frame/sample/byte bounds, split multi-byte decoding, truncated-EOF detection, cancellation races, nullable counters, and the resource audit — including that `0-1`, `0,1` and `1,0` are the same CPU mask while a malformed, missing, non-string, or unrequested echo fails closed. `test_coordination.py` covers the engine lock across output directories, release on process death by subprocess `SIGKILL`, release after a failure anywhere between acquisition and the final write, orphan rechecking under the lock, plan and image validation, engine outages never reading as a clean run, and adversarial cleanup ownership. `test_telemetry.py` covers sampler wiring in a trial, close-before-removal ordering, telemetry faults not changing an outcome, the enforcement audit, probe evaluation, and the report's fidelity section. `test_recovery_docker.py` is opt-in and real: `SIGKILL` orphan diagnosis and exact-ID cleanup, refusal of a concurrent second run writing to a different output directory, and batch/concurrency ordering under genuine CPU load.

Both telemetry collectors have adversarial coverage. For the CLI snapshot sampler a fixture parks inside the daemon call so the bounded close times out: the tests assert the thread is reported as `thread_leaked`, that the workload still passes, that removal happened without waiting for it, and — after releasing and joining the thread — that the late sample was refused at the source rather than merely missing from a defensive copy. Sample and error buffers are separately bound-tested, and a close that raises still cleans up and persists.

Telemetry tests must be read for thread tracebacks, not just the exit code: a reader thread that dies raises through `threading.excepthook` without failing the run. Treat a clean exit code on a telemetry run as insufficient evidence.

```sh
python3 -m unittest discover -s tests -v
docker build -t evalnoise-workloads:local workloads
docker build -t evalnoise-verifier:local verifiers
docker build -t evalnoise-probe:local probes
EVALNOISE_DOCKER_TESTS=1 python3 -m unittest discover -s tests -v
python3 -m compileall -q evalnoise probes
```

At v0.3 that discovery is 199 tests: 183 unit and 16 real Docker methods, with no skips once `EVALNOISE_DOCKER_TESTS=1` is set. Run the Docker job sequentially and alone, never beside another measurement, because the engine lock will otherwise refuse one of them by design.

The GitHub Actions definition covers Python 3.11 through 3.14 and a real Docker job. A workflow file is not evidence that CI has run. Keep local and remote verification claims separate.

## Experiment Checks

Run calibration and A/A sequentially. Verify exact trial counts, missingness, raw OOM flags, immutable image IDs, profile echoes, and cleanup errors. A successful CLI return alone is insufficient. Inspect the generated HTML on desktop and a narrow viewport; expand both manifest and trial evidence.

Rebuild a report with Docker stopped to exercise offline reconstruction. Test missing trial artifacts with isolated copies, never by deleting evidence from the original run. Any intentional fault-injection artifact must be clearly labeled.

## Future Tests

Additional native Linux and rootless variants; daemon disconnect during creation/start/cleanup; long-running log bounds; disk-full persistence; repository-artifact verifier isolation; provider timeout/retry attribution; statistical interval calibration. SIGKILL lock recovery, controller enforcement, loaded batch/concurrency ordering, and a descriptive sampler overhead contrast now have tests and a recorded run. Concurrent EvalNoise processes are still exercised only through refusal, not measured contention, and interference from other tenants sharing the daemon is not tested at all. A statistically characterised telemetry overhead figure, across hosts and intervals, remains future work.

## Evidence Policy

Record environment, command, run ID, counts, and limitations for measured results. Do not describe scripted fixtures as model evaluations, mocked tests as real engine tests, or successful local tests as a passed remote CI pipeline. Generated runs stay local and ignored by default because future workloads may contain sensitive data.
