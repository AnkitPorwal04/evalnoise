# M3 Plan: Agent Integration, Offline Slice

This plan covers **v0.4**, an offline slice of M3. It adds a bounded agent tool loop, a
recorded deterministic provider, and a strict synthetic budget ledger. It does **not**
close the M3 gate: that still requires an explicitly authorized and budgeted real-provider
run, which has not happened and is not attempted here.

## Framework Decision: Bespoke Loop Now, Harbor Interoperability Later

The roadmap said to prefer an existing framework such as Harbor "after verifying its
current contract". The contract was verified against the primary source
(`harbor-framework/harbor` v0.22.0, `pyproject.toml`, `src/harbor/agents/base.py`,
`src/harbor/environments/base.py`, `src/harbor/models/task/config.py`, and the published
docs at harborframework.com). The relevant facts:

- `BaseAgent.run(instruction, environment: BaseEnvironment, context: AgentContext)` is
  async, and agents drive the environment through `BaseEnvironment.exec`.
- Environments are pluggable. `BaseEnvironment` is an interface with many existing
  backends, so an EvalNoise-backed Harbor environment is a **legitimate future option**,
  not an impossibility.
- Harbor supports `NetworkMode.NO_NETWORK | PUBLIC | ALLOWLIST` per phase, so network
  isolation is available there too.
- The default model path injects provider credentials into the agent's environment
  (`agents/model_connection.py`, `ProviderAccess.api_key_envs`), or proxies through
  `bridges/acp.py`.
- Harbor requires Python >= 3.12 and roughly twenty-five runtime dependencies. EvalNoise
  today declares `dependencies = []` and tests on 3.11 through 3.14.

**Decision: bespoke for this slice, on bounded-scope grounds, not on capability grounds.**
Adopting Harbor now means either replacing EvalNoise's container lifecycle with Harbor's,
or writing a Harbor `BaseEnvironment` that delegates to EvalNoise. Both are real projects
with their own review and test burden, and neither is needed to deliver a bounded tool
loop with independent verification. The dependency and Python-floor deltas are integration
costs to weigh, **not** evidence that Harbor is unsuitable, and Harbor's optional
dependencies do not imply that anything listens on a socket in this repository.

What this slice does adopt, for free and with no dependency, is Harbor's **field
vocabulary**, so that a later one-way exporter is a mapping rather than a rewrite:

| EvalNoise field | Harbor source |
| --- | --- |
| `tool_calls[].{tool_call_id,function_name,arguments}` | `models/trajectories/tool_call.py` |
| `steps[].{step_id,timestamp,source,model_name,message,observation,metrics,llm_call_count}` | `models/trajectories/step.py` |
| `metrics.{prompt_tokens,completion_tokens,cache_tokens}` | `models/metric/usage_info.py` |
| `agent.{name,version}`, `model.{name,provider}` | `models/trial/result.py` |

Recorded as ADR 012. Revisit when a real-provider budget is authorized.

## What The Recorded Cassette Does And Does Not Establish

The fixture answer is a sum of squares up to a seed-derived limit. It is **arithmetically
derivable** once the limit is known, and a sufficiently capable model could shortcut parts
of the task. This slice therefore makes no claim that the answer is impossible without
tools, and no claim about model capability at all.

What the recorded provider does establish is narrower and checkable:

- A cassette key is the SHA-256 of the **full canonical request**: model, parameters,
  protocol, and the entire message list including every prior tool observation verbatim.
  A replay that skipped a tool call, or received a different observation, would produce a
  different digest and find no recorded entry. Replay is therefore faithful to the
  recorded interaction, and cannot silently degenerate into emitting a final answer.
- A cassette miss is a hard `ProviderError`. The recorded provider never generates,
  interpolates, or falls back to a nearest entry.
- Fixture correctness is checked by the existing independent `sum-v1` verifier, which is
  unchanged by this slice.

These are fixture-correctness and determinism properties. They are not a benchmark result.

## Protocol: agent-step-v1

The model call happens in the **runner process**. Each tool action happens in a **fresh
hardened container** with `--network none`, no credentials, and the profile's own budget,
created through the existing `trial()` path so it keeps the M0-M2 resource audit,
telemetry, classification, and ownership-checked cleanup.

```text
provider.complete(call)              host, recorded cassette, no socket
  -> ledger.admit(...)               integer micro-USD, worst case, before the call
  -> ToolCall validated against a fixed schema
  -> trial(..., artifact_env="EVALNOISE_TOOL_CALL_B64")
  -> logs -> envelope(logs, "evalnoise_observation")
  -> append step -> loop, bounded by max_steps
  -> final_answer -> emitted as the evalnoise_artifact
  -> existing verify() -> existing trusted verifier          UNCHANGED
```

Because each step is a fresh container, tools are **pure functions of `(seed, tool_call)`**.
This slice ships a read-only, stateless tool surface with a fixed tool table and no
expression evaluation, no shell, and no template execution. Stateful tools need `docker
exec` or a persistent container, which would break the pre-start enforcement audit, and
are deferred behind their own design review.

## Budget: Synthetic, Integer, Offline

- All money is **integer micro-USD**. No float arithmetic anywhere in the ledger, so
  rounding cannot produce an overspend.
- Prices are **declared in the configuration**. An undeclared model is a `ConfigError`.
  We cannot verify a provider's price list offline and do not pretend to.
- Admission is **worst case, before the call**: estimated prompt tokens plus the
  configured `max_output_tokens`, priced with ceiling division.
- The prompt estimate is a **local character-based heuristic**. It is not a provider
  tokenizer and is explicitly **not** a guarantee about a real provider's billed cost.
- The ledger records `simulated_reported_*` figures from the cassette next to
  `actual_charged_micros: 0`, because no request leaves the host in this slice.
- Calls, retries, and steps are separately bounded. Rejection happens before the provider
  call and before the tool container is created, as appropriate.
- One ledger per run, shared and mutex-guarded, so profile concurrency cannot over-admit.

## Recovery Must Cover Agent Step Containers

`recovery.expected_names` derives container names from the validated plan. An agent task
produces no parent container and up to `max_steps` step containers, so the derivation is
extended to enumerate `evalnoise-<run>-<trial>-sNN` for `NN` in `1..max_steps`, plus the
verifier name, and to omit the parent workload name for agent tasks. `stage_image` gains
an `agent_step` stage that resolves to the task's tool image. Ownership labels, exact-ID
removal, and the refuse-everything-if-any-candidate-is-unowned rule are unchanged.

Because names are enumerated, the configuration bounds total planned containers, not just
planned trials.

## Acceptance Gates

### Offline slice gate (v0.4) — must pass to ship this milestone

- [ ] Two runs of the same config and seed produce identical step records, tool calls, and
      observations, modulo timestamps and container identity.
- [ ] A cassette miss is a hard error; the recorded provider never invents a response.
- [ ] The cassette key covers the full message history, so a transcript that omits a tool
      observation cannot be replayed.
- [ ] Budget admission refuses before any provider call and before any container is
      created; a plan that cannot fit the budget is refused before the run directory exists.
- [ ] A budget refusal is `budget_exhausted`, never a pass, and never launches a verifier.
- [ ] All money arithmetic is integer micro-USD; a float in the ledger is a test failure.
- [ ] No credential environment variable name or value reaches a container argv or any
      artifact, verified with fake keys set in the environment.
- [ ] Every agent step container keeps `--network none` and passes the enforcement audit.
- [ ] Provenance recorded: agent name and version, model, parameters, retry attempts,
      per-step token and simulated cost observations, tool image ID, cassette digest, and
      the task contract hash.
- [ ] Missing or mutated cassette or contract is rejected by the reporter.
- [ ] A report regenerates offline from the run snapshot without the original cassette file.
- [ ] Step limit, tool failure, protocol failure, and cancellation each produce explicit
      non-pass statuses, and an interruption never fabricates a final verdict.
- [ ] Agent clock domains stay separate; the parent record aggregates and does not
      impersonate a single container measurement.
- [ ] `recovery.diagnose` and `cleanup --confirm` cover agent step containers, verified
      against a real `SIGKILL`ed run.
- [ ] The batch verification barrier and the halt-on-cleanup-failure rule still hold.
- [ ] M0-M2 test semantics are unchanged and the full suite passes locally.

### Full M3 gate — OPEN, requires explicit authorization

- [ ] One real provider call under a declared USD budget, with observed cost reconciled
      against admission and the price table checked against an actual invoice.
- [ ] A small public, reviewed task subset that is not this in-repo fixture.
- [ ] Real retry, timeout, and rate-limit attribution measured rather than simulated.
- [ ] A live client and credential policy reviewed before any key is read.

No paid or credentialed call is performed by this slice. There is no live client in the
codebase and no credential lookup path.

## Fixture Honesty

`tools/` and `cassettes/` are small, reviewed, in-repo fixtures written for this
repository. They are **not** a public benchmark dataset, not a held-out test set, and not
evidence about any model. They exist to exercise the loop, the budget, and the recovery
paths deterministically and offline.
