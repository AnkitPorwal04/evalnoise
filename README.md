# EvalNoise

**Measure how execution conditions change evaluation outcomes—and preserve the evidence needed to explain them.**

[![Verify EvalNoise](https://github.com/AnkitPorwal04/evalnoise/actions/workflows/tests.yml/badge.svg)](https://github.com/AnkitPorwal04/evalnoise/actions/workflows/tests.yml)

EvalNoise runs reviewed workloads under different CPU, memory, timeout and concurrency settings. It records execution state, checks answers in independent verifier containers, and produces offline reports and a local investigation workbench.

It helps answer: **did a task produce a wrong answer, exhaust its resources, time out, or fail in the execution infrastructure?** A successful process exit is not automatically a correct answer, and an OOM event is not proof of poor model reasoning.

**Current version: 0.6.0, research/development release.** The repository is public; no hosted execution service or PyPI package is provided. The included workloads are synthetic, known-answer fixtures, not an LLM leaderboard. Live-provider evaluation and broader statistical validation remain unfinished. See [limitations](#limitations-and-trust-boundaries) before using the results.

## Start here

- **Try a local experiment:** follow the [quick start](#quick-start).
- **Understand the motivation:** read the [48-trial case study](docs/demonstration-results.md).
- **Inspect your own runs:** start the [read-only workbench](#inspect-and-compare-results).
- **Write a workload or verifier:** use the [configuration contract](docs/data-contract.md) and [verifier guide](docs/verification.md).
- **Evaluate the coordinator pilot:** read the [separate runbook](docs/cluster.md); it is not needed for ordinary experiments.

## What you get

- Seeded repetitions and randomized profile order, with explicit task/repetition pairing.
- Fresh, non-root containers with a read-only root filesystem, disabled networking, bounded resources and no host mounts.
- Image IDs resolved before measurement; no image pulls during trials.
- Independent output verification, with execution and correctness outcomes kept separate.
- Per-trial JSON, run manifests, bounded logs, resource audits and optional raw Docker Engine telemetry.
- Offline HTML/JSON/CSV reports that preserve missing and incomplete observations.
- Paired descriptive comparisons that refuse incompatible task, verifier, image or seed identities.
- A loopback-only workbench with filters, outcome matrices, provenance differences, resource plots, traces and filtered exports.
- Ownership-checked recovery tools and an optional authenticated local coordinator pilot.

## Requirements

- **Python 3.11–3.14**; the measurement package has no third-party runtime dependencies.
- **Git** to clone this repository.
- **Docker CLI and a running Linux-container engine** to execute workloads. Docker Desktop is suitable for local development; its VM is part of the measured environment.
- Working cgroup resource limits and sufficient RAM/disk space for your chosen profiles and concurrency.

The documented shell commands target macOS or Linux. Native Windows execution is not validated; use a Linux environment with a working Docker engine. Viewing existing evidence and running offline comparisons do not require Docker. Image builds and Python build tooling may need network access; the supplied workload trials do not call model APIs.

## Quick start

### 1. Clone and install

```sh
git clone https://github.com/AnkitPorwal04/evalnoise.git
cd evalnoise
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
evalnoise --help
```

Keep subsequent commands in the repository root: the example configurations, Dockerfiles and study scripts are checkout resources, not wheel-installed assets. Alternatively, skip installation and replace `evalnoise` with `python3 -m evalnoise` while in the checkout.

### 2. Check Docker and build the calibration image

```sh
evalnoise doctor
docker build -t evalnoise-workloads:local workloads
```

Review the Dockerfile and workload code before running them. `--trust-config` is an acknowledgement that you trust the configured images and commands, not a guarantee of hostile-code isolation.

### 3. Validate, inspect the schedule, and run

```sh
evalnoise validate experiments/calibration.json
evalnoise plan experiments/calibration.json
evalnoise run experiments/calibration.json --trust-config
```

This example schedules 18 trials: three workloads, two memory profiles and three repetitions. The memory-allocation fixture is intentionally larger than the tight profile's limit, so non-passing outcomes are expected. Do not increase limits merely to make all results pass.

The final command prints JSON containing the generated run directory, run status and `report.html` path. **An exit code of zero means the experiment completed—not that every workload passed.** Keep the printed run directory for subsequent commands.

### 4. Open the workbench

```sh
evalnoise workbench --root runs --port 4178
```

Open **http://127.0.0.1:4178** on the same machine. Stop the server with Ctrl+C. The viewer reads local artifacts and has no execution controls. A fresh checkout has no historical runs; create one first using the preceding steps.

## Inspect and compare results

Replace `RUN_DIRECTORY` below with the directory printed by your run. `NEW_COMPARISON_DIRECTORY` must not already exist; for example, `runs/first-comparison`.

```sh
evalnoise compare RUN_DIRECTORY --baseline tight --candidate roomy \
  --treatment memory_mb --output NEW_COMPARISON_DIRECTORY
```

Omit `--output` to print JSON without writing comparison files. Comparisons are **descriptive**, not causal effects or model capability scores. They retain planned, recorded, missing and eligible-pair counts. Undeclared resource differences are refused; identity differences such as task, verifier, image or seed cannot be treated as resource changes.

To compare compatible profiles in two different runs:

```sh
evalnoise compare BASELINE_RUN CANDIDATE_RUN \
  --baseline standard --candidate standard
```

Cross-run comparisons additionally require complete, stable engine identity and matching contract evidence. Separate runs do not share a time block simply because their repetition numbers match.

Each run already includes an offline report. If you intentionally want to regenerate its **derived** HTML, JSON summary and CSV using the current code:

```sh
evalnoise report RUN_DIRECTORY
```

This rewrites derived report files in that directory; it does not rewrite the manifest or trial JSON. Preserve original derived outputs first if you need byte-for-byte historical reports.

## Try independent answer verification

```sh
docker build -t evalnoise-workloads:local workloads
docker build -t evalnoise-verifier:local verifiers
evalnoise run experiments/verified.json --trust-config
```

The correct, wrong and spoofed-verdict fixtures all exit successfully. Only the correct answer should pass the independent verifier. Verifiers execute **after the entire workload batch**, with a separate budget and data-only handoff. A verifier crash or invalid response is a `verifier_error`, not proof of a wrong answer.

See the [verifier protocol](docs/verification.md) for the exact JSON envelope and trust model.

## Reproduce the 48-trial demonstration

The [case study](docs/demonstration-results.md) distinguishes resource failures from wrong answers: its measured run recorded three OOM kills and twelve exit-zero answers rejected by verification. These are observations on one host, not universal expected counts or a public benchmark score.

```sh
docker build -t evalnoise-demo-workloads:local workloads
docker build -t evalnoise-demo-verifier:local verifiers
mkdir -p runs
python -m scripts.prepare_demo \
  --workload evalnoise-demo-workloads:local \
  --verifier evalnoise-demo-verifier:local \
  --output runs/my-demo-config
evalnoise run runs/my-demo-config/experiment.json --trust-config
```

Preparation resolves your locally built images to immutable IDs. Do not copy workstation-specific image IDs from historical records. After the run, use its printed directory:

```sh
python -m scripts.analyze_demo RUN_DIRECTORY --output runs/my-demo-analysis
python -m scripts.static_demo RUN_DIRECTORY --output runs/my-static-demo
```

Both output directories must be new. The static exporter accepts only the reviewed 48-cell design and known outcome vocabulary; unfamiliar outcomes require review rather than silent exclusion. It emits a standalone `index.html` and selected `evidence.json`, without raw logs, machine paths or credentials. Open the HTML locally. These commands do not deploy anything.

## Other workflows

**Identical-profile control:** run `evalnoise run experiments/aa-control.json --trust-config` separately from other experiments. Matching results are a useful check, not proof that measurement noise is absent.

**Recorded agent loop:** build both required images before replaying the fixture:

```sh
docker build -t evalnoise-tools:local tools
docker build -t evalnoise-verifier:local verifiers
evalnoise run experiments/agent-offline.json --trust-config
```

This replays recorded responses with stateless read-only tools and independent final verification. Its usage/cost ledger is synthetic: no provider is contacted. It is not live agent evaluation.

**Resource probes:** build `evalnoise-probe:local` from `probes/`, then run `evalnoise probe experiments/calibration.json --trust-config`. Probes inspect their own container limits separately from measured workloads. CPU quotas and affinity masks do not reserve physical CPUs.

**Interruption recovery:** inspect first; remove only artifacts you own:

```sh
evalnoise diagnose RUN_DIRECTORY
evalnoise cleanup RUN_DIRECTORY --confirm
```

Cleanup checks container identity and ownership. It does not resume the run, invent missing verdicts, or prune unrelated containers. A per-engine advisory lock prevents overlapping cooperating runs for one local user; it does not exclude other users or unrelated host load.

**Coordinator pilot:** [owner-scoped jobs, workers, leases, quotas and HMAC receipts](docs/cluster.md) are available through separate `cluster-*` commands. Follow its catalog and credential provisioning instructions. It binds to loopback, accepts reviewed immutable-image catalog entries, and is not a public SaaS or adversarial multi-tenant deployment.

**Block analysis:** `block-analyze` is a separate experimental fixed-horizon method, not a replacement for descriptive `compare`. Read the [protocol and assumptions](docs/m4-block-protocol.md) before choosing a horizon or analyzing data. The historical `block-memory.json` contains host-specific image IDs and must be adapted before use elsewhere.

## Understanding the evidence

A run directory contains a manifest, individual trial records and derived reports. Depending on the workflow, it also contains verifier or tool-step evidence. The [data contract](docs/data-contract.md) defines the fields and statuses.

- `passed`: the applicable exit-code or independent-verification contract passed. Inspect the task contract to know which.
- `verification_failed`: the independent verifier rejected an otherwise successful candidate execution.
- `oom_killed`: an OOM kill was observed by the engine; exit code 137 alone is not sufficient evidence.
- `timeout`, setup/runtime errors and verifier errors describe different failure paths; do not collapse them into wrong answers.
- Missing, cancelled and pending observations remain visible. Recorded pass rates and complete-pair comparisons use different denominators.
- Timing summaries can condition on different passing subsets. They are not unconditional speedups or throughput estimates.
- Raw sampled telemetry is not a peak measurement; missing data is not zero. Sampling is opt-in and adds overhead.

## Tests and installation checks

Run ordinary checks from the checkout:

```sh
python -m unittest discover -s tests -v
python -m compileall -q evalnoise probes tools scripts workloads verifiers
python -m scripts.check_install
```

The install check creates a temporary virtual environment, installs a built wheel, and checks the installed CLI and web assets outside the source tree. Build dependencies may be downloaded; no model or Docker workload is launched by that check.

For real Docker regression tests, build all four test images first and run alone:

```sh
docker build -t evalnoise-workloads:local workloads
docker build -t evalnoise-verifier:local verifiers
docker build -t evalnoise-probe:local probes
docker build -t evalnoise-tools:local tools
EVALNOISE_DOCKER_TESTS=1 python -m unittest discover -s tests -v
```

Browser tests use a test-only dependency:

```sh
python -m pip install playwright==1.58.0
python -m playwright install chromium
EVALNOISE_BROWSER_TESTS=1 python -m unittest discover -s tests \
  -p 'test_workbench_browser.py' -v
```

On Linux, browser system libraries may require `python -m playwright install --with-deps chromium`. Default test discovery skips opt-in integrations and unavailable platform checks; read the executed/skipped counts rather than treating every discovered test as executed. [CI](https://github.com/AnkitPorwal04/evalnoise/actions) separately checks Python 3.11–3.14, Docker, Chromium and clean installation.

## Troubleshooting

- **Docker unavailable:** start the engine in Linux-container mode, then run `evalnoise doctor`. The viewer and offline analysis can still work without Docker.
- **Image not found:** build the named image on the selected engine and confirm it with `docker image inspect IMAGE`. Image resolution failures stop preflight; EvalNoise does not silently pull or retry.
- **Output directory already exists:** choose a new analysis/export directory. Do not delete measured evidence just to rerun a command.
- **Engine already in use:** wait for the cooperating run to finish. Diagnose owned leftovers after a crash; do not bypass the lock to force overlapping measurements.
- **Empty workbench:** verify `--root` points to a directory containing run subdirectories with manifests. Historical workstation runs are intentionally not committed.
- **Comparison refused:** read the mismatch and review the contracts. Do not declare an identity difference as a treatment or edit raw evidence to make it pass.
- **Subscription check blocked:** the experimental `codex-check` is not a validated live-provider adapter. It currently refuses execution when its pre-model tool boundary cannot be verified; it does not fall back to API-key billing.

## Limitations and trust boundaries

Use only images, task configurations and verifiers you have reviewed. Containers and read-only filesystems are not a complete hostile-code security boundary. Docker access is privileged. Raw logs and manifests may contain private data; review exported evidence before sharing it. Keep coordinator credentials and state outside version control.

The public repository does **not** imply a public hosted demo, PyPI distribution, production support commitment or completed research validation. Live-provider validation, broader statistical review, power/multiplicity analysis, hostile-worker isolation, production networking, key rotation and high availability remain open. HMAC receipts are symmetric integrity checks, not public-key signatures or remote attestation. See the [roadmap](docs/roadmap.md) and [release-readiness boundaries](docs/release-readiness.md).

## Documentation and contributing

- [Research motivation and primary sources](docs/research.md)
- [Architecture and decisions](docs/architecture.md)
- [Experiment methodology](docs/methodology.md)
- [Configuration and artifact contract](docs/data-contract.md)
- [Verifier authoring](docs/verification.md)
- [Workbench guide](docs/workbench.md)
- [Coordinator operations](docs/cluster.md) and [deployment threat model](docs/m6-design.md)
- [Security and recovery](docs/security.md)
- [Testing guide](docs/testing.md) and [validation history](docs/validation.md)
- [Contributing](CONTRIBUTING.md)

For a bug report, include the commit, Python/Docker versions, command, a minimal reviewed configuration, and redacted diagnostics. Never attach credentials or an unreviewed raw run bundle. Preserve failing evidence and identify skipped tests; do not hide failures with retries or relaxed assertions.

## License

**No license has been selected yet.** Public visibility is not an open-source license or a grant of general reuse, modification or redistribution rights. Contact the maintainer through GitHub before uses requiring permission. A license will be added explicitly rather than inferred from the repository being public.
