# EvalNoise

## Local evidence workbench

Run `python3 -m evalnoise workbench --root runs` and open
**http://127.0.0.1:4178** to search experiments, filter outcomes, and inspect agent,
verifier, and infrastructure evidence. This read-only M5 slice has no Docker
execution endpoints. See [usage and limits](docs/workbench.md).

**Measure how execution conditions change evaluation outcomes, without mistaking infrastructure failures for model ability.**

EvalNoise is a research-driven experiment runner for controlled resource-profile comparisons. It preserves the configuration, randomized schedule, image identity, container state, bounded logs, missing trials, and descriptive comparisons in an offline experiment notebook.

**Status: v0.5 adds an offline paired comparison slice on top of the v0.4 agent slice. It is not a finished agent-evaluation platform; the full M3 and M4 gates are both open.** The fixtures are scripted calibration workloads and a small reviewed in-repo agent fixture. They validate plumbing; they do not evaluate an LLM or reproduce a published benchmark result.

v0.5 adds `evalnoise compare`, an offline two-arm paired contrast that is **descriptive only**. Summaries are task-weighted: repetitions of a task are averaged within that task before anything is combined, because they share the task, host, schedule block, and seed and are not independent observations. Compatibility **fails closed**: task, verifier, image, provider, model, and schema identity must match and can never be declared as a treatment, and a resource difference is refused as a confounder unless named with `--treatment`.

`compare` remains descriptive and publishes no bootstrap intervals. A separate
`block-analyze` command now supports a conservative, fixed-horizon conditional
Hoeffding bound for complete two-arm, fixed-suite experiments. It does not revive
the rejected task bootstrap. The target and assumptions are explicit in the
[block protocol](docs/m4-block-protocol.md); the actual 144-trial Docker study and
simulation results are in the [execution record](docs/m4-execution-record.md).
Broader M4 features such as cost frontiers and multiplicity control remain open.

```sh
python3 -m evalnoise run experiments/block-memory.json --trust-config
python3 -m evalnoise block-analyze runs/<run-id> --baseline tight --candidate roomy \
  --treatment memory_mb --output runs/<new-analysis-directory>
```

The study file pins the local ARM64 image used for the recorded experiment.
On another host, build the reviewed `workloads/` image and replace its image IDs
before starting your own study. Output directories must be new; analysis never
overwrites a previous report. The protocol and horizon must be chosen before
observing results, not optimized to exclude zero.

## Why This Exists

An evaluation score depends on more than a model: the executor, resource budget, timeout, scheduler, verifier, and environment also matter. Anthropic's [infrastructure-noise study](https://www.anthropic.com/engineering/infrastructure-noise) reported a six-percentage-point difference across resource configurations in its Terminal-Bench experiment. That motivates investigating execution conditions, not assuming the same effect in every benchmark.

EvalNoise asks a narrower, auditable question first: **when we run the same trusted workload under specified resource ceilings, which observed outcomes change?**

## Implemented

- Strict, versioned JSON configurations with bounded inputs and argv-only commands.
- Repeated, seeded experiments with randomized profile order and matched task/repetition pairs.
- Fresh containers using pre-resolved local image IDs; no pulls during measurement.
- CPU and RAM ceilings, swap disabled, bounded concurrency, explicit timeout intervention.
- Non-root, read-only, network-disabled containers without host mounts or added capabilities.
- Separate setup, runtime, timeout, OOM-observed, workload-failure, cancellation, and unknown outcomes.
- Atomic per-trial JSON and a manifest that preserves incomplete runs.
- Optional raw Docker CLI samples, explicitly not peak-memory measurements.
- Offline HTML, JSON summaries, and CSV exports with explicit denominators.
- Descriptive paired comparisons over tasks, within one run or across two, with fail-closed identity validation, pair-identity loss accounting, and no published uncertainty.
- Optional independent verifier containers with separate resource budgets and data-only artifact handoff.
- Task/verifier content hashes, execution-versus-correctness outcomes, and interruption-safe verification checkpoints.
- Advisory per-daemon run coordination that survives `SIGKILL`, plus read-only diagnosis and ownership-checked cleanup by resolved container ID.
- Bounded, cancel-before-cleanup telemetry collectors that detach rather than mutating evidence late or blocking container removal.
- Unit tests, opt-in real Docker tests, and GitHub Actions across Python 3.11-3.14.

## Quick Start

Requirements: Python 3.11+, Docker CLI and a running Linux-container engine with functioning cgroups. Docker Desktop works for development, but its VM is part of the measured environment. Use only workloads and images you have reviewed and trust.

Run from the project directory, without installing Python packages:

```sh
python3 -m evalnoise doctor
docker build -t evalnoise-workloads:local workloads
python3 -m evalnoise validate experiments/calibration.json
python3 -m evalnoise plan experiments/calibration.json
python3 -m evalnoise run experiments/calibration.json --trust-config
```

The final command prints the output directory and report path. Open that `report.html` in a browser. It works offline and contains the raw trial evidence. The initial image build downloads public image layers; trials have no network access and do not invoke paid model APIs.

Run the identical-profile control separately, not concurrently with calibration:

```sh
python3 -m evalnoise run experiments/aa-control.json --trust-config
```

Rebuild a report from existing evidence, without Docker:

```sh
python3 -m evalnoise report runs/<experiment-run-id>
```

Compare two profiles offline. Both arms are named before any number exists; there is no mode that searches for the largest difference:

```sh
python3 -m evalnoise compare runs/<run-id> --baseline tight --candidate roomy \
    --treatment memory_mb --output runs/cmp-v5b/calibration
python3 -m evalnoise compare runs/<run-a> runs/<run-b> --baseline standard --candidate standard
```

Omit `--output` to print the JSON and write nothing; `--output` writes into a directory you name and never into either run. A field that differs without a matching `--treatment` exits 2 and names the flag that would declare it. The configured seed is identity, not a treatment: a seed mismatch is fatal, and every trial seed is checked against its plan and against its counterpart, because a repetition index is a label while the seed determines the workload. Cross-run contrasts additionally require persisted per-task contract hashes and a complete, stable engine identity on both manifests, so artifacts written before those existed are refused rather than having their identity guessed.

## Measurement Fidelity And Recovery

Optional raw Engine telemetry requires a local Unix socket endpoint. Set `sample_interval_s` on the experiment or on an individual profile; it is a retention filter over the daemon's own sample timestamps, not a request for a sampling rate. Remote endpoints keep the coarser `docker stats` snapshot and say so in the report.

Sampling is **off by default**. Run `sampler-overhead-e60d93977bd0` recorded 20 of 20 paired trials, a `sampler-off` successful median of 3.561533 s against a `sampler-on` median of 3.5607595 s over 10 pairs each, with 40 samples received, 20 retained, and zero leaked reader threads. The two medians differ by under a millisecond and the sampled arm is nominally the faster one, so the contrast resolves no overhead in either direction. It is a descriptive record of one run on one host with no statistical test attached, and deliberately not a zero-overhead claim. Both collectors are bounded and cancel before container removal; a collector that cannot be stopped is detached and reported as `thread_leaked` rather than being allowed to change a workload outcome or delay cleanup.

Run the offline agent slice. Nothing is sent and nothing is charged:

```sh
docker build -t evalnoise-tools:local tools
python3 -m evalnoise run experiments/agent-offline.json --trust-config
```

Each trial replays `list_files -> read_file -> final_answer` from a recorded cassette, runs the two tool calls as separate hardened containers, and hands the final answer to the existing trusted `sum-v1` verifier. The report states plainly that no provider request was made and prints the simulated usage beside the zero actual charge. Regenerate the cassettes with `scripts/record_cassette.py` whenever the tool surface, prompts, or agent parameters change.

Check what limits the engine really applied, using a separate reviewed image rather than touching the workload:

```sh
docker build -t evalnoise-probe:local probes
python3 -m evalnoise probe experiments/calibration.json --trust-config
```

After a hard kill or daemon outage, diagnose before removing anything. Diagnosis never writes to a run directory, and cleanup only touches containers this run owns:

```sh
python3 -m evalnoise diagnose runs/<experiment-run-id>
python3 -m evalnoise cleanup  runs/<experiment-run-id> --confirm
```

One EvalNoise run holds an advisory lock per daemon ID, so a second run against the same engine refuses to start even from a different output directory. That is cooperation between EvalNoise processes for one local user; it is not a distributed lock and does not exclude other users, tools, or general host load.

Optional installation: `python3 -m pip install -e .` exposes the `evalnoise` command. Runtime dependencies are standard-library only; installation uses setuptools as the build backend.

## Independent Verification

Build the separate trusted verifier image, then run the known-answer fixtures:

```sh
docker build -t evalnoise-workloads:local workloads
docker build -t evalnoise-verifier:local verifiers
python3 -m evalnoise run experiments/verified.json --trust-config
```

All three candidate programs exit successfully. Only the correct answer passes verification. The wrong answer and a candidate that prints its own positive verdict both fail the trusted check. This demonstrates an execution/correctness distinction, not an agent benchmark.

Verifiers run sequentially **after each entire workload batch**, never beside measured workloads. A verifier crash, timeout, malformed response, or cleanup failure produces `verifier_error`, not an incorrect-answer verdict. Missing or invalid candidate output produces `artifact_error`. [Read the verifier contract](docs/verification.md) before implementing a task.

## Experiments And Interpretation

`calibration.json` repeats three workloads under 48 MiB and 256 MiB memory ceilings. The memory fixture intentionally allocates more than 48 MiB. CPU hashing and a tiny repository test fixture provide additional execution checks. This is a positive control for memory pressure, not a representative coding benchmark.

`aa-control.json` compares two differently named profiles with identical settings. A matching result checks basic consistency; it does not prove absence of measurement noise.

Profile batches run sequentially. Within a batch, `concurrency` controls the number of workers. A profile with concurrency one cannot produce a contention experiment by itself. Concurrent profiles also require enough tasks to fill their workers. Both properties are checked against engine-reported container timestamps under real CPU load: trials within a profile overlap, and profile batches do not. Since v0.3 a run holds an advisory per-daemon lock, so another EvalNoise process is refused rather than silently competing — but that lock does not exclude unrelated host work, other users, or other tools, so avoid those during measurement.

The report's pass rate is clean final passes divided by **recorded** trials, including recorded infrastructure failures, verification errors, cancellations, and pending verification in that denominator. Missing trials are displayed separately. Paired deltas exclude missing, cancelled, and pending-verification pairs. Neither quantity is a model score. Successful-duration medians describe workloads only, excluding verifier time, and can compare different survivor sets; they are not unconditional speedups. Per-task rows distinguish exit-code and independently verified contracts.

## Documentation

- [Research and source assessment](docs/research.md): why this is required and what existing work establishes.
- [Product requirements](docs/product.md): users, use cases, requirements, and non-goals.
- [Architecture and decisions](docs/architecture.md): execution flow, modules, and design tradeoffs.
- [Experiment methodology](docs/methodology.md): controls, estimands, the cluster sampling unit, the evidence policy, confounders, and interpretation.
- [Data contract](docs/data-contract.md): configuration, artifacts, outcomes, and compatibility.
- [Independent verification](docs/verification.md): protocol, trusted verifier authoring, lifecycle, and scope.
- [Security and operations](docs/security.md): trust boundary, containment, cleanup, and sharing.
- [Roadmap](docs/roadmap.md): milestone gates for the larger platform.
- [M3 offline slice plan](docs/m3-plan.md): the agent loop, the Harbor decision, and the open gate.
- [Testing](docs/testing.md): verification commands and evidence requirements.
- [Local validation record](docs/validation.md): actual test results, experiment IDs, and unverified environments.
- [Contributing](CONTRIBUTING.md): how to extend the project without weakening measurement integrity.

## Tests

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q evalnoise probes tools scripts
EVALNOISE_DOCKER_TESTS=1 python3 -m unittest discover -s tests -v
```

Discovery is now 414 tests: 393 unit and 21 real Docker methods. The first command reports `Ran 414 tests ... OK (skipped=21)`, which is **393 executed**, not 414 passed — the 21 skips are exactly the opt-in Docker methods, and they run only under the final command. Published v0.4 was 293, plus 66 for the experimental `codex-check` suite gives 359; the 55 added for v0.5 are the comparison and resampling suites, neither of which needs Docker. v0.3 ran 199; those M0-M2 tests keep their semantics. The final command creates and removes real containers using the previously built images, removes only containers it created, and never prunes. Run it sequentially and alone — a second concurrent run is refused by the engine lock by design. Read its stderr for thread tracebacks rather than trusting the exit code, because a telemetry reader thread can die through `threading.excepthook` without failing the run. Check [GitHub Actions](https://github.com/AnkitPorwal04/evalnoise/actions) for the result of the specific commit; local success is not proof of remote CI success.

## Current Limits

No live provider client in the measurement core, no credential file is ever read by EvalNoise, no Harbor integration, no repository/patch artifact transfer, no API proxy, distributed workers, signed artifacts, web control plane, or multi-tenant isolation yet. Verifier and tool input is a bounded JSON value, not an arbitrary filesystem or archive. Agent tools are a fixed, stateless, read-only table with no shell and no expression evaluation, because every step is a fresh container; stateful tools are deferred. Provider retry and timeout attribution is simulated from recorded outcomes, not measured. The budget ledger is synthetic and its prompt estimate is a character heuristic, not a provider tokenizer, so it is not a spending control for a real provider. No resume. CPU quotas are not dedicated CPUs. Host caches and other workloads are uncontrolled. CLI polling and optional sampling add overhead. Timeouts are best-effort host deadlines, not real-time guarantees. Local evidence can be edited and logs may contain secrets. Paired comparisons are descriptive only and publish no uncertainty: the configured suite is a fixed set of scripted tasks rather than a random sample, and the prototype resampler failed its own coverage characterisation, so intervals are withheld pending independent methodology review. Uncertainty, time-block sensitivity, multiple-comparison policy, power analysis, and independent review are all open, so the M4 gate is open.

Measurement fidelity — engine identity, same-engine run coordination, enforcement probes, hard-kill recovery, and loaded batch ordering — is implemented and recorded in the [validation log](docs/validation.md). The agent offline slice is implemented and recorded there too; its full M3 gate stays open until an explicitly authorized and budgeted real-provider run, a public reviewed task subset, and measured retry attribution exist. A larger dashboard comes after those foundations, not instead of them.
