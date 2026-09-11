"""Optional preflight enforcement probe.

Runs a separate reviewed image with a profile's exact flags and reads that container's own
cgroup interface files. Candidate workloads and verifiers are never modified or inspected.
"""

import dataclasses
import json
import math
from pathlib import Path

from .config import cpuset, Experiment, Task
from .docker import Docker
from .runner import execute
from .storage import write_json
from .verification import envelope

CLAIM = ("Shows the limits this engine applied to a container created with this profile. "
         "It does not prove dedicated CPUs, scheduler fairness, freedom from host or VM "
         "interference, or that limits stay constant during later trials.")


def expectations(profile):
    expected = {"memory_max": str(profile.memory_mb * 1024**2), "memory_swap_max": "0",
                "pids_max": "128"}
    quota = round(profile.cpus * 100000)
    expected["cpu_max"] = f"{quota} 100000"
    return expected


def evaluate(payload, profile):
    if not payload.get("cgroup_v2_unified"):
        return {"conclusive": False, "matches": {}, "mismatches": [],
                "reason": payload.get("inconclusive_reason") or "cgroup v2 was not observed"}
    files = payload.get("files") or {}
    mismatches, matches = [], {}
    for key, want in expectations(profile).items():
        observed = files.get(key)
        matches[key] = {"expected": want, "observed": observed}
        if observed != want:
            mismatches.append(key)
    affinity = {"requested": profile.cpuset_cpus,
                "cpuset_cpus": files.get("cpuset_cpus"),
                "cpuset_cpus_effective": files.get("cpuset_cpus_effective"),
                "online_cpus": payload.get("online_cpus")}
    if profile.cpuset_cpus:
        wanted = set(cpuset(profile.cpuset_cpus))
        effective = files.get("cpuset_cpus_effective")
        try:
            actual = set(cpuset(effective)) if isinstance(effective, str) and effective else set()
        except Exception:
            actual = set()
        affinity["effective_is_requested"] = actual == wanted
        affinity["effective_is_subset"] = bool(actual) and actual <= wanted
        if actual != wanted:
            mismatches.append("cpuset_cpus_effective")
    if isinstance(payload.get("online_cpus"), int) and payload["online_cpus"] < math.ceil(profile.cpus):
        mismatches.append("online_cpus")
    return {"conclusive": True, "matches": matches, "mismatches": mismatches,
            "affinity": affinity, "enforced_as_requested": not mismatches, "reason": None}


def experiment_for(image_reference, profiles):
    task = Task("probe", image_reference, ("python", "/opt/probe/probe.py"))
    quiet = tuple(dataclasses.replace(profile, sample_interval_s=0) for profile in profiles)
    return Experiment("probe", 0, 1, (task,), quiet, 0)


def run(backend, image_reference, profiles, output):
    """Runs as an ordinary owned run so the engine lock, orphan refusal, architecture and
    affinity checks, cleanup-failure halting, and diagnose/cleanup recovery all apply."""
    backend = backend or Docker()
    experiment = experiment_for(image_reference, profiles)
    directory = execute(experiment, output, backend)
    manifest = json.loads((directory / "manifest.json").read_text())
    by_id = {profile.id: profile for profile in experiment.profiles}
    results = []
    for batch in manifest["plan"]:
        profile = by_id[batch["profile"]]
        for planned in batch["trials"]:
            path = directory / "trials" / f"{planned['id']}.json"
            entry = {"profile": profile.id, "trial": planned["id"], "execution_status": "missing",
                     "resource_audit": None, "payload": None, "evaluation": None,
                     "error": "No trial artifact was recorded"}
            if path.exists():
                record = json.loads(path.read_text())
                entry.update(execution_status=record["status"],
                             resource_audit=record.get("resource_audit"), error=None)
                if record["status"] == "passed":
                    try:
                        value, _ = envelope(record.get("logs", {}), "evalnoise_probe")
                        entry["payload"] = value["payload"]
                        entry["evaluation"] = evaluate(value["payload"], profile)
                    except ValueError as error:
                        entry["error"] = str(error)
                else:
                    entry["error"] = record.get("error") or "Probe container did not complete"
            results.append(entry)
    summary = {"schema_version": 1, "run_id": manifest["run_id"], "directory": str(directory),
               "status": manifest["status"], "image": manifest["images"][image_reference],
               "engine_identity": (manifest.get("environment") or {}).get("engine_identity"),
               "engine_identity_final": manifest.get("engine_identity_final"),
               "profiles": results, "claim": CLAIM,
               "conclusive": bool(results) and manifest["status"] == "completed" and all(
                   r["evaluation"] and r["evaluation"]["conclusive"] for r in results),
               "enforced_as_requested": bool(results) and all(
                   r["evaluation"] and r["evaluation"].get("enforced_as_requested") for r in results)}
    write_json(directory / "probe-summary.json", summary)
    return summary
