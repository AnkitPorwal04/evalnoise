# Same answer contract, different failure evidence

## What was run

The [prospective protocol](demonstration-protocol.md) fixed 48 trials before
execution: four synthetic aggregation fixtures, four resource profiles and three
repetitions. Run `aggregation-demonstration-dcc4e21e077d` completed with all 48
records on the local Linux ARM64 Docker Desktop engine. No model was involved.

The reproducible workload image ID was
`sha256:ed15564ff10dcaa0c163ee30181073258334cf503ff919a43407f4a95142f86f`;
the independent verifier image ID was
`sha256:b217adffedf37edbb4fb584298fb34ae2227717b2cc405ef89297604dadb66af`.
The first preparation attempt incorrectly supplied the build's configuration
digest, which this image store could not resolve as an image. It stopped before
creating a configuration directory or any trial. Docker image inspection supplied
the actual index IDs above; those were explicitly used for the sole measured run.
No failed trial was retried or removed.

## Three distinctions the evidence demonstrates

**A materialization failure is not an incorrect answer.** The batch fixture passed
all three baseline trials at 256 MiB, but all three 48-MiB trials recorded
`oom_killed` with the engine's `OOMKilled` flag. No verifier was launched for those
unsuccessful executions. The streaming implementation passed all three trials
under the same 48-MiB profile. This illustrates an implementation/resource
interaction in these reviewed fixtures, not a universal memory recommendation.

**Exit zero is not sufficient evidence of correctness.** All 12 intentionally
wrong outputs exited zero, retained `execution_status: passed`, and received
`verification_failed` from the separate closed-form verifier. Increasing resources
did not repair the deliberate arithmetic error. This public known-answer checker
is not hidden-test security or proof against malicious candidates.

**Slower does not necessarily mean unsuccessful.** Lowering the CPU quota from
1 to 0.25 preserved all baseline outcome categories. The CPU fixture's paired
mean workload duration increased by 4.322189 seconds across its three jointly
passing pairs. This is container start-to-finish time, excluding verifier time,
on one host—not a general speed ratio or causal effect estimate.

## All outcomes and contrasts, not just the largest difference

- Baseline: 9 passed, 3 verification failures; 12/12 recorded.
- Memory-tight: 6 passed, 3 observed OOM kills, 3 verification failures; 12/12 recorded.
- CPU-tight: 9 passed, 3 verification failures; 12/12 recorded.
- Concurrent: 9 passed, 3 verification failures; 12/12 recorded.

Each baseline contrast contains 12/12 resolved pairs. Candidate-minus-baseline
task-weighted pass differences are -0.25 for memory, 0 for CPU and 0 for
concurrency. Paired mean duration differences over jointly passing observations
are +0.009199 seconds (memory, 6 eligible pairs/two tasks), +1.685290 seconds
(CPU, 9 pairs/three tasks) and +0.015926 seconds (concurrency, 9 pairs/three tasks).
These timings have different survivor sets; do not compare them as general
speedups. The concurrency result is not an aggregate throughput measurement.

All three comparisons are descriptive, with no confidence intervals, p-values,
power claim or population generalization. The generic comparison CLI labels
selection as user-selected: this linked protocol records our intended contrasts,
but neither the tool nor a source hash proves prospective registration.

## Telemetry and evidence integrity

All 48 workload resource audits matched the requested engine configuration;
daemon identity was stable from beginning to end. Raw streaming received 49
samples and retained 39, with zero recorded telemetry-error, truncated or leaked-
reader trials. Several short workloads yielded no usable sample. Counts do not
turn sampled memory into a peak or imply dedicated CPUs. `oom_killed` is preserved
as its own workload outcome; the comparison's narrower infrastructure-error
category covers harness/engine/verifier failures and must not be read as an OOM
count.

`python -m scripts.analyze_demo RUN --output NEW_DIRECTORY` validates existing
evidence, creates all three comparisons, and emits `evidence.json`: an allowlisted
projection of all 48 trial statuses, exit codes, OOM flags, verifier verdicts,
durations and individual raw-trial hashes. It omits logs, machine paths, engine
identifiers and credentials. It is explicitly not a complete or signed bundle.
The canonical source manifest-plus-trials SHA-256 is
`8defe60503ddc7c1cc1597f629efdccecc706efcb1423de0db4eb7afff4aea8d`.
Raw evidence remains locally preserved under ignored `runs/`; no existing run
was regenerated or overwritten.

The first derived comparison output exposed a stale explanatory sentence claiming
all within-run artifacts predated contract hashes. That sentence was corrected
with a regression test; fresh comparisons were written to a new analysis directory.
The original measured manifest and trials were untouched.

## Verification

The local regression run discovered 483 tests: 460 executed successfully and 23
opt-in/platform checks skipped. The 48 real Docker study trials above are a
separate measurement run, not a substitute for the skipped integration tests.
Python compilation and diagnostics on the changed Python files passed. The
memory comparison was checked in Chromium at 1440-pixel and 390-pixel widths
without page overflow or JavaScript errors; the workbench listed all 48 records.
Remote CI results are available in the repository's Actions history.

## Inspect locally

- Workbench: `http://127.0.0.1:4178/run/aggregation-demonstration-dcc4e21e077d`
- Full run report: `http://127.0.0.1:4177/aggregation-demonstration-dcc4e21e077d/report.html`
- Comparisons: under `http://127.0.0.1:4177/aggregation-demonstration-analysis/`,
  use `memory-tight/comparison.html`, `cpu-tight/comparison.html` and
  `concurrent/comparison.html`.

These URLs require the local services and local artifacts; they are not public
hosted demos. The source repository is now public and MIT-licensed; raw run data remains local. Publishing reviewed evidence or a
static demonstration is a separate step; the execution coordinator is not exposed.
