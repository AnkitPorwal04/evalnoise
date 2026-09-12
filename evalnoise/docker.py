"""Docker CLI boundary. Commands never pass through a host shell."""

from datetime import datetime
import base64
import json
import os
import platform
import subprocess

from . import endpoint as endpoints
from .config import ConfigError, cpuset


ECHO_FIELDS = ("NanoCpus", "CpuPeriod", "CpuQuota", "CpusetCpus", "CpusetMems", "Memory",
               "MemorySwap", "MemorySwappiness", "MemoryReservation", "PidsLimit", "OomKillDisable",
               "NetworkMode", "ReadonlyRootfs", "CapDrop", "SecurityOpt")


class DockerError(RuntimeError):
    pass


class EnforcementError(DockerError):
    pass


def affinity_mismatch(requested, observed):
    """`0-1` and `0,1` are the same mask, so compare parsed sets. An echo that cannot be
    parsed is a mismatch, never an assumed match."""
    if not isinstance(observed, str):
        return {"field": "CpusetCpus", "requested": requested or "",
                "observed": observed, "reason": "engine echoed a non-string CPU mask"}
    if not requested:
        if observed.strip():
            return {"field": "CpusetCpus", "requested": "", "observed": observed,
                    "reason": "engine applied a CPU mask that was not requested"}
        return None
    if not observed.strip():
        return {"field": "CpusetCpus", "requested": requested, "observed": observed,
                "reason": "engine recorded no CPU mask"}
    try:
        wanted, echoed = set(cpuset(requested)), set(cpuset(observed))
    except ConfigError as error:
        return {"field": "CpusetCpus", "requested": requested, "observed": observed,
                "reason": f"unreadable CPU mask: {error}"}
    if wanted != echoed:
        return {"field": "CpusetCpus", "requested": requested, "observed": observed,
                "reason": "engine recorded a different set of CPUs"}
    return None


def audit(profile, resources, pids_limit=128):
    requested_mask = getattr(profile, "cpuset_cpus", None)
    expected = {"NanoCpus": round(profile.cpus * 10**9),
                "Memory": profile.memory_mb * 1024**2,
                "MemorySwap": profile.memory_mb * 1024**2,
                "PidsLimit": pids_limit, "NetworkMode": "none", "ReadonlyRootfs": True}
    mismatches = [{"field": field, "requested": value, "observed": resources.get(field)}
                  for field, value in expected.items() if resources.get(field) != value]
    affinity = affinity_mismatch(requested_mask, resources.get("CpusetCpus"))
    if affinity:
        mismatches.append(affinity)
    expected["CpusetCpus"] = requested_mask or ""
    return {"requested": expected, "observed": {k: resources.get(k) for k in ECHO_FIELDS},
            "mismatches": mismatches, "enforced_as_requested": not mismatches,
            "scope": ("HostConfig echo of what this engine recorded for the request. It is not an "
                      "independent controller audit and implies no dedicated CPU reservation.")}


class Docker:
    def __init__(self, endpoint=None):
        self.endpoint = endpoint
        self.telemetry = {"telemetry_source": "cli_snapshot", "reason": "Endpoint not resolved yet"}
        self.engine_identity = {}
        self.owner_token = None
        self.environment = dict(os.environ)

    def call(self, args, timeout=20, merge=False):
        flags = endpoints.cli_flags(self.endpoint) if self.endpoint else []
        environment = (endpoints.child_environment(self.environment, self.endpoint)
                       if self.endpoint else self.environment)
        try:
            result = subprocess.run(["docker", *flags, *args], capture_output=True, text=True,
                                    errors="replace", timeout=timeout, env=environment)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DockerError(f"docker {args[0]} unavailable or timed out: {error}") from error
        if result.returncode:
            raise DockerError(
                f"docker {args[0]} failed (exit {result.returncode}): "
                f"{result.stderr[-2000:].strip() or 'no stderr diagnostics'}")
        return result.stdout + result.stderr if merge else result.stdout

    def _ambient(self, args, timeout=20):
        try:
            result = subprocess.run(["docker", *args], capture_output=True, text=True,
                                    errors="replace", timeout=timeout, env=self.environment)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DockerError(f"docker {args[0]} unavailable or timed out: {error}") from error
        if result.returncode:
            raise DockerError(f"docker {args[0]} failed: {result.stderr[-2000:].strip()}")
        return result.stdout

    def pin(self):
        if self.endpoint is None:
            self.endpoint = endpoints.resolve(self._ambient, self.environment)
        return endpoints.require_stable_endpoint(self.endpoint)

    def identity(self):
        self.pin()
        info = json.loads(self.call(["info", "--format", "{{json .}}"], 10))
        return {"id": info.get("ID"), "server_version": info.get("ServerVersion"),
                "cgroup_version": info.get("CgroupVersion"), "ncpu": info.get("NCPU")}

    def doctor(self):
        self.pin()
        version = json.loads(self.call(["version", "--format", "{{json .}}"], 10))
        info = json.loads(self.call(["info", "--format", "{{json .}}"], 10))
        if info.get("OSType") != "linux":
            raise DockerError("Only Linux containers are supported")
        if info.get("CgroupDriver") in (None, "", "none"):
            raise DockerError("A working cgroup driver is required to enforce resource limits")
        warnings = info.get("Warnings") or []
        if any("swap limit" in warning.lower() for warning in warnings):
            raise DockerError("Engine reports unsupported swap limits; refusing a no-swap experiment")
        if not info.get("ID"):
            raise DockerError("Engine did not report a daemon ID; run coordination cannot be keyed")
        server = version.get("Server") or {}
        self.telemetry = endpoints.negotiate(self.endpoint, server.get("ApiVersion"),
                                             server.get("MinAPIVersion"))
        return {"client_platform": platform.platform(), "client_machine": platform.machine(),
                "python": platform.python_version(), "docker_client": version["Client"]["Version"],
                "docker_server": version["Server"]["Version"],
                "endpoint": self.endpoint, "telemetry": self.telemetry,
                "telemetry_support": endpoints.availability(info.get("CgroupVersion")),
                "engine_identity": self._remember(info),
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
        engine_id = (self.engine_identity or {}).get("id")
        if engine_id:
            extra += ["--label", f"io.evalnoise.engine={engine_id}"]
        if self.owner_token:
            extra += ["--label", f"io.evalnoise.owner={self.owner_token}"]
        if getattr(profile, "cpuset_cpus", None):
            extra += ["--cpuset-cpus", profile.cpuset_cpus]
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

    def _remember(self, info):
        self.engine_identity = {"id": info.get("ID"), "name": info.get("Name"),
                                "server_version": info.get("ServerVersion"),
                                "cgroup_version": info.get("CgroupVersion"), "ncpu": info.get("NCPU")}
        return self.engine_identity

    def inspect(self, name):
        info = json.loads(self.call(["inspect", name]))[0]
        host = info["HostConfig"]
        return {"state": info["State"], "image": info["Image"],
                "container_id": info.get("Id"),
                "labels": (info.get("Config") or {}).get("Labels") or {},
                "resources": {key: host.get(key) for key in ECHO_FIELDS}}

    def stats(self, name):
        output = self.call(["stats", "--no-stream", "--format", "{{json .}}", name], 5)
        return json.loads(output)

    def stream(self, container_id, interval, started):
        if self.telemetry.get("telemetry_source") != "engine_stream":
            return None
        if self.endpoint is None:
            raise DockerError("Streaming requires a pinned Docker endpoint")
        return endpoints.StatsStream(self.endpoint["socket_path"],
                                     self.telemetry["api_version_pinned"],
                                     container_id, interval, started).start()

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
