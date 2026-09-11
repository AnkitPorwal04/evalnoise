"""Docker CLI boundary. Commands never pass through a host shell."""

from datetime import datetime
import base64
import json
import platform
import subprocess


class DockerError(RuntimeError):
    pass


class Docker:
    def call(self, args, timeout=20, merge=False):
        try:
            result = subprocess.run(["docker", *args], capture_output=True, text=True,
                                    errors="replace", timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DockerError(f"docker {args[0]} unavailable or timed out: {error}") from error
        if result.returncode:
            raise DockerError(f"docker {args[0]} failed: {result.stderr[-2000:].strip()}")
        return result.stdout + result.stderr if merge else result.stdout

    def doctor(self):
        version = json.loads(self.call(["version", "--format", "{{json .}}"], 10))
        info = json.loads(self.call(["info", "--format", "{{json .}}"], 10))
        if info.get("OSType") != "linux":
            raise DockerError("Only Linux containers are supported")
        if info.get("CgroupDriver") in (None, "", "none"):
            raise DockerError("A working cgroup driver is required to enforce resource limits")
        warnings = info.get("Warnings") or []
        if any("swap limit" in warning.lower() for warning in warnings):
            raise DockerError("Engine reports unsupported swap limits; refusing a no-swap experiment")
        return {"client_platform": platform.platform(), "client_machine": platform.machine(),
                "python": platform.python_version(), "docker_client": version["Client"]["Version"],
                "docker_server": version["Server"]["Version"],
                "engine": {key: info.get(key) for key in
                           ("OSType", "Architecture", "OperatingSystem", "KernelVersion", "NCPU",
                             "MemTotal", "CgroupVersion", "CgroupDriver", "Driver", "SecurityOptions", "Warnings")},
                "caveat": "Engine resources may belong to a VM. Quotas are ceilings, not reservations."}

    def image(self, reference):
        info = json.loads(self.call(["image", "inspect", reference]))[0]
        if info.get("Os") != "linux":
            raise DockerError("Only Linux images are supported")
        return {"reference": reference, "id": info["Id"], "repo_digests": info.get("RepoDigests", []),
                "os": info["Os"], "architecture": info["Architecture"],
                "variant": info.get("Variant"), "created": info.get("Created")}

    def create(self, name, run_id, task, profile, image, seed, artifact=None):
        extra = []
        if artifact is not None:
            if len(artifact) > 8192:
                raise DockerError("Verifier artifact exceeds 8192 bytes")
            extra = ["--env", "EVALNOISE_ARTIFACT_B64=" + base64.b64encode(artifact).decode("ascii")]
        return self.call([
            "create", "--name", name, "--label", f"io.evalnoise.run={run_id}", "--pull", "never",
            "--init", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", "65534:65534", "--pids-limit", "128",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m", "--workdir", "/tmp",
            "--log-driver", "local", "--log-opt", "max-size=1m", "--log-opt", "max-file=2",
            "--cpus", str(profile.cpus), "--memory", f"{profile.memory_mb}m",
            "--memory-swap", f"{profile.memory_mb}m", "--env", f"EVALNOISE_SEED={seed}",
            *extra, "--entrypoint", task.command[0], image["id"], *task.command[1:]
        ]).strip()

    def inspect(self, name):
        info = json.loads(self.call(["inspect", name]))[0]
        host = info["HostConfig"]
        return {"state": info["State"], "image": info["Image"],
                "resources": {key: host.get(key) for key in
                              ("NanoCpus", "Memory", "MemorySwap", "PidsLimit", "NetworkMode",
                               "ReadonlyRootfs")}}

    def stats(self, name):
        output = self.call(["stats", "--no-stream", "--format", "{{json .}}", name], 5)
        return json.loads(output)

    def logs(self, name):
        # Docker stores bounded rotating logs; publication truncates further.
        output = self.call(["logs", "--timestamps", "--tail", "2001", name], merge=True)
        lines = output.splitlines(keepends=True)
        retained = "".join(lines[-2000:])
        return {"text": retained[-262144:], "truncated": len(lines) > 2000 or len(retained) > 262144,
                "scope": "Last 2000 lines, at most 262144 characters; stdout then stderr, not interleaved. Engine rotation also applies."}

    def remove(self, name):
        try:
            self.call(["rm", "--force", name])
        except DockerError as error:
            # Confirm absence through a successful engine query, not localized stderr.
            remaining = self.call(["ps", "--all", "--filter", f"name=^/{name}$", "--format", "{{.ID}}"])
            if remaining.strip():
                raise


def classify(state, timed_out=False, cancelled=False, expected_exit=0):
    if cancelled:
        return "cancelled"
    if timed_out:
        return "timeout"
    if state.get("OOMKilled") is True:
        if state.get("ExitCode") == expected_exit:
            return "oom_observed_expected_exit"
        return "oom_killed"
    if state.get("Error"):
        return "runtime_error"
    if state.get("Running") is False and state.get("Status") == "exited":
        return "passed" if state.get("ExitCode") == expected_exit else "workload_failed"
    return "unknown"


def container_duration(state):
    try:
        start = datetime.fromisoformat(state["StartedAt"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(state["FinishedAt"].replace("Z", "+00:00"))
        duration = (end - start).total_seconds()
        return duration if duration >= 0 and start.year > 2000 else None
    except (KeyError, ValueError, TypeError):
        return None
