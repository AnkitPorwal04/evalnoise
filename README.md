# EvalNoise

**Measure how execution conditions change evaluation outcomes, without mistaking infrastructure failures for model ability.**

EvalNoise is a research-driven experiment runner for controlled resource-profile comparisons. It preserves the configuration, randomized schedule, image identity, container state, bounded logs, missing trials, and descriptive comparisons in an offline experiment notebook.

**Status: v0.1 foundation, not a finished agent-evaluation platform.** The current fixtures are scripted calibration workloads. They validate measurement plumbing; they do not evaluate an LLM or reproduce a published benchmark result.

Licensing has not been selected yet. This local scaffold is not presented as a licensed open-source release.

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
- Unit tests, opt-in real Docker tests, and a proposed CI matrix.

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

Optional installation: `python3 -m pip install -e .` exposes the `evalnoise` command. Runtime dependencies are standard-library only; installation uses setuptools as the build backend.

## Experiments And Interpretation

`calibration.json` repeats three workloads under 48 MiB and 256 MiB memory ceilings. The memory fixture intentionally allocates more than 48 MiB. CPU hashing and a tiny repository test fixture provide additional execution checks. This is a positive control for memory pressure, not a representative coding benchmark.

`aa-control.json` compares two differently named profiles with identical settings. A matching result checks basic consistency; it does not prove absence of measurement noise.

Profile batches run sequentially. Within a batch, `concurrency` controls the number of workers. A profile with concurrency one cannot produce a contention experiment by itself. Concurrent profiles also require enough tasks to fill their workers. Avoid unrelated host work and other EvalNoise processes during measurement; v0.1 does not acquire a daemon-wide experiment lock.

The report's pass rate is clean passes divided by **recorded** trials, including recorded infrastructure failures and cancellations in that denominator. Missing trials are displayed separately. Paired deltas exclude missing/cancelled pairs. Neither quantity is a model score. Successful-duration medians can compare different survivor sets and must not be interpreted as unconditional speedups.

## Documentation

- [Research and source assessment](docs/research.md): why this is required and what existing work establishes.
- [Product requirements](docs/product.md): users, use cases, requirements, and non-goals.
- [Architecture and decisions](docs/architecture.md): execution flow, modules, and design tradeoffs.
- [Experiment methodology](docs/methodology.md): controls, estimands, confounders, and interpretation.
- [Data contract](docs/data-contract.md): configuration, artifacts, outcomes, and compatibility.
- [Security and operations](docs/security.md): trust boundary, containment, cleanup, and sharing.
- [Roadmap](docs/roadmap.md): milestone gates for the larger platform.
- [Testing](docs/testing.md): verification commands and evidence requirements.
- [Local validation record](docs/validation.md): actual test results, experiment IDs, and unverified environments.
- [Contributing](CONTRIBUTING.md): how to extend the project without weakening measurement integrity.

## Tests

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q evalnoise
EVALNOISE_DOCKER_TESTS=1 python3 -m unittest discover -s tests -v
```

The final command creates and removes real containers using the previously built image. CI definitions exist, but remote CI has not run until this project is placed in a repository with Actions enabled.

## Current Limits

No model/provider adapters, Harbor integration, independent verifier process, API proxy, distributed workers, signed artifacts, statistical confidence intervals, web control plane, or multi-tenant isolation yet. No retries or resume. CPU quotas are not dedicated CPUs. Host caches and other workloads are uncontrolled. CLI polling and optional sampling add overhead. Timeouts are best-effort host deadlines, not real-time guarantees. Local evidence can be edited and logs may contain secrets.

The intended next step is an independent task/verifier contract, followed by measured agent integration. A larger dashboard comes after those foundations, not instead of them.
