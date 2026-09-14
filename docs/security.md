# Security And Operations

## Trust Model

v0.2 is for reviewed local workloads and trusted verifiers on a Docker engine the operator controls. `--trust-config` acknowledges that a configuration selects executable code. It is not a security proof. Docker access is powerful; the runner is not a safe public multi-tenant execution service.

Do not run unknown image submissions, generated adversarial agent code, private employer datasets, or credential-bearing tasks on your everyday machine. A future adversarial-agent tier requires separately evaluated isolation such as dedicated disposable VMs or a suitable sandbox backend.

## Implemented Containment

Containers run as UID/GID 65534 with all capabilities dropped, no-new-privileges, Docker's normal seccomp policy, a read-only root, no network, no host mounts, PID limit 128, memory/CPU ceilings, and swap disabled. `/tmp` is a 64 MiB noexec/nosuid/nodev tmpfs. Tmpfs use consumes memory; workloads must be compatible with these restrictions. Image-declared volumes and image contents remain part of the trusted image contract.

The host executes argv arrays, never shell-expanded user strings. Image references and identifiers are constrained. The measurement runner does not mount the Docker socket into workloads, pass host credentials, or publish artifacts automatically. The M5 workbench is read-only. The separate opt-in M6 loopback coordinator exposes authenticated job submission for administrator-reviewed immutable-image catalogs, not arbitrary commands or images. Workers independently allowlist images and upload bounded evidence bundles. See [the coordinator trust boundary](m6-design.md) and [operational runbook](cluster.md); this is not an Internet-facing execution service or hostile multi-tenant sandbox.

## Residual Risks

Independent verification transfers only a validated JSON envelope, at most 8192 UTF-8 bytes, through a base64 environment variable. Base64 is not encryption: Docker administrators can inspect it, and artifacts are retained in reports. The verifier image must not execute candidate data. Candidate-written verdicts are ignored; a verdict is accepted only from the separately executed trusted verifier. This is a correctness boundary for reviewed fixtures, not adversarial isolation or hidden-test confidentiality. Contract hashes detect inconsistent identities, not coordinated tampering.

Container/kernel vulnerabilities, host exhaustion, image-declared behavior, daemon compromise, Docker endpoint changes, host interference, and hard runner termination remain possible. Cgroup driver and swap-warning checks detect some unsupported environments, not complete kernel enforcement. Inspect HostConfig echoes requested settings; it is not an independent controller audit. A shared Docker daemon does not provide dedicated CPU reservations or experiment exclusivity.

Logs and stdout can contain secrets. Exported reports include bounded logs and command metadata. Review artifacts before sharing. The HTML renderer escapes workload logs and uses a no-script CSP with no external assets, but the offline artifact loader is intended for locally generated evidence, not hostile uploads. No signatures or tamper-evident storage exist yet.

## Cancellation And Cleanup

SIGINT and SIGTERM request cooperative cancellation. Running containers are killed and evidence/cleanup is attempted. Docker CLI timeouts do not guarantee immediate daemon response. Cleanup failure stops later batches and records the affected container name.

v0.3 adds telemetry and recovery surface worth stating plainly. Raw Engine telemetry connects to a local Docker socket, which is privileged access to the daemon; the runner only reads container stats over it and never mounts it into a container. The recorded endpoint keeps a `skip_tls_verify` boolean but never certificate paths, key paths, or TLS material. A remote context is refused for measurement because a context is a mutable name; use an explicit `DOCKER_HOST`. `DOCKER_CONTEXT` and `DOCKER_HOST` are stripped from child processes so the pinned endpoint cannot be overridden mid-run.

A single advisory lock per daemon ID keeps cooperating EvalNoise runs from overlapping on one engine, and running EvalNoise containers from another run refuse a start. This is cooperation between processes of one local user against one daemon. It is not a distributed lock, not a security control, and it does not exclude other users, other tools, or general host load.

`evalnoise probe` starts a configured image as trusted code and therefore requires `--trust-config` like `run`. It is not a read-only command.

## Agent Slice Credential And Network Policy

v0.4 adds a bounded agent loop whose model turn runs in the runner process, never inside a measured container. Tool containers keep every existing restriction, including `--network none`, and receive only the seed and a bounded base64 tool call in `EVALNOISE_TOOL_CALL_B64`. The Docker boundary accepts only that variable or `EVALNOISE_ARTIFACT_B64` for the bounded handoff; any other name is refused. As with the verifier envelope, base64 is not encryption and Docker administrators can read it.

**The measurement core has no live provider client and no credential lookup.** This statement is scoped to the recorded-provider path, which is unchanged: `provider.py` contains no HTTP client, no socket use, and no environment access, and a test asserts the absence of those constructs and that a full replay completes with `socket.socket` patched to raise. `provider.kind` accepts only `recorded`. Agent `parameters` is a closed numeric whitelist, so a configuration cannot smuggle an API key, base URL, or environment mapping into an artifact. A test sets fake `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` values and asserts that neither the name nor the value appears in any container argv, trial record, manifest, or report.

### Scope correction: the experimental `codex-check` preflight

`evalnoise codex-check` is a separate, experimental CLI preflight and is **not** part of
the measurement core. It narrows the blanket claim above in exactly one way, stated here
rather than buried: it executes the installed `codex` binary as a subprocess, and that
subprocess performs its own ChatGPT subscription authentication.

What remains true, and what is now qualified:

- **EvalNoise still reads no credential files.** It does not open, copy, symlink, or parse
  `auth.json` or any other auth material, and it holds no API key. Auth state is learned
  only from the exit status and text of the official `codex login status` command.
- **EvalNoise still has no live provider client.** There is no HTTP client, no socket, no
  reverse proxy, and no API endpoint in EvalNoise. The `codex` binary is the client.
- **The child environment is an allowlist** of `HOME`, `PATH`, `CODEX_HOME`, `TMPDIR`, and
  `TERM`. A credential-shaped variable name is refused outright. `HOME` is required
  because the official binary resolves its own credentials there, so this is **not**
  credential isolation and is not described as one.
- **The recorded provider path is untouched.** `codex-check` writes a separate
  `subscription_smoke` artifact and never creates a `RecordedProvider`, cassette entry, or
  budget ledger row.

EvalNoise refuses to call the model at all unless it can first verify the child's tool
catalog is empty or non-executing. On Codex CLI 0.153.4 that catalog is not exposed to any
zero-model command, so the command blocks and exits 3. **That refusal is EvalNoise's own
boundary, not a user preference, and no parameter, flag, or environment variable overrides
it.** Post-hoc detection of a tool call in the transcript is treated as evidence of a
control gap, never as the control itself.

Cassettes are reviewed in-repo fixtures. They are frozen and hashed at preflight, bounded to 1 MiB and 512 entries, parsed strictly with duplicate-key and non-finite rejection, and restricted to a relative path of at most four components that resolves inside the configuration's own directory. Their digest enters the task contract hash. This detects an inconsistent or edited cassette; it is not a signature and not protection against an attacker who rewrites every hash. Do not put secrets in a cassette, a tool call, or an observation: all three are retained in artifacts.

The budget ledger is **synthetic**. It admits and prices against a table declared in the configuration and records `actual_charged_micros: 0` and `provider_requests_sent: 0` because nothing is sent. Its prompt estimate is a local character heuristic, not a provider tokenizer, and must not be relied on as a spending control for a real provider. A real-provider path would need its own credential and network review before any key is read; none is authorized or implemented.

Agent step containers are named `evalnoise-<run>-<trial>-sNN` and carry the same run, engine, and owner labels as any other container, so `diagnose` and `cleanup --confirm` cover them under the existing ownership rules. The configuration bounds total planned containers so that recovery's name enumeration stays finite.

After SIGKILL, host failure, or daemon outage, prefer the built-in commands, which refuse anything this run does not own:

```sh
python3 -m evalnoise diagnose runs/<experiment-run-id>
python3 -m evalnoise cleanup  runs/<experiment-run-id> --confirm
```

Diagnosis writes nothing. Cleanup holds the engine lock while it validates and removes, requires the container name, run label, engine label, and exact-stage image identity to agree, checks every candidate before removing any, and removes by resolved full container ID. It refuses entirely while any coordinator is live, including a still-running instance of the same run. It never edits trial statuses, verdicts, or the manifest status, and there is no automatic resume.

To inspect manually instead, still restrict yourself to the run you own:

```sh
docker ps -a --filter label=io.evalnoise.run=<run-id>
docker inspect <exact-container-name>
docker rm --force <exact-container-name>
```

Do not sweep every EvalNoise-labeled container: another experiment may be active. Do not remove images, volumes, or unrelated containers as routine cleanup. Keep incomplete artifacts and regenerate the report to expose missing observations. There is no automatic resume; use a new run ID after recovery.

## Operational Checklist

Review config/image/command, prepare images before timing, confirm native architecture, confirm available VM resources, stop unrelated heavy workloads if appropriate, run one experiment process at a time, inspect outcome and cleanup errors, and retain provenance with any shared result. Do not interpret a laptop Docker Desktop run as a native-Linux performance result.
