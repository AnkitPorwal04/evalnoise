# Aggregation demonstration: prospective protocol

This protocol is written before the demonstration is run. It is a synthetic
data-processing case study, not an AI-agent benchmark or representative sample
of production applications. No provider, subscription or paid API is used.

## Question and fixtures

Can preserved execution evidence distinguish an unsuccessful resource-limited
execution from an exit-zero answer rejected by an independent verifier?

Each fixture processes the sequence 1..200,000 shifted by `seed % 31` and reports
its row count and sum of squares. `batch` materializes and sorts annotated
records; `stream` aggregates without materialization; `cpu` adds a fixed
3,000,000-link SHA-256 chain before reporting the same aggregate; `wrong`
deliberately introduces a one-unit answer error. The CPU chain is a load fixture:
the verifier checks the reported aggregate, not proof that every hash executed.
The verifier uses a separate image and closed-form arithmetic, not candidate
code. These public, known-answer fixtures do not establish adversarial grading.

## Fixed design

- Seed 2026; three repetitions; four tasks; four profiles: **48 planned trials**.
- Baseline: one CPU, 256 MiB, concurrency one, 20-second workload deadline.
- Memory contrast: 48 MiB only; CPU contrast: 0.25 CPU only; concurrency contrast:
  two workers only. Per-container quotas are ceilings, not reservations.
- Raw telemetry retention is two seconds in every profile. Short executions may
  have no usable samples; absence is not zero resource consumption.
- Each trusted verifier has a separate one-CPU/128-MiB/10-second budget and runs
  after the entire workload batch, following the existing scheduler.
- Existing seeded scheduler randomizes profile order within each repeat, with
  matched task order. Concurrency changes the overlap pattern intentionally.
- Resolve both image IDs before execution. Keep the generated configuration,
  manifest and every trial; no trial retries, selective removal, or early stopping.

Expected checks, not acceptance criteria to tune toward: `wrong` should exit zero
but fail verification under all profiles; materialization may fail under the
memory ceiling; CPU quota may lengthen the CPU fixture. Preserve contradictory
results rather than changing workloads until a preferred result appears.

## Analysis and interpretation

Report counts by task/profile and both execution and verification status. Use
existing descriptive `compare` for three baseline contrasts, explicitly declaring
the single changed resource field. Publish planned, recorded, resolved and
jointly passing denominators; timing contrasts condition on jointly passing
pairs. No bootstrap, significance claim, general overhead estimate or multiple-
comparison inference. Three repeats characterize this demonstration, not power.

An observed `OOMKilled` flag is evidence of an OOM event; exit 137 alone is not.
A resource-profile association is not proof of the counterfactual reason any
particular application would fail. Host load, thermal state, shared caches and
Docker Desktop VM scheduling are not controlled. Independent output rejection
is kept separate from runtime, timeout and verifier failures.

## Reproduce

Build `workloads` and `verifiers` using their pinned Dockerfiles. Then run
`python -m scripts.prepare_demo --workload IMAGE --verifier IMAGE --output runs/demo-config`
with the newly built image IDs, followed by
`python -m evalnoise run runs/demo-config/experiment.json --trust-config --output runs`.
The preparation directory must be new. Do not reuse local image IDs on another
architecture without rebuilding and recording that host's immutable IDs.
