# Testing And Validation

## Layers

Unit tests use a fake Docker boundary to check validation, deterministic scheduling, classification, timeouts, cancellation, evidence errors, cleanup, missingness, report escaping, and atomic JSON persistence. These tests cannot prove engine behavior.

Opt-in integration tests use real reviewed fixtures to exercise memory-pressure failure, configured resource echoes, successful memory allocation, ordinary nonzero exits, exit 137 without OOM, timeout intervention, raw sampling, and container removal. The pressure fixture accepts observed OOM, an explicit Python MemoryError, or exit 137 without claiming the latter proves OOM; engine/platform timing can change the available evidence. Classifier unit tests independently require OOM labels to follow observed flags only.

Verification tests cover strict protocol parsing, bounded environment transport, content hashes, pending checkpoints, cancellation, cleanup failures, and verifier scheduling. Real container cases cover correct/wrong/spoofed/missing candidates and crashed, malformed, and timed-out verifiers. Build both images before integration tests.

v0.3 adds four suites. `test_endpoint.py` covers endpoint precedence and pinning, TLS material never reaching an artifact, environment snapshotting, refusal of a mutable remote context and of a missing default socket, API negotiation against a real chunked HTTP server on a Unix socket, the stream's frame/sample/byte bounds, split multi-byte decoding, truncated-EOF detection, cancellation races, nullable counters, and the resource audit — including that `0-1`, `0,1` and `1,0` are the same CPU mask while a malformed, missing, non-string, or unrequested echo fails closed. `test_coordination.py` covers the engine lock across output directories, release on process death by subprocess `SIGKILL`, release after a failure anywhere between acquisition and the final write, orphan rechecking under the lock, plan and image validation, engine outages never reading as a clean run, and adversarial cleanup ownership. `test_telemetry.py` covers sampler wiring in a trial, close-before-removal ordering, telemetry faults not changing an outcome, the enforcement audit, probe evaluation, and the report's fidelity section. `test_recovery_docker.py` is opt-in and real: `SIGKILL` orphan diagnosis and exact-ID cleanup, refusal of a concurrent second run writing to a different output directory, and batch/concurrency ordering under genuine CPU load.

Both telemetry collectors have adversarial coverage. For the CLI snapshot sampler a fixture parks inside the daemon call so the bounded close times out: the tests assert the thread is reported as `thread_leaked`, that the workload still passes, that removal happened without waiting for it, and — after releasing and joining the thread — that the late sample was refused at the source rather than merely missing from a defensive copy. Sample and error buffers are separately bound-tested, and a close that raises still cleans up and persists.

Telemetry tests must be read for thread tracebacks, not just the exit code: a reader thread that dies raises through `threading.excepthook` without failing the run. Treat a clean exit code on a telemetry run as insufficient evidence.

v0.4 adds two suites. `test_provider.py` covers canonical request digests, that any transcript or parameter difference changes the digest, cassette schema strictness including duplicate keys and non-finite numbers, path traversal and size limits, recorded retry replay and exhaustion, `max_attempts` enforcement below a recorded sequence, integer ceiling pricing, the absence of any float in a ledger record, worst-case admission that refuses a call whose observed cost would have fitted, separate call and step ceilings, plan-level refusal, concurrent admission that never exceeds a ceiling, the agent configuration whitelist, and pinned v0.3 config and contract digests recorded from commit `c8ea9ca`. `test_agent.py` covers the loop end to end against a fake engine: one container per tool call with no overlap, the recorded tool sequence, retry provenance, ATIF field names, separate clock domains, deterministic replay across runs, no credential name or value in any argv or artifact, replay with `socket.socket` patched to raise, every non-pass status, offline report regeneration without the cassette, mutated and missing snapshots, ledger totals, and recovery's step-name enumeration. `test_agent_docker.py` is opt-in and real: a tool container emitting a valid observation, a `--network none` container failing to connect, an end-to-end run passing independent verification with audited step containers, the lazy cassette genuinely failing the trusted verifier, and a `SIGKILL`ed agent step leaving a diagnosable owned orphan that exact-ID cleanup removes without touching the manifest status.

`test_compat.py` guards the declared `requires-python = ">=3.11"` floor. Development happens on a newer interpreter, and **`ast.parse(feature_version=(3, 11))` does not catch PEP 701 f-strings** — the 3.12+ tokenizer accepts a nested same-quote f-string whatever `feature_version` says, so that check is a false negative and is not used. The suite tokenizes instead, flagging both constructs that have actually reached this repository: a nested string reusing the enclosing quote, and a backslash inside a replacement field. It also shells out to a real `python3.11` when one is on `PATH`, which is the stronger check: the tokenizer heuristic caught the first construct, and only the real interpreter caught the second.

Budget reconciliation has its own suite. Ceilings are enforced against **committed** figures, which rise at admission and are reconciled to observed figures at settlement, so an outstanding reservation blocks a second admission, a settled call releases what it did not use, and neither a sequential nor an eight-thread concurrent workload can pass a token, cost, call, or attempt ceiling. Provider attempts are reserved *before* the call at the configured per-call maximum, so an exhausted retry sequence cannot escape the run ceiling; that reservation is deliberately conservative. Recorded usage is always written into the ledger and the step metrics before any accounting verdict, so an overrun is an explicit `accounting_errors` entry beside retained evidence rather than discarded evidence. Both the successful and the failed settlement paths return that verdict, and an overrun on either ends the trial as `budget_exhausted`; because the recorded provider caps attempts, the failed-path overrun is injected at the provider boundary. The ledger is also asserted to publish no field claiming to be a retained worst-case total, in both the outstanding and the settled state.

The `--dwell` flag on the tool image is a labelled recovery-test fixture. It delays output without changing it, so a dwelling step replays the same cassette entries as an instant one.

**Always capture the full Docker run to a file.** The opt-in suite carries a known, undiagnosed intermittency recorded in [the validation notes](validation.md): three failures in roughly fifteen full runs, always in the real-container tests. Two of the three are permanently unattributable because the terminal cleared before the traceback was read. Nothing was retried or relaxed to hide it; the only response was to make `Docker.call` report the process exit code and emit an explicit `no stderr diagnostics` marker instead of an empty tail, so a bare `docker <verb> failed: ` can no longer reach a log. That message shape is pinned by a regression test. If the suite fails, keep the log and attribute the failure before rerunning — a passing rerun is not a diagnosis.

```sh
python3 -m unittest discover -s tests -v
docker build -t evalnoise-workloads:local workloads
docker build -t evalnoise-verifier:local verifiers
docker build -t evalnoise-probe:local probes
docker build -t evalnoise-tools:local tools
EVALNOISE_DOCKER_TESTS=1 python3 -m unittest discover -s tests -v
python3 -m compileall -q evalnoise probes tools scripts
```

At v0.4 that discovery is 293 tests: 272 unit and 21 real Docker methods, with no skips once `EVALNOISE_DOCKER_TESTS=1` is set and a `python3.11` interpreter is on `PATH`. At v0.3 it was 199. Run the Docker job sequentially and alone, never beside another measurement, because the engine lock will otherwise refuse one of them by design.

Regenerate the fixture cassettes whenever the tool surface, prompts, or agent parameters change, or the recorded digests will no longer match and every agent trial will become an `agent_error`:

```sh
python3 scripts/record_cassette.py --policy reading --out experiments/cassettes/offline-sum-v1.json
python3 scripts/record_cassette.py --policy lazy --label scripted-lazy-policy --out experiments/cassettes/offline-lazy-v1.json
```

The GitHub Actions definition covers Python 3.11 through 3.14 and a real Docker job. A workflow file is not evidence that CI has run. Keep local and remote verification claims separate.

Note that CI never runs the real-3.11 compile check: no job puts a `python3.11` on `PATH` under that name, so `test_compat.RealInterpreterTests` skips on all five jobs, including `unit (3.11)`. CI does run the whole suite *under* 3.11, which is stronger, but that particular guard is a local-only check. Run the suite with a `python3.11` on `PATH` before publishing.

## Experiment Checks

Run calibration and A/A sequentially. Verify exact trial counts, missingness, raw OOM flags, immutable image IDs, profile echoes, and cleanup errors. A successful CLI return alone is insufficient. Inspect the generated HTML on desktop and a narrow viewport; expand both manifest and trial evidence.

Rebuild a report with Docker stopped to exercise offline reconstruction. Test missing trial artifacts with isolated copies, never by deleting evidence from the original run. Any intentional fault-injection artifact must be clearly labeled.

## Future Tests

Additional native Linux and rootless variants; daemon disconnect during creation/start/cleanup; long-running log bounds; disk-full persistence; repository-artifact verifier isolation; statistical interval calibration. Provider timeout and retry attribution is currently only *simulated* from recorded attempt outcomes; measuring real provider failures needs the authorized real-provider gate. Stateful agent tools, multi-tool turns, and a Harbor environment adapter are untested because they are unimplemented. SIGKILL lock recovery, controller enforcement, loaded batch/concurrency ordering, and a descriptive sampler overhead contrast now have tests and a recorded run. Concurrent EvalNoise processes are still exercised only through refusal, not measured contention, and interference from other tenants sharing the daemon is not tested at all. A statistically characterised telemetry overhead figure, across hosts and intervals, remains future work.

## Evidence Policy

Record environment, command, run ID, counts, and limitations for measured results. Do not describe scripted fixtures as model evaluations, mocked tests as real engine tests, or successful local tests as a passed remote CI pipeline. Generated runs stay local and ignored by default because future workloads may contain sensitive data.
