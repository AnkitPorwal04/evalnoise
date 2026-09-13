# Research Basis

## Research Question

How much do an evaluation's observed outcomes depend on execution conditions, and which evidence is needed to distinguish workload results from failures in execution or observation?

The important distinction is between observing an infrastructure event and proving it caused a particular benchmark score change. An OOM event does not reveal whether the same attempt would otherwise solve a task. More resources can also change the strategies an agent can execute. EvalNoise must preserve evidence without inventing counterfactual answers.

## Primary Sources

1. [Anthropic: Infrastructure noise](https://www.anthropic.com/engineering/infrastructure-noise). The reported Terminal-Bench resource experiment observed an approximately six-percentage-point spread across configurations. Infrastructure errors fell from 5.8% to 0.5% between the strict and uncapped settings. The 1x-to-3x comparison reduced infrastructure errors but did not establish a statistically significant success-rate increase in that comparison. These are that study's findings, not EvalNoise results or universal tuning rules.
2. [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/). CPU quotas are bandwidth ceilings, not reservations. Equal memory and memory-swap limits disable swap. Soft reservations are not hard guarantees. These semantics determine the current profile contract.
3. [Docker Engine API state definitions](https://docs.docker.com/reference/api/engine/version/v1.51/). Container state includes exit code, lifecycle timestamps, runtime error, and OOMKilled. An OOM flag can describe a killed child even if the main process exits successfully. EvalNoise retains both pieces of evidence.
4. [Docker stats](https://docs.docker.com/reference/cli/docker/container/stats/) and [runtime metrics](https://docs.docker.com/engine/containers/runmetrics/). CLI memory displays and raw API values differ. Sampling has overhead, can miss short-lived peaks, and depends on cgroup version. The initial implementation stores optional raw CLI samples rather than claiming precise peak usage or throttling counters.
5. [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html). The kernel's controllers are the underlying resource mechanism. Requested Docker settings are not independent proof of effective enforcement. Rootless and delegation differences need explicit treatment.
6. [Docker rootless limitations](https://docs.docker.com/engine/security/rootless/tips/#limiting-resources). Unsupported cgroup delegation can make requested limits ineffective. EvalNoise rejects a missing cgroup driver and known swap-limit warnings, but does not yet audit delegated controllers inside every environment.
7. [Docker Desktop resource settings](https://docs.docker.com/desktop/settings-and-maintenance/settings/). On macOS, measurements involve a Linux VM with its own resource limits. VM backend, Resource Saver, host pressure, and emulation can confound comparisons. Current provenance records engine/OS/architecture/resources, not every Desktop preference.
8. [Harbor](https://github.com/harbor-framework/harbor) and its [resource-management documentation](https://harborframework.com/docs/tasks/managing-resources). An existing agent-evaluation framework and a candidate integration boundary. EvalNoise should add controlled experiment design and evidence comparisons, not reimplement an entire agent harness without justification.
9. [Adding Error Bars to Evals](https://arxiv.org/abs/2411.00640), Evan Miller, arXiv:2411.00640. Read in full for v0.5 rather than cited from memory. Section 2.2 derives clustered standard errors for questions drawn in groups and reports a real cluster adjustment up to roughly 3x on DROP, so ignoring clustering can make an estimate look far more precise than it is. Section 3.1 states directly that pooling all *K*x*N* answers is inconsistent because multiple answers to one question violate independent draws — this is the rule that makes the task, not the trial, EvalNoise's sampling unit. Section 4.2 recommends conducting inference on question-level paired differences rather than on population-level summary statistics, which is the estimand EvalNoise implements. Section 5's power analysis gives roughly 969 questions for a 3-percentage-point minimum detectable effect under its stated assumptions, which is why a handful of scripted tasks cannot support an effect claim. Two departures are deliberate and recorded: Miller regards bootstrapping as unnecessary when the C.L.T. applies, but EvalNoise has very few, unequally sized, partially missing clusters, so a resampling interval is used instead of a closed-form standard error; and Miller's super-population framing does not hold for a fixed configured suite, so EvalNoise reports resampling stability rather than a population confidence interval.
10. [Python subprocess](https://docs.python.org/3/library/subprocess.html). Killing a timed-out Docker CLI process does not clean up the daemon-owned container. The runner needs a pre-generated name and explicit cleanup independent of the subprocess lifecycle.
11. [`scipy.stats.bootstrap`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html). Consulted for the definition of the resampling procedure, not imported: SciPy is not a dependency and the runtime stays standard library only. It defines the percentile method as resample, recompute the statistic, and take the interval of the bootstrap distribution, and documents that `method='BCa'` emits a `DegenerateDataWarning` and can return NaN when the bootstrap distribution is degenerate. EvalNoise therefore implements percentile intervals and refuses a degenerate distribution explicitly rather than emitting a NaN or a zero-width interval. Its `paired=True` semantics — resample an index array and apply the same indices to every sample — is the same idea as resampling whole task clusters here.
12. [Python `random`](https://docs.python.org/3/library/random.html). The "Notes on Reproducibility" section guarantees that a given seed reproduces the generator's sequence, which is what makes a seeded bootstrap reproducible across invocations. The module's own bootstrap recipe fixes the percentile index convention EvalNoise uses: for 100 sorted resample means a 90% interval is `means[5]` to `means[94]`, that is ranks `floor(tail*B)` and `B-1-floor(tail*B)`.

## Alternatives And Positioning

Harbor provides task/agent execution infrastructure. Terminal-Bench provides benchmark tasks and evaluation conventions. Container engines enforce execution settings. General tracing tools expose agent traces and application metrics. EvalNoise's proposed role is a controlled-experiment layer connecting execution settings, repeated matched tasks, outcomes, and auditable artifacts.

This is a positioning hypothesis, not a claim that no existing tool offers similar functionality. Before committing to an adapter, verify the current upstream API, resource semantics, task versioning, and artifact format. Do not assume a source's current behavior from a research note alone.

## Decisions Driven By Research

- Keep image preparation outside timed trials; record the resolved image ID and architecture.
- Separate lifecycle errors from workload exit results; do not infer OOM from exit 137.
- Start with known-positive and identical-profile controls before real agents.
- Record missing observations rather than quietly dropping them.
- Use randomized profile order within repetition blocks, while acknowledging imperfect temporal control.
- Keep v0.1 reporting descriptive. Do not add confidence intervals before defining the sampling population and dependence model.
- Do not claim secure execution of adversarial agents from Docker hardening alone.
- Aggregate by task and average repetitions within a task first, because repeated attempts share the task, host, block, and seed.
- **Publish no uncertainty estimate until the estimand is settled and a method has demonstrated coverage.** A prototype cluster bootstrap was written, characterised, found inadequate, and withheld rather than shipped.
- Do not infer adequacy from the size of a resampling support: bootstrap multisets are not equiprobable, so counting them says nothing about tail resolution.
- Detect differential loss by pair identity rather than by count.
- Fail closed on missing or unstable engine provenance rather than comparing absent fields as equal.

## Open Research Work

Quantify observer overhead, characterize native-Linux versus VM behavior, add raw cgroup/Engine measurements, test time-block sensitivity, and distinguish verifier failures from agent failures. Task-weighted descriptive comparison is implemented; **inference is not**. What remains is a defensible estimand for a fixed configured suite, a resampling or analytic method with demonstrated coverage at realistic task counts, an **independent review of the methodology**, a task suite large enough to support any of it, time-block handling, multiple-comparison policy, and cost/reliability frontiers. Provider drift, rate limits, token cost, and network reliability become relevant only after a reviewed model adapter exists.
