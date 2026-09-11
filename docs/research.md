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
9. [Adding Error Bars to Evals](https://arxiv.org/abs/2411.00640). Useful statistical background for uncertainty in evaluation measurements. Repeated attempts on the same tasks are not a license to treat all records as independent population draws. Formal inference remains a separate milestone.
10. [Python subprocess](https://docs.python.org/3/library/subprocess.html). Killing a timed-out Docker CLI process does not clean up the daemon-owned container. The runner needs a pre-generated name and explicit cleanup independent of the subprocess lifecycle.

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

## Open Research Work

Quantify observer overhead, characterize native-Linux versus VM behavior, add raw cgroup/Engine measurements, define task-clustered inference, test time-block sensitivity, and distinguish verifier failures from agent failures. Provider drift, rate limits, token cost, and network reliability become relevant only after a reviewed model adapter exists.
