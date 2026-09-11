"""Read-only diagnosis and narrowly scoped, explicitly confirmed cleanup.

Diagnosis never writes to a run directory. Cleanup removes only containers that match
this manifest's expected names, run label, and immutable image identity, and removes
them by resolved full container ID so a name cannot be re-pointed between the check and
the removal. Neither command resumes a run, rewrites a trial status, or invents verdicts.
"""

import json
from pathlib import Path
import re

from .config import ConfigError, parse, plan
from .coordination import CoordinationError, EngineLock, LABEL_ENGINE, LABEL_RUN, live_holder
from .docker import DockerError
from .endpoint import utc
from .storage import write_json

NAME = re.compile(r"^evalnoise-[0-9a-f]{6,32}-[a-z0-9][a-z0-9_-]{0,63}(-verify)?$")
FULL_ID = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


class RecoveryError(RuntimeError):
    pass


def load_manifest(directory):
    path = Path(directory) / "manifest.json"
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise RecoveryError(f"Unreadable manifest: {error}") from error
    if not isinstance(manifest, dict):
        raise RecoveryError("Manifest is not a JSON object")
    for key in ("run_id", "config", "plan", "images"):
        if key not in manifest:
            raise RecoveryError(f"Manifest is missing required key {key!r}")
    if not re.fullmatch(r"[0-9a-f]{6,32}", str(manifest["run_id"])):
        raise RecoveryError("Manifest run_id is not a plain hexadecimal identifier")
    try:
        experiment = parse(manifest["config"])
    except ConfigError as error:
        raise RecoveryError(f"Manifest configuration is not valid: {error}") from error
    if manifest["plan"] != plan(experiment):
        raise RecoveryError(
            "Manifest plan does not match the schedule its own configuration and seed produce; "
            "refusing to derive container names from an inconsistent plan")
    if not isinstance(manifest["images"], dict):
        raise RecoveryError("Manifest images is not a JSON object")
    return manifest


def expected_names(manifest):
    """Names and their stages are derived from the validated plan, never from free text."""
    run_id = manifest["run_id"]
    tasks = {task["id"]: task for task in manifest["config"]["tasks"]}
    names = {}
    for batch in manifest["plan"]:
        for trial in batch["trials"]:
            base = f"evalnoise-{run_id}-{trial['id']}"
            names[base] = (trial["task"], "workload")
            if tasks[trial["task"]].get("verifier"):
                names[base + "-verify"] = (trial["task"], "verifier")
    for name in names:
        if not NAME.fullmatch(name):
            raise RecoveryError(f"Refusing a container name that is not plan-derived: {name!r}")
    return names


def stage_image(manifest, task_id, stage):
    """Each stage has exactly one expected image identity; a union would accept the wrong one."""
    task = next(t for t in manifest["config"]["tasks"] if t["id"] == task_id)
    reference = task["image"] if stage == "workload" else (task.get("verifier") or {})["image"]
    identity = (manifest["images"].get(reference) or {}).get("id")
    if not isinstance(identity, str) or not IMAGE_ID.fullmatch(identity):
        raise RecoveryError(
            f"Manifest has no valid image identity for {task_id!r} {stage}; refusing to match "
            "containers against a missing or malformed image ID")
    return identity


def _present(backend, name):
    listing = backend.call(["ps", "--all", "--no-trunc", "--filter", f"name=^/{name}$",
                            "--format", "{{json .}}"], 20)
    entries = []
    for line in listing.splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            raise RecoveryError(f"Unreadable container listing for {name!r}") from None
    return [entry for entry in entries if str(entry.get("Names") or "") == name]


def survey(backend, manifest):
    names = expected_names(manifest)
    run_id = manifest["run_id"]
    engine_id = ((manifest.get("environment") or {}).get("engine_identity") or {}).get("id")
    if not engine_id:
        raise RecoveryError("Manifest records no engine identity; refusing to match containers")
    found = []
    for name, (task, stage) in sorted(names.items()):
        if not _present(backend, name):
            continue
        expected_image = stage_image(manifest, task, stage)
        try:
            entries = json.loads(backend.call(["inspect", name], 20))
        except ValueError as error:
            raise RecoveryError(f"Unreadable inspection for {name!r}: {error}") from error
        if not isinstance(entries, list) or len(entries) != 1:
            raise RecoveryError(f"Ambiguous inspection result for {name!r}")
        info = entries[0]
        labels = (info.get("Config") or {}).get("Labels") or {}
        container_id = str(info.get("Id") or "")
        reasons = []
        if str(info.get("Name") or "") != "/" + name:
            reasons.append(f"engine reports name {info.get('Name')!r}, not {name!r}")
        if labels.get(LABEL_RUN) != run_id:
            reasons.append(f"{LABEL_RUN} label is {labels.get(LABEL_RUN)!r}, not this run")
        if labels.get(LABEL_ENGINE) != engine_id:
            reasons.append(f"{LABEL_ENGINE} label is {labels.get(LABEL_ENGINE)!r}, not this engine")
        if info.get("Image") != expected_image:
            reasons.append("image identity does not match the manifest for this stage")
        if not FULL_ID.fullmatch(container_id):
            reasons.append("engine did not report a full container ID")
        found.append({"name": name, "task": task, "stage": stage, "container_id": container_id,
                      "state": (info.get("State") or {}).get("Status"),
                      "running": bool((info.get("State") or {}).get("Running")),
                      "owned": not reasons, "refusals": reasons})
    return found


def diagnose(backend, directory):
    directory = Path(directory)
    manifest = load_manifest(directory)
    trials = sorted(path.name for path in (directory / "trials").glob("*.json")) \
        if (directory / "trials").is_dir() else []
    planned = [trial["id"] for batch in manifest["plan"] for trial in batch["trials"]]
    recorded = {name[:-5] for name in trials}
    engine = (manifest.get("environment") or {}).get("engine_identity") or {}
    observed_engine = None
    engine_error = None
    try:
        observed_engine = backend.identity()["id"]
    except Exception as error:
        engine_error = f"{type(error).__name__}: {error}"
    containers = []
    survey_error = None
    if observed_engine and engine.get("id") and observed_engine != engine["id"]:
        survey_error = "Connected daemon ID differs from the manifest; no container survey attempted"
    else:
        try:
            containers = survey(backend, manifest)
        except Exception as error:
            survey_error = f"{type(error).__name__}: {error}"
    return {"schema_version": 1, "read_only": True, "generated_at": utc(),
            "run_id": manifest["run_id"], "run_status": manifest.get("status"),
            "directory": str(directory.resolve()),
            "planned_trials": len(planned), "recorded_trials": len(recorded),
            "missing_trials": sorted(set(planned) - recorded),
            "pending_verification": sorted(
                name[:-5] for name in trials
                if json.loads((directory / "trials" / name).read_text()).get("status") == "pending_verification"),
            "manifest_engine_id": engine.get("id"), "observed_engine_id": observed_engine,
            "engine_error": engine_error, "engine_matches": bool(
                observed_engine and engine.get("id") and observed_engine == engine["id"]),
            "containers": containers, "survey_error": survey_error,
            "live_coordinator": live_holder(engine["id"]) if engine.get("id") else None,
            "note": ("Diagnosis is read-only. It does not resume, reclassify, or complete a run. "
                     "Missing trials remain missing observations.")}


def cleanup(backend, directory, confirm=False):
    directory = Path(directory)
    manifest = load_manifest(directory)
    if not confirm:
        raise RecoveryError("Cleanup requires --confirm; run `evalnoise diagnose` first")
    engine = (manifest.get("environment") or {}).get("engine_identity") or {}
    observed = backend.identity()["id"]
    if not engine.get("id") or observed != engine["id"]:
        raise RecoveryError(
            f"Connected daemon ID {observed!r} does not match the manifest engine "
            f"{engine.get('id')!r}; refusing to remove anything")
    # Holding the lock for validation and removal prevents a check-then-race against any
    # coordinator, including a live run of this same run_id that still owns these containers.
    lock = EngineLock(engine["id"])
    try:
        lock.acquire(f"cleanup-{manifest['run_id']}", directory)
    except CoordinationError as error:
        raise RecoveryError(
            f"An EvalNoise process is coordinating this engine; refusing to remove containers "
            f"underneath it. {error}") from error
    try:
        candidates = survey(backend, manifest)
        refused = [entry for entry in candidates if not entry["owned"]]
        if refused:
            raise RecoveryError(
                "Refusing the entire cleanup because some expected names are not owned by this run: "
                + "; ".join(f"{entry['name']}: {', '.join(entry['refusals'])}" for entry in refused))
        removed, failures = [], []
        for entry in candidates:
            try:
                backend.call(["rm", "--force", entry["container_id"]], 30)
                removed.append(entry)
            except DockerError as error:
                failures.append({**entry, "error": str(error)})
    finally:
        lock.release()
    audit = {"schema_version": 1, "generated_at": utc(), "run_id": manifest["run_id"],
             "engine_id": engine["id"], "examined": candidates, "removed": removed,
             "failures": failures, "coordinator_held": lock.token,
             "note": ("Container removal only. Trial statuses, verdicts, and the manifest status "
                      "are unchanged; an interrupted run stays interrupted with missing trials. "
                      "Images, volumes, and networks were not touched and nothing was pruned.")}
    write_json(directory / "cleanup.json", audit)
    return audit
