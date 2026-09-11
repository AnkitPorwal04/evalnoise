# Security And Operations

## Trust Model

v0.2 is for reviewed local workloads and trusted verifiers on a Docker engine the operator controls. `--trust-config` acknowledges that a configuration selects executable code. It is not a security proof. Docker access is powerful; the runner is not a safe public multi-tenant execution service.

Do not run unknown image submissions, generated adversarial agent code, private employer datasets, or credential-bearing tasks on your everyday machine. A future adversarial-agent tier requires separately evaluated isolation such as dedicated disposable VMs or a suitable sandbox backend.

## Implemented Containment

Containers run as UID/GID 65534 with all capabilities dropped, no-new-privileges, Docker's normal seccomp policy, a read-only root, no network, no host mounts, PID limit 128, memory/CPU ceilings, and swap disabled. `/tmp` is a 64 MiB noexec/nosuid/nodev tmpfs. Tmpfs use consumes memory; workloads must be compatible with these restrictions. Image-declared volumes and image contents remain part of the trusted image contract.

The host executes argv arrays, never shell-expanded user strings. Image references and identifiers are constrained. The runner does not mount the Docker socket into workloads, expose a web execution endpoint, pass host credentials, or publish artifacts automatically.

## Residual Risks

Independent verification transfers only a validated JSON envelope, at most 8192 UTF-8 bytes, through a base64 environment variable. Base64 is not encryption: Docker administrators can inspect it, and artifacts are retained in reports. The verifier image must not execute candidate data. Candidate-written verdicts are ignored; a verdict is accepted only from the separately executed trusted verifier. This is a correctness boundary for reviewed fixtures, not adversarial isolation or hidden-test confidentiality. Contract hashes detect inconsistent identities, not coordinated tampering.

Container/kernel vulnerabilities, host exhaustion, image-declared behavior, daemon compromise, Docker endpoint changes, host interference, and hard runner termination remain possible. Cgroup driver and swap-warning checks detect some unsupported environments, not complete kernel enforcement. Inspect HostConfig echoes requested settings; it is not an independent controller audit. A shared Docker daemon does not provide dedicated CPU reservations or experiment exclusivity.

Logs and stdout can contain secrets. Exported reports include bounded logs and command metadata. Review artifacts before sharing. The HTML renderer escapes workload logs and uses a no-script CSP with no external assets, but the offline artifact loader is intended for locally generated evidence, not hostile uploads. No signatures or tamper-evident storage exist yet.

## Cancellation And Cleanup

SIGINT and SIGTERM request cooperative cancellation. Running containers are killed and evidence/cleanup is attempted. Docker CLI timeouts do not guarantee immediate daemon response. Cleanup failure stops later batches and records the affected container name.

After SIGKILL, host failure, or daemon outage, inspect only the run you own:

```sh
docker ps -a --filter label=io.evalnoise.run=<run-id>
docker inspect <exact-container-name>
docker rm --force <exact-container-name>
```

Do not sweep every EvalNoise-labeled container: another experiment may be active. Do not remove images, volumes, or unrelated containers as routine cleanup. Keep incomplete artifacts and regenerate the report to expose missing observations. There is no automatic resume; use a new run ID after recovery.

## Operational Checklist

Review config/image/command, prepare images before timing, confirm native architecture, confirm available VM resources, stop unrelated heavy workloads if appropriate, run one experiment process at a time, inspect outcome and cleanup errors, and retain provenance with any shared result. Do not interpret a laptop Docker Desktop run as a native-Linux performance result.
