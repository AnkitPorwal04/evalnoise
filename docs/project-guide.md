# EvalNoise: how it works and what we have built

This guide explains the project from the beginning, walks through your first
experiment, and separates demonstrated achievements from unfinished goals.
It describes the **v0.6.0 implementation**, now publicly available under the
[MIT License](../LICENSE). It is not a claim that every roadmap milestone is
production-ready.

## 1. Understand the problem

Imagine two runs of the same task. One succeeds; the other fails. Before blaming
the program or model, ask:

- Did both runs have the same memory and CPU limits?
- Did one time out or encounter a Docker error?
- Did the program finish successfully but return a wrong answer?
- Did the checker itself fail?
- Are we comparing all planned observations or only the survivors?

EvalNoise records the evidence needed to investigate those questions. Its current
demonstrations use reviewed synthetic programs, **not live LLM evaluations**.
The goal is trustworthy measurement infrastructure before broader agent studies.

## 2. Learn the five building blocks

1. **Task:** the image and command to execute. A task can also declare an
   independent verifier.
2. **Profile:** execution conditions, such as 1 CPU, 48 MiB memory, a 15-second
   timeout and concurrency one.
3. **Repetition:** another scheduled observation of a task under the profiles.
   Repetitions are paired by their recorded identities and seeds; they are not
   automatically independent samples.
4. **Trial:** one task executed under one profile in one repetition.
5. **Run:** the complete experiment plan and its collected trial evidence.

For example, the calibration configuration has three tasks, two profiles and
three repetitions: **3 × 2 × 3 = 18 planned trials**.

## 3. Follow what happens inside one run

```text
Reviewed JSON configuration
        |
        v
Validate fields, bounds and experiment plan
        |
        v
Resolve Docker endpoint, engine identity and local image IDs
        |
        v
Acquire cooperating-run lock and persist the manifest
        |
        v
For each scheduled profile batch:
    create fresh workload containers
    -> audit requested resource settings before starting
    -> execute with bounded concurrency
    -> observe state, logs, timing and optional telemetry
    -> remove owned containers and persist trial records
    -> run independent verifiers for eligible successful workloads
        |
        v
Finalize run evidence and generate HTML / JSON / CSV reports
```

### Validation happens before measurement

The configuration parser rejects unknown fields, malformed identifiers and
out-of-range values. Commands are argument arrays, not interpolated host-shell
strings. Images must already exist on the selected engine; the runner resolves
their identities before measured trials and does not silently pull or retry.

The seeded plan randomizes profile order between repetitions. Profile batches
execute sequentially; `concurrency` controls parallel trials **inside** a batch.
Changing concurrency can therefore change contention, but it is not automatically
a measurement of throughput.

### Workloads run under explicit constraints

Containers use a non-root user, a read-only root filesystem, disabled networking,
dropped capabilities and bounded CPU, memory, process count and temporary storage.
There are no workload host mounts. An engine configuration audit runs before start.

These measures are for reviewed code, not a complete hostile-code security boundary.
A CPU quota is a ceiling, not a reserved processor. Other host activity and Docker
Desktop's VM can still affect observations.

### Execution and correctness are separate

Without a verifier, a task's success contract is its expected process exit code,
subject to the recorded runtime/OOM conditions. With a verifier:

1. The candidate must execute successfully and emit a valid bounded JSON artifact.
2. Its trial is persisted as `pending_verification`, not prematurely passed.
3. After the whole workload batch, a separate trusted verifier container checks it.
4. A valid positive verdict becomes `passed`; a negative verdict becomes
   `verification_failed`.
5. A broken verifier becomes `verifier_error`, not a wrong-answer verdict.

A candidate cannot pass simply by printing its own positive verdict. Conversely,
this public known-answer verifier is not hidden-test security against malicious code.

### Evidence survives incomplete execution

The runner stores observations per trial rather than waiting to write everything
at the end. Missing trials remain missing; cancellation does not manufacture an
answer. Recovery checks ownership before removing leftover containers and does
not resume an interrupted experiment automatically.

## 4. Install from a fresh clone

Use Python 3.11–3.14 and a running Linux-container Docker engine. The following
commands target macOS or Linux:

```sh
git clone https://github.com/AnkitPorwal04/evalnoise.git
cd evalnoise
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
evalnoise --help
```

Stay in the checkout for the example configurations, Dockerfiles and scripts.
The installed wheel contains the package and workbench asset, not the example
images. There is no PyPI release to install by name.

## 5. Run your first experiment

Review `workloads/Dockerfile`, `workloads/workload.py` and
`experiments/calibration.json`, then:

```sh
evalnoise doctor
docker build -t evalnoise-workloads:local workloads
evalnoise validate experiments/calibration.json
evalnoise plan experiments/calibration.json
evalnoise run experiments/calibration.json --trust-config
```

`validate` should report 18 planned trials. `plan` lets you inspect their schedule
before execution. `run` prints its actual directory, completion status and report
path. Record that directory; later examples call it `RUN_DIRECTORY`.

The memory fixture deliberately exceeds the tight profile's limit. A non-passing
trial is expected evidence, not necessarily a broken installation. **A completed
run and CLI exit zero do not mean every task passed.**

## 6. Read the artifacts

The generated directory has this general structure:

```text
runs/<experiment-name>-<run-id>/
  manifest.json       configuration, plan, images, engine and run status
  trials/             one JSON record per recorded workload trial
  verification/       verifier execution records, when applicable
  summary.json        derived outcome counts and summaries
  trials.csv          derived flat trial export
  report.html         offline evidence report
```

Start with the manifest to confirm **what was intended**, then trial JSON to see
**what was observed**. Use the reports for navigation rather than treating them
as substitutes for raw evidence.

Key distinctions to inspect:

- `execution_status` versus final `status` for verified tasks.
- The engine's `OOMKilled` flag; exit 137 alone does not establish an OOM kill.
- Planned, recorded, missing and eligible-pair counts.
- Workload container duration versus verifier or host-side agent time.
- Telemetry errors, cleanup errors and missing measurements.

Raw telemetry contains samples, not guaranteed peaks. An unavailable measurement
is not zero. Sampling is opt-in and can itself add overhead.

## 7. Investigate visually

```sh
evalnoise workbench --root runs --port 4178
```

Open **http://127.0.0.1:4178** on the same machine:

1. Select a run from the library.
2. Filter by task, profile or outcome.
3. Inspect the outcome matrix and planned/recorded counts.
4. Open a trial's logs, verifier evidence, resource audit or agent steps.
5. Inspect raw resource timelines where samples exist.
6. Compare two manifests' provenance before interpreting their numbers.
7. Download a filtered JSON export, retaining its subset information.

Live refresh is opt-in polling. The viewer is read-only and has no Docker execution
API. Stop it with Ctrl+C. A fresh clone has no historical run data until you create
your own runs.

## 8. Compare the profiles

Replace `RUN_DIRECTORY` with your printed run path:

```sh
evalnoise compare RUN_DIRECTORY --baseline tight --candidate roomy \
  --treatment memory_mb --output runs/my-first-comparison
```

Choose a new output directory. The command checks compatibility and writes separate
comparison artifacts without modifying either source run. Repetitions are averaged
within tasks before the task-weighted summary is formed.

`compare` publishes descriptive differences, not p-values or bootstrap confidence
intervals. Its passing-duration comparison uses jointly passing observations,
which may be a selected subset. Do not interpret that as unconditional speedup.

The separate `block-analyze` method has a narrower fixed-horizon conditional target.
Read its [protocol](m4-block-protocol.md) first; its existence does not make every
comparison inferentially validated.

## 9. See why independent verification matters

```sh
docker build -t evalnoise-verifier:local verifiers
evalnoise run experiments/verified.json --trust-config
```

This uses the workload image built in step 5. All candidate programs exit
successfully, but only the correct answer should pass the independent checker.
Wrong output and a spoofed candidate verdict should be rejected.

For a larger example, follow the [README's reproducible demonstration](../README.md#reproduce-the-48-trial-demonstration).
It builds aggregation workloads and a separate closed-form verifier, then compares
memory, CPU and concurrency profiles in 48 trials.

## 10. What we have built, milestone by milestone

### M0 — A working measurement foundation

Implemented strict configuration, seeded scheduling, hardened Docker execution,
image/engine provenance, explicit outcomes and persistent reports. Real fixtures
exercise memory pressure, timeouts, nonzero exits and cleanup.

### M1 — Independent task verification

Added bounded JSON handoff, separate verifier containers, versioned task contracts
and pending-verification checkpoints. Tests distinguish incorrect outputs from
verifier failures and reject candidate self-grading.

### M2 — Measurement fidelity and safe recovery

Added pinned endpoint identity, raw local Engine telemetry, nullable counter
derivations, resource audits, separate cgroup probes, optional CPU affinity,
cooperating-run locks and ownership-checked recovery. Real tests exercise process
kills and batch/concurrency behavior. The sampler contrast did **not** establish
zero overhead, so sampling remains opt-in.

### M3 — An offline agent integration slice

Implemented a bounded recorded-response loop, fresh containers for read-only tool
actions, independent answer verification, provenance and a synthetic budget ledger.
Repeated fixture runs produced matching evidence; deliberately wrong recorded
answers failed verification.

**Not achieved:** validated live-provider benchmarking. The experimental Codex
subscription preflight blocks on the tested CLI when it cannot verify the required
tool boundary before a model call. We did not bypass that condition or substitute
paid API calls. Recorded usage is not real provider billing.

### M4 — Descriptive comparisons and a narrow block-analysis extension

Implemented identity checks, paired outcomes, missingness accounting and offline
comparison artifacts. An early task-bootstrap proposal failed review/calibration;
its intervals were withheld rather than presented as valid.

We subsequently ran a separate **144-trial, 24-block** fixed-suite experiment.
Its candidate-minus-baseline point was one-third, but its conservative conditional
bound included zero. That did not establish a statistically supported improvement.
Broader method review, power planning and multiplicity policy remain open.

### M5 — A usable local investigation workbench

Implemented run browsing, live refresh, filtering, outcome matrices, provenance
differences, resource timelines, trace/verifier inspection and filtered exports.
Automated Chromium checks exercise desktop/mobile flows, refresh state and escaping.
This is browser regression evidence, not a comprehensive accessibility certification.

### M6 — An authenticated local coordination pilot

Implemented owner/worker/admin roles, reviewed image catalogs, worker allowlists,
transactional leases, stale-attempt fencing, quotas, retained artifacts and HMAC
receipts/audit checks. Two local owners and two worker identities executed and
retrieved separate Docker results; failure tests cover expired or killed workers.

The service aims for at-most-once **accepted results**, not exactly-once execution:
a disconnected worker may continue after its lease expires. HMAC is symmetric
integrity protection, not a public signature or remote attestation. Multi-host
production deployment and hostile-tenant isolation remain outside the pilot.

## 11. What our demonstration actually established

The preserved [aggregation study](demonstration-results.md) ran four tasks across
four profiles with three repetitions: all **48 trial records** were retained.

- **Three observed OOM kills:** the materializing batch implementation failed
  under 48 MiB, while the streaming implementation passed under that same profile.
- **Twelve wrong answers rejected:** each deliberately wrong candidate exited
  zero, but the independent verifier rejected it.
- **Slower without becoming incorrect:** the CPU-heavy fixture remained correct
  at 0.25 CPU; its paired mean workload duration increased by approximately
  4.32 seconds relative to 1 CPU on that host.
- **Complete diagnostic accounting:** the run recorded 49 raw telemetry samples,
  retained 39, and reported no telemetry-error, truncation or leaked-reader trials.

These are fixture-specific observations on a Linux ARM64 Docker Desktop engine.
They do not measure an LLM, establish a universal RAM recommendation, prove a
causal effect, or measure concurrency throughput.

## 12. What we have achieved as a distributable project

- Public source repository with an MIT license and declared package licensing.
- A Python package with no third-party runtime dependencies.
- A checked wheel installation outside the source checkout, including web assets.
- CI jobs for Python 3.11–3.14, real Docker tests, Chromium and clean installation.
- A reproducible case study, research/design records, security notes and runbooks.
- A strict static-demo exporter that omits arbitrary logs, machine paths and
  credentials. The generated preview remains local; it has not been deployed here.

At the public-onboarding check, test discovery found **486 tests**: 463 executed
and 23 opt-in/platform checks skipped. Integrations run in separate CI jobs.
The [MIT publication CI run](https://github.com/AnkitPorwal04/evalnoise/actions/runs/34936283968)
passed all seven jobs. These are historical verification records, not a promise
that every future commit has passed; consult [current CI](https://github.com/AnkitPorwal04/evalnoise/actions).

## 13. Where to go next

For users: reproduce a fixture, inspect its evidence, then adapt a reviewed task
using the [configuration contract](data-contract.md) and [verifier guide](verification.md).
Plan resource contrasts before inspecting their outcomes.

For contributors: prioritize reproducibility and first-user feedback, additional
host validation, the intermittent Docker image-resolution issue recorded in
[validation history](validation.md), and the open live-provider/statistical gates.
Production coordinator work needs a separate deployment and threat review.

The project is useful now for **reviewed local infrastructure-sensitivity
experiments**. It is not yet a fully validated live-agent benchmark platform or a
production distributed evaluation service. That distinction is part of the result,
not a disclaimer to hide at the end of a score.
