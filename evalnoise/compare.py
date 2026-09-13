"""Offline paired comparison of two measured arms.

An arm is one profile inside one recorded run. Two arms may live in the same run
(the ordinary within-run contrast) or in two separate runs (the cross-run
contrast). Nothing here contacts Docker, a provider, or a network.

The contrast must be named on the command line before the numbers are produced.
There is no mode that scans profiles and reports the largest observed difference,
because selecting a contrast after seeing the differences is what makes an
interval meaningless.
"""

from collections import Counter
import csv
import html
import json
from math import isfinite
from pathlib import Path
import statistics

from . import __version__
from .report import STYLE, summarize
from .storage import write_json

WITHHELD = "interval_withheld_pending_methodology_review"

# Appended after the shared report style rather than merged into it, so the
# experiment report's HTML stays byte-identical. The evidence keys this report
# prints are long unbroken tokens; without an explicit wrap they widen the page
# and every `.scroll` container then sizes to the widened body, which produced a
# real 28px desktop and 158px mobile horizontal overflow.
COMPARISON_STYLE = (".notice,.notice strong,article h3,article strong,li,caption"
                    "{overflow-wrap:anywhere}td,th{overflow-wrap:anywhere}")

WITHHELD_DETAIL = (
    "No uncertainty interval is published. The resampling method this project "
    "prototyped has not passed independent methodology review and its own "
    "characterisation shows it is not fit to publish: the cluster-count floor it used "
    "was derived by treating bootstrap multisets as equiprobable, which they are not, "
    "and measured coverage at that floor is 0.850 against a nominal 0.95 with a 0.150 "
    "false-positive rate under A/A. The target of inference is also unsettled, because "
    "resampling tasks presumes the fixed configured suite is an exchangeable sample "
    "from a population that it is not. The figures below are descriptive summaries of "
    "the recorded pairs and carry no error bar, no significance, and no coverage claim.")


class CompareError(ValueError):
    pass


PASSED = "passed"

UNRESOLVED_STATUSES = {
    "pending_verification": "successful execution still awaiting the trusted verifier",
    "cancelled": "runner intervention, so the workload outcome was never observed",
    "agent_incomplete": "durable checkpoint from an interrupted loop, not a final outcome",
    "unknown": "evidence does not establish a supported terminal outcome",
}

INFRASTRUCTURE_STATUSES = {
    "setup_error", "runtime_error", "runner_error", "enforcement_error", "timeout",
    "artifact_error", "verifier_error", "agent_error", "budget_exhausted",
    "step_limit_reached",
}

VERIFICATION_STATUSES = {"verification_failed"}

WORKLOAD_STATUSES = {"workload_failed", "oom_killed", "oom_observed_expected_exit"}

RESOLVED_STATUSES = ({PASSED} | INFRASTRUCTURE_STATUSES | VERIFICATION_STATUSES
                     | WORKLOAD_STATUSES)

TREATMENT_KEYS = (
    "cpus", "memory_mb", "timeout_s", "concurrency", "cpuset_cpus", "sample_interval_s",
    "repeats", "experiment_sample_interval_s",
    "engine_daemon_id", "engine_server_version", "engine_cgroup_version", "engine_ncpu",
    "evalnoise_version",
)

ENGINE_TREATMENT_KEYS = ("engine_daemon_id", "engine_server_version",
                         "engine_cgroup_version", "engine_ncpu")

ESTIMANDS = {
    "jointly_resolved_success_task_weighted": (
        "Task-weighted mean paired success difference over jointly resolved pairs",
        "Candidate minus baseline of an indicator that the trial reached final "
        "`passed`, averaged within each task and then averaged unweighted across "
        "tasks. Only pairs where **both** arms recorded a resolved outcome are "
        "included. Every resolved non-pass counts as a zero, including recorded "
        "infrastructure and verifier errors, so this describes end-to-end recorded "
        "success and not correctness among resolved answers. It is a descriptive "
        "summary of the pairs that survived, not an estimate of any population."),
    "jointly_resolved_infrastructure_error_task_weighted": (
        "Task-weighted mean paired infrastructure-error difference over jointly resolved pairs",
        "Candidate minus baseline of an indicator that the trial ended in a "
        "recorded harness, engine, verifier-execution, agent-loop, or budget "
        "failure, averaged within task then unweighted across tasks. A negative "
        "value means the candidate recorded fewer such failures among the pairs "
        "that survived. This is not a claim about correctness."),
    "jointly_passing_duration_task_weighted": (
        "Task-weighted mean paired duration difference over jointly passing pairs",
        "Candidate minus baseline of `container_duration_s`, over pairs where "
        "**both** trials passed and both recorded a container duration, averaged "
        "within task then unweighted across tasks. Conditioning on joint success "
        "selects a subset of pairs, so this is not a speedup of the workload in "
        "general and must never be compared with an unmatched survivor median."),
}

CASE_BASIS_COMPLETE = "complete_pair"
CASE_BASIS_SELECTED = "selected_case"


def score_success(trial):
    return 1.0 if trial["status"] == PASSED else 0.0


def score_infrastructure(trial):
    return 1.0 if trial["status"] in INFRASTRUCTURE_STATUSES else 0.0


KNOWN_STATUSES = RESOLVED_STATUSES | set(UNRESOLVED_STATUSES)


def classify(status):
    if status == PASSED:
        return "passed"
    if status in INFRASTRUCTURE_STATUSES:
        return "infrastructure_error"
    if status in VERIFICATION_STATUSES:
        return "verification_failed"
    if status in WORKLOAD_STATUSES:
        return "workload_outcome"
    if status in UNRESOLVED_STATUSES:
        return "unresolved"
    raise CompareError(f"Unrecognised trial status {status!r}")


def check_statuses(trials, run_id):
    """Refuse a status this version does not know how to score.

    Silently bucketing an unknown status would make it a non-pass by default,
    which is a scoring decision disguised as a fallthrough. Statuses are sorted by
    `repr` rather than natural order, because a mixed-type set (a stray null or
    integer beside the strings) raises an uncontrolled `TypeError` from `sorted`.
    """
    unknown = {trial.get("status") for trial in trials} - KNOWN_STATUSES
    if unknown:
        raise CompareError(
            f"Run {run_id} contains trial status(es) {sorted(unknown, key=repr)} that this "
            f"version does not classify. Refusing rather than scoring an unknown status as "
            f"a non-pass. Known statuses are {sorted(KNOWN_STATUSES)}.")


def check_seeds(arm):
    """Every retained trial must carry the repetition seed its plan assigned it.

    A repetition index is only a label; the seed is what actually determines the
    workload. A trial whose seed does not match the plan was not the planned
    observation, so pairing on `(task, repeat)` would silently pair two different
    workloads.
    """
    run_id = arm["manifest"].get("run_id")
    planned = {}
    for batch in arm["manifest"]["plan"]:
        if batch["profile"] != arm["profile_id"]:
            continue
        for specification in batch["trials"]:
            planned[(specification["task"], batch["repeat"])] = specification.get("seed")
    for (task, repeat), trial in arm["lookup"].items():
        expected = planned.get((task, repeat))
        if expected is None:
            raise CompareError(
                f"Run {run_id} plans no seed for task {task!r} repeat {repeat}, so a recorded "
                f"trial cannot be checked against it.")
        if trial.get("seed") != expected:
            raise CompareError(
                f"Trial {trial['id']!r} in run {run_id} records seed {trial.get('seed')!r} but "
                f"its plan assigns {expected!r}. The retained record is not the planned "
                f"observation and will not be paired.")
    return planned


def check_finite(value, what, trial_id):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompareError(f"Trial {trial_id} has a non-numeric {what}: {value!r}")
    if not isfinite(value):
        raise CompareError(
            f"Trial {trial_id} has a non-finite {what} ({value!r}). NaN and infinity are "
            f"refused rather than propagated into a mean.")
    return float(value)


def load_run(directory):
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise CompareError(f"No manifest.json under {directory}; this is not a run directory")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CompareError(f"Unreadable manifest in {directory}: {error}") from error
    if not isinstance(manifest, dict) or "config" not in manifest or "plan" not in manifest:
        raise CompareError(f"Manifest in {directory} has no config or plan; refusing to guess")
    trials = []
    for path in sorted((directory / "trials").glob("*.json")):
        try:
            trials.append(json.loads(path.read_text()))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise CompareError(f"Unreadable trial artifact {path.name}: {error}") from error
    try:
        # The existing report validation is the gate: duplicate, unexpected,
        # plan-mismatched, or contract-mismatched evidence is refused there, and a
        # comparison must not accept evidence a report would reject.
        summary = summarize(manifest, trials)
    except (ValueError, KeyError, TypeError) as error:
        raise CompareError(
            f"Run {directory} fails the existing report validation and cannot be "
            f"compared: {error}") from error
    return {"directory": directory, "manifest": manifest, "trials": trials, "summary": summary}


def arm(run, profile_id):
    profiles = {profile["id"]: profile for profile in run["manifest"]["config"]["profiles"]}
    if profile_id not in profiles:
        raise CompareError(
            f"Run {run['manifest'].get('run_id')} has no profile {profile_id!r}; "
            f"it declares {sorted(profiles)}")
    selected = [trial for trial in run["trials"] if trial["profile"] == profile_id]
    lookup = {}
    for trial in selected:
        key = (trial["task"], trial["repeat"])
        if key in lookup:
            raise CompareError(f"Duplicate record for task {key[0]!r} repeat {key[1]}")
        lookup[key] = trial
    planned = sum(len(batch["trials"]) for batch in run["manifest"]["plan"]
                  if batch["profile"] == profile_id)
    return {**run, "profile_id": profile_id, "profile": profiles[profile_id],
            "trials": selected, "lookup": lookup, "planned": planned}


def contract_identity(manifest):
    contracts = manifest.get("task_contracts")
    if isinstance(contracts, dict) and contracts:
        return contracts
    return None


ENGINE_FIELDS = (("engine_daemon_id", "id"), ("engine_server_version", "server_version"),
                 ("engine_cgroup_version", "cgroup_version"), ("engine_ncpu", "ncpu"))


def engine_facts(manifest, run_id):
    """Read the engine identity actually written by `docker.py`, failing closed.

    The keys are `id`, `server_version`, `cgroup_version` and `ncpu`, all
    lowercase. An earlier version read Docker-API casing (`ID`, `ServerVersion`,
    `CgroupVersion`, `CPUs`), which exists nowhere in a real manifest, so every
    lookup returned None and two different daemons compared equal as None ==
    None. That silently disabled the entire engine check, so a missing or
    unreadable identity is now an error rather than a None.
    """
    environment = manifest.get("environment") or {}
    identity = environment.get("engine_identity")
    if not isinstance(identity, dict) or not identity:
        raise CompareError(
            f"Run {run_id} records no environment.engine_identity, so the daemon it ran "
            f"against cannot be established. A cross-run contrast will not assume two "
            f"runs shared an engine. Compare profiles inside one run instead.")
    facts = {}
    for name, key in ENGINE_FIELDS:
        if key not in identity or identity[key] is None:
            raise CompareError(
                f"Run {run_id} records engine identity without {key!r}. Refusing to "
                f"compare across runs on incomplete engine provenance.")
        facts[name] = identity[key]
    check_engine_stability(manifest, run_id, required=True)
    return facts


def check_engine_stability(manifest, run_id, *, required):
    """An explicitly unstable engine refuses any contrast, within-run included.

    `stable` must be exactly `True`, not merely truthy: a string like `"no"` or
    `"false"` is truthy in Python and would otherwise pass a stability check that
    the manifest is actively reporting as failed.

    `required` is False within a single run, where older artifacts legitimately
    predate the field. An absent record is then unknown rather than unstable, and
    is surfaced as a warning instead of a refusal. An explicitly false record is
    refused in both scopes.
    """
    final = manifest.get("engine_identity_final")
    if final is None or (isinstance(final, dict) and "stable" not in final):
        if required:
            raise CompareError(
                f"Run {run_id} records no engine_identity_final, so it is unknown whether the "
                f"daemon stayed the same for the whole run.")
        return "unknown"
    if not isinstance(final, dict):
        raise CompareError(
            f"Run {run_id} records a non-object engine_identity_final ({final!r}).")
    if final["stable"] is not True:
        raise CompareError(
            f"Run {run_id} reports engine_identity_final.stable as {final['stable']!r}, which "
            f"is not True (expected {final.get('expected')!r}, observed "
            f"{final.get('observed')!r}). The daemon changed, became unreachable, or the flag "
            f"is not a boolean, so these trials are not attributable to one engine and will "
            f"not be contrasted.")
    return "stable"


def difference(key, left, right):
    return {"key": key, "baseline": left, "candidate": right, "equal": left == right}


def compatibility(baseline, candidate, declared, same_run):
    """Fail closed on identity, allow only declared resource differences.

    The whole configuration hash is deliberately not compared. A treatment lives
    inside that hash, so comparing it would refuse every real contrast; the check
    is structural instead, field by field, with identity separated from treatment.
    """
    declared = set(declared)
    unknown = declared - set(TREATMENT_KEYS)
    if unknown:
        raise CompareError(
            f"Unknown treatment key(s) {sorted(unknown)}. Declarable keys are "
            f"{list(TREATMENT_KEYS)}. Task, verifier, image, provider, agent, and "
            f"model identity are never declarable.")
    left, right = baseline["manifest"], candidate["manifest"]
    fatal = []

    def refuse(what, a, b):
        fatal.append({"key": what, "baseline": a, "candidate": b})

    if left.get("schema_version") != right.get("schema_version"):
        refuse("schema_version", left.get("schema_version"), right.get("schema_version"))
    if left.get("measurement_kind") != right.get("measurement_kind"):
        refuse("measurement_kind", left.get("measurement_kind"), right.get("measurement_kind"))
    left_tasks = [task["id"] for task in left["config"]["tasks"]]
    right_tasks = [task["id"] for task in right["config"]["tasks"]]
    if left_tasks != right_tasks:
        refuse("task_set", left_tasks, right_tasks)
    # The configured seed is identity, never a treatment. Pairing is by
    # (task, repeat), but a repetition index is only a label: the seed is what
    # determines the workload. Two runs with different configured seeds assign
    # different seeds to the same repeat index, so pairing them would contrast two
    # different workloads while reporting the difference as a resource effect.
    if left["config"]["seed"] != right["config"]["seed"]:
        refuse("configured_seed", left["config"]["seed"], right["config"]["seed"])

    attestation = "manifest_structural" if same_run else None
    if not same_run:
        left_contracts, right_contracts = contract_identity(left), contract_identity(right)
        if left_contracts is None or right_contracts is None:
            missing = [run["manifest"].get("run_id")
                       for run, contracts in ((baseline, left_contracts), (candidate, right_contracts))
                       if contracts is None]
            raise CompareError(
                f"Cross-run comparison requires persisted per-task contract hashes in both "
                f"manifests, and run(s) {missing} record none. Artifacts written before "
                f"contract hashes existed cannot establish that two separate runs executed "
                f"the same task, image, and verifier, and that identity will not be guessed. "
                f"Compare profiles inside one of those runs instead.")
        if left_contracts != right_contracts:
            refuse("task_contracts", left_contracts, right_contracts)
        for side in (baseline, candidate):
            for trial in side["trials"]:
                if not trial.get("contract_sha256"):
                    raise CompareError(
                        f"Cross-run comparison requires every used trial to attest its own "
                        f"contract hash; trial {trial['id']!r} in run "
                        f"{side['manifest'].get('run_id')} records none.")
        attestation = "per_trial_contract_sha256"
        left_provider = left.get("provider_snapshot") or {}
        right_provider = right.get("provider_snapshot") or {}
        keys = ("kind", "protocol", "model", "provider_label", "cassette_sha256", "entries")
        if {key: left_provider.get(key) for key in keys} != {key: right_provider.get(key) for key in keys}:
            refuse("provider_snapshot", {key: left_provider.get(key) for key in keys},
                   {key: right_provider.get(key) for key in keys})
        left_images = left.get("images") or {}
        right_images = right.get("images") or {}
        if ({name: value.get("id") for name, value in left_images.items()}
                != {name: value.get("id") for name, value in right_images.items()}):
            refuse("image_ids", {name: value.get("id") for name, value in left_images.items()},
                   {name: value.get("id") for name, value in right_images.items()})
    if fatal:
        listed = ", ".join(entry["key"] for entry in fatal)
        raise CompareError(
            f"Refusing an incompatible contrast. These identity fields differ and are not "
            f"declarable as a treatment: {listed}. Comparing arms whose task, verifier, "
            f"image, provider, model, or schema identity differs would attribute that "
            f"difference to the resource treatment.")

    observed = []
    for key in ("cpus", "memory_mb", "timeout_s", "concurrency", "cpuset_cpus", "sample_interval_s"):
        observed.append(difference(key, baseline["profile"].get(key), candidate["profile"].get(key)))
    if not same_run:
        observed.append(difference("repeats", left["config"]["repeats"], right["config"]["repeats"]))
        observed.append(difference("experiment_sample_interval_s",
                                   left["config"].get("sample_interval_s", 0),
                                   right["config"].get("sample_interval_s", 0)))
        observed.append(difference("evalnoise_version", left.get("evalnoise_version"),
                                   right.get("evalnoise_version")))
        left_engine = engine_facts(left, left.get("run_id"))
        right_engine = engine_facts(right, right.get("run_id"))
        for key in left_engine:
            observed.append(difference(key, left_engine[key], right_engine[key]))
    differing = [entry["key"] for entry in observed if not entry["equal"]]
    undeclared = [key for key in differing if key not in declared]
    if undeclared:
        flags = " ".join(f"--treatment {key}" for key in undeclared)
        raise CompareError(
            f"These fields differ between the arms but were not declared as the treatment: "
            f"{undeclared}. An undeclared difference is a confounder, not a treatment. "
            f"Re-run with {flags} if the difference is deliberate, and read the result as "
            f"a contrast of every declared difference jointly, never of one of them alone.")
    idle = sorted(declared - set(differing))
    return {
        "same_run": same_run,
        "contract_attestation": attestation,
        "declared_treatment": sorted(declared),
        "observed_differences": observed,
        "differing_keys": differing,
        "declared_but_identical": idle,
        "checked_identity": sorted([
            "schema_version", "measurement_kind", "task_set"]
            + ([] if same_run else ["task_contracts", "per_trial_contract_sha256",
                                    "provider_snapshot", "image_ids"])),
        "attestation_note": (
            "Both arms come from one manifest, one plan, and one image resolution, so task "
            "identity is structural. These artifacts predate per-trial contract hashes, "
            "which is why the same artifacts cannot be used for a cross-run contrast."
            if attestation == "manifest_structural" else
            "Every used trial attests the contract hash it executed and those hashes agree "
            "with both manifests."),
    }


def pairs(baseline, candidate, tasks, repeats):
    rows = []
    for task in tasks:
        for repeat in range(repeats):
            left = baseline["lookup"].get((task, repeat))
            right = candidate["lookup"].get((task, repeat))
            if left is not None and right is not None and left.get("seed") != right.get("seed"):
                raise CompareError(
                    f"Task {task!r} repeat {repeat} carries seed {left.get('seed')!r} in the "
                    f"baseline arm and {right.get('seed')!r} in the candidate arm. A matched "
                    f"pair must share its repetition seed; pairing these would contrast two "
                    f"different workloads.")
            row = {"task": task, "repeat": repeat,
                   "baseline_status": left["status"] if left else None,
                   "candidate_status": right["status"] if right else None,
                   "baseline": left, "candidate": right}
            if left is None or right is None:
                row["complete"] = False
                sides = [name for name, value in (("baseline", left), ("candidate", right))
                         if value is None]
                row["exclusion"] = "missing_" + "_and_".join(sides)
            elif left["status"] not in RESOLVED_STATUSES or right["status"] not in RESOLVED_STATUSES:
                row["complete"] = False
                sides = [name for name, value in (("baseline", left), ("candidate", right))
                         if value["status"] not in RESOLVED_STATUSES]
                row["exclusion"] = "unresolved_" + "_and_".join(sides)
            else:
                row["complete"] = True
                row["exclusion"] = None
            rows.append(row)
    return rows


def coverage(rows, baseline, candidate):
    complete = [row for row in rows if row["complete"]]
    exclusions = Counter(row["exclusion"] for row in rows if not row["complete"])
    per_arm = {}
    for name, side in (("baseline", "baseline_status"), ("candidate", "candidate_status")):
        counts = Counter(row[side] or "missing" for row in rows)
        per_arm[name] = {
            "statuses": dict(sorted(counts.items())),
            "categories": dict(sorted(Counter(
                "missing" if row[side] is None else classify(row[side]) for row in rows).items())),
        }
    planned = len(rows)
    # Loss is compared by pair identity, not by count. Two arms can lose the same
    # NUMBER of pairs on entirely different task/repeat cells, which is differential
    # loss that an aggregate count reports as balanced.
    lost_baseline = {(row["task"], row["repeat"]) for row in rows
                     if row["baseline"] is None
                     or (row["baseline"]["status"] not in RESOLVED_STATUSES)}
    lost_candidate = {(row["task"], row["repeat"]) for row in rows
                      if row["candidate"] is None
                      or (row["candidate"]["status"] not in RESOLVED_STATUSES)}
    only_baseline = sorted(lost_baseline - lost_candidate)
    only_candidate = sorted(lost_candidate - lost_baseline)
    identity = lambda pairs: [{"task": task, "repeat": repeat} for task, repeat in pairs]
    return {
        "planned_pairs": planned,
        "complete_pairs": len(complete),
        "complete_pair_fraction": len(complete) / planned if planned else None,
        "excluded_pairs": planned - len(complete),
        "exclusions": dict(sorted(exclusions.items())),
        "per_arm": per_arm,
        "baseline_only_records": len([row for row in rows
                                      if row["baseline"] is not None and row["candidate"] is None]),
        "candidate_only_records": len([row for row in rows
                                       if row["candidate"] is not None and row["baseline"] is None]),
        "pairs_lost_in_baseline_only": identity(only_baseline),
        "pairs_lost_in_candidate_only": identity(only_candidate),
        "pairs_lost_both_arms": len(lost_baseline & lost_candidate),
        "loss_counts_balanced": len(lost_baseline) == len(lost_candidate),
        "differential_missingness": bool(only_baseline or only_candidate),
        "baseline_planned_trials": baseline["planned"],
        "candidate_planned_trials": candidate["planned"],
        "baseline_recorded_trials": len(baseline["trials"]),
        "candidate_recorded_trials": len(candidate["trials"]),
        "unresolved_reasons": {status: reason for status, reason in UNRESOLVED_STATUSES.items()
                               if any(row["baseline_status"] == status or row["candidate_status"] == status
                                      for row in rows)},
    }


def descriptives(rows):
    complete = [row for row in rows if row["complete"]]
    baseline_passes = sum(score_success(row["baseline"]) for row in complete)
    candidate_passes = sum(score_success(row["candidate"]) for row in complete)
    return {
        "complete_pairs": len(complete),
        "baseline_passes": int(baseline_passes),
        "candidate_passes": int(candidate_passes),
        "baseline_pass_rate_complete_pairs": baseline_passes / len(complete) if complete else None,
        "candidate_pass_rate_complete_pairs": candidate_passes / len(complete) if complete else None,
        "fail_to_pass": sum(1 for row in complete
                            if score_success(row["baseline"]) == 0 and score_success(row["candidate"]) == 1),
        "pass_to_fail": sum(1 for row in complete
                            if score_success(row["baseline"]) == 1 and score_success(row["candidate"]) == 0),
        "both_passed": sum(1 for row in complete
                           if score_success(row["baseline"]) == 1 and score_success(row["candidate"]) == 1),
        "neither_passed": sum(1 for row in complete
                              if score_success(row["baseline"]) == 0 and score_success(row["candidate"]) == 0),
        "baseline_infrastructure_errors": int(sum(score_infrastructure(row["baseline"]) for row in complete)),
        "candidate_infrastructure_errors": int(sum(score_infrastructure(row["candidate"]) for row in complete)),
        "scope": ("Counted over complete pairs only, so both arms share exactly the same "
                  "denominator. These are the observed numbers for this fixed suite; no "
                  "population or causal claim is attached to them."),
    }


def clusters_for(rows, score, tasks, repeats):
    values, detail, included = [], [], 0
    for task in tasks:
        eligible = [row for row in rows if row["task"] == task and row["complete"]]
        differences = [score(row["candidate"]) - score(row["baseline"]) for row in eligible]
        included += len(differences)
        detail.append({"task": task, "planned_pairs": repeats,
                       "included_pairs": len(eligible),
                       "value": statistics.fmean(differences) if differences else None})
        if differences:
            values.append(detail[-1]["value"])
    return values, detail, included


def duration_clusters(rows, tasks, repeats):
    values, detail, included, skipped = [], [], 0, Counter()
    for task in tasks:
        differences = []
        for row in rows:
            if row["task"] != task:
                continue
            if not row["complete"]:
                skipped["pair_not_jointly_resolved"] += 1
                continue
            left, right = row["baseline"], row["candidate"]
            if left["status"] != PASSED or right["status"] != PASSED:
                skipped["not_both_passed"] += 1
                continue
            first = check_finite(left.get("container_duration_s"), "container_duration_s", left["id"])
            second = check_finite(right.get("container_duration_s"), "container_duration_s", right["id"])
            if first is None or second is None:
                skipped["no_container_duration"] += 1
                continue
            differences.append(second - first)
        included += len(differences)
        detail.append({"task": task, "planned_pairs": repeats,
                       "included_pairs": len(differences),
                       "value": statistics.fmean(differences) if differences else None})
        if differences:
            values.append(detail[-1]["value"])
    return values, detail, included, dict(sorted(skipped.items()))


def estimate(name, values, detail, *, planned_pairs, included_pairs, case_basis, extra=None):
    """Descriptive task-weighted summary. Never an interval: see WITHHELD_DETAIL."""
    title, definition = ESTIMANDS[name]
    record = {
        "estimand": name, "title": title, "definition": definition,
        "tasks_contributing": len(values), "tasks_total": len(detail),
        "cluster_detail": detail,
        "planned_pairs": planned_pairs, "included_pairs": included_pairs,
        "case_basis": case_basis,
        "point": statistics.fmean(values) if values else None,
        "bootstrap": None,
        "evidence": WITHHELD,
        "evidence_detail": WITHHELD_DETAIL,
    }
    if extra:
        record.update(extra)
    if not values:
        record["point"] = None
        record["evidence"] = (
            "no_jointly_passing_duration_pairs"
            if name == "jointly_passing_duration_task_weighted" else "no_jointly_resolved_pairs")
        eligibility = ("in which both arms passed and both recorded a container duration"
                       if name == "jointly_passing_duration_task_weighted"
                       else "in which both arms recorded a usable outcome")
        record["evidence_detail"] = (
            f"No task contributed a pair {eligibility}, so this quantity is undefined. It is "
            f"reported as null rather than as zero, because zero would assert that a "
            f"difference was measured and found absent.")
    return record


def compare(baseline_run, baseline_profile, candidate_run, candidate_profile, *, treatment=()):
    baseline_directory, candidate_directory = Path(baseline_run), Path(candidate_run)
    same_run = baseline_directory.resolve() == candidate_directory.resolve()
    left_run = load_run(baseline_directory)
    right_run = left_run if same_run else load_run(candidate_directory)
    if same_run and baseline_profile == candidate_profile:
        raise CompareError(
            "The baseline and candidate are the same profile in the same run. A profile "
            "compared with itself is an identity, not a contrast.")
    baseline = arm(left_run, baseline_profile)
    candidate = arm(right_run, candidate_profile)
    check_statuses(baseline["trials"], left_run["manifest"].get("run_id"))
    check_statuses(candidate["trials"], right_run["manifest"].get("run_id"))
    check_seeds(baseline)
    check_seeds(candidate)
    stability = {}
    for side, run in (("baseline", left_run), ("candidate", right_run)):
        stability[side] = check_engine_stability(
            run["manifest"], run["manifest"].get("run_id"), required=not same_run)
    checks = compatibility(baseline, candidate, treatment, same_run)
    tasks = [task["id"] for task in left_run["manifest"]["config"]["tasks"]]
    left_repeats = left_run["manifest"]["config"]["repeats"]
    right_repeats = right_run["manifest"]["config"]["repeats"]
    repeats = min(left_repeats, right_repeats)
    rows = pairs(baseline, candidate, tasks, repeats)
    reach = coverage(rows, baseline, candidate)
    basis = (CASE_BASIS_COMPLETE if reach["complete_pairs"] == reach["planned_pairs"]
             else CASE_BASIS_SELECTED)
    planned_pairs = reach["planned_pairs"]
    success_values, success_detail, success_n = clusters_for(rows, score_success, tasks, repeats)
    infra_values, infra_detail, infra_n = clusters_for(rows, score_infrastructure, tasks, repeats)
    duration_values, duration_detail, duration_n, duration_skipped = duration_clusters(
        rows, tasks, repeats)
    estimates = [
        estimate("jointly_resolved_success_task_weighted", success_values, success_detail,
                 planned_pairs=planned_pairs, included_pairs=success_n, case_basis=basis),
        estimate("jointly_resolved_infrastructure_error_task_weighted", infra_values, infra_detail,
                 planned_pairs=planned_pairs, included_pairs=infra_n, case_basis=basis),
        estimate("jointly_passing_duration_task_weighted", duration_values, duration_detail,
                 planned_pairs=planned_pairs, included_pairs=duration_n,
                 case_basis=(CASE_BASIS_COMPLETE if duration_n == planned_pairs
                             else CASE_BASIS_SELECTED),
                 extra={"excluded_from_duration": duration_skipped, "unit": "seconds"}),
    ]
    engine_varied = sorted(set(checks["differing_keys"]) & set(ENGINE_TREATMENT_KEYS))
    if engine_varied:
        host_scope = (f"two different engines: {', '.join(engine_varied)} was declared as part "
                      f"of the treatment, so these arms did NOT run on one host and every "
                      f"difference between them is confounded with that change")
    elif same_run:
        host_scope = "one run on one host"
    else:
        host_scope = ("two separate runs against the same recorded engine identity, executed "
                      "at different times")
    warnings = []
    if not same_run:
        warnings.append(
            "The arms are separate runs, so they share no schedule block, no ordering, and no "
            "contemporaneous host state. Time-separated execution is itself uncontrolled here.")
    for side in ("baseline", "candidate"):
        if stability[side] == "unknown":
            warnings.append(
                f"The {side} run records no engine_identity_final, so whether its daemon stayed "
                f"the same for the whole run is unknown rather than confirmed.")
    if engine_varied:
        warnings.append(
            f"Engine fields {engine_varied} were declared as treatment. Comparing across "
            f"different engines confounds the resource setting with the engine change, and "
            f"nothing here separates them.")
    if left_repeats != right_repeats:
        warnings.append(
            f"The arms declare different repetition counts ({left_repeats} and {right_repeats}). "
            f"Pairing uses the first {repeats} repetitions of each, so "
            f"{abs(left_repeats - right_repeats)} repetition(s) of the longer arm are outside "
            f"the paired denominator entirely and are not counted as missing pairs.")
    if reach["complete_pair_fraction"] is not None and reach["complete_pair_fraction"] < 1.0:
        warnings.append(
            f"{reach['excluded_pairs']} of {reach['planned_pairs']} planned pairs are not "
            f"complete and are excluded from every paired quantity. Excluded pairs are listed "
            f"by reason and are not treated as failures or as successes.")
    if reach["differential_missingness"]:
        detail = ""
        if reach["loss_counts_balanced"]:
            detail = (" The two arms lost the SAME NUMBER of pairs, so a count-only check would "
                      "call this balanced, but they lost different task/repeat cells.")
        warnings.append(
            f"Loss is differential by pair identity: "
            f"{len(reach['pairs_lost_in_baseline_only'])} pair(s) lost in the baseline arm only "
            f"and {len(reach['pairs_lost_in_candidate_only'])} lost in the candidate arm only."
            f"{detail} Asymmetric loss can move a paired summary on its own; the affected "
            f"task/repeat identities are listed under coverage.")
    for status, reason in reach["unresolved_reasons"].items():
        warnings.append(f"Status {status!r} was treated as unresolved and excluded: {reason}.")
    for side, run in (("baseline", left_run), ("candidate", right_run)):
        if run["manifest"].get("status") != "completed":
            warnings.append(
                f"The {side} run status is {run['manifest'].get('status')!r}, not 'completed', "
                f"so its missingness is likely systematic rather than incidental.")
    if checks["declared_but_identical"]:
        warnings.append(
            f"Declared treatment key(s) {checks['declared_but_identical']} are identical in "
            f"both arms, so this contrast does not vary them.")
    if len(checks["differing_keys"]) > 1:
        warnings.append(
            f"{len(checks['differing_keys'])} fields differ at once ({checks['differing_keys']}). "
            f"Their effects are not separable by this comparison and must be read jointly.")
    if not checks["differing_keys"]:
        warnings.append(
            "No declared field differs between the arms. This is an A/A contrast: any nonzero "
            "estimate here is harness and host variation, not a treatment effect.")
    return {
        "artifact_type": "paired_comparison",
        "schema_version": 1,
        "evalnoise_version": __version__,
        "contrast": {
            "baseline": {"run_id": left_run["manifest"].get("run_id"),
                         "run_directory": str(baseline_directory),
                         "profile": baseline_profile, "resources": baseline["profile"]},
            "candidate": {"run_id": right_run["manifest"].get("run_id"),
                          "run_directory": str(candidate_directory),
                          "profile": candidate_profile, "resources": candidate["profile"]},
            "scope": "within_run" if same_run else "cross_run",
            "selection": ("user_selected: the baseline and candidate were chosen as command "
                          "arguments by whoever ran this command. This is NOT a preregistration "
                          "and carries none of its guarantees. The selection happened after the "
                          "underlying runs existed and may have been informed by their results. "
                          "The tool has no mode that scans profiles for the largest difference, "
                          "but nothing here prevents a person from doing that by hand and "
                          "reporting only the contrast they liked."),
        },
        "compatibility": checks,
        "design": {
            "aggregation_unit": "task",
            "tasks": tasks,
            "repetitions_per_task": repeats,
            "case_basis": basis,
            "held_fixed": ["task set", "task and verifier contract", "workload and verifier image",
                           "provider, model, and cassette identity", "outcome contract",
                           "measurement kind"] + ([] if same_run else ["engine identity unless declared"]),
            "varied": checks["differing_keys"] or None,
            "dependence_note": (
                ("Repetitions of a task within one arm share the task, the run, the host, the "
                 "schedule block, and the repetition seed. "
                 if same_run else
                 "Repetitions of a task within one arm share the task, the run, and the "
                 "repetition seed. The two arms are separate runs, so they do NOT share a "
                 "schedule block, an ordering, or a contemporaneous host state; a paired "
                 "difference here spans two executions separated in time. ")
                + "Repetitions are averaged within the task first, so the reported figure "
                "weights tasks equally rather than weighting tasks that happened to retain "
                "more repetitions more heavily. They are never counted as independent "
                "observations."),
            "seed_note": (
                "Both arms share the configured seed, and every retained trial was checked "
                "against the seed its plan assigned it and against its counterpart's seed. A "
                "repetition index is only a label; a pair is matched on its seed."),
            "host_scope": host_scope,
            "population_note": (
                "This suite is a fixed configured set of scripted tasks, not a random sample "
                "from a population of tasks. These figures describe the recorded pairs and "
                "nothing else. They do not license a statement about tasks outside this suite, "
                "and no uncertainty is attached to them."),
            "case_basis_note": (
                "complete_pair means every planned task/repetition pair contributed. "
                "selected_case means some pairs were lost to missing or unresolved records, so "
                "the figures describe the surviving subset, whose selection may depend on the "
                "treatment itself. The planned denominator is shown beside every included "
                "count so the loss is never hidden."),
            "causal_note": (
                "Nothing here identifies a causal effect. Profiles are not randomly assigned "
                "to independent units; ordering, host state, thermal state, and cache state "
                "are shared and only partly controlled."),
        },
        "coverage": reach,
        "descriptives": descriptives(rows),
        "estimates": estimates,
        "pairs": [{key: row[key] for key in
                   ("task", "repeat", "baseline_status", "candidate_status", "complete", "exclusion")}
                  for row in rows],
        "warnings": warnings,
        "multiplicity": (
            "All three summaries are always reported together, so none is chosen after the fact "
            "by this tool. Because no interval or test is published, there is no multiplicity "
            "correction to apply and none is implied. A person running this command over many "
            "profile pairs and quoting only the widest gap would still be selecting on the "
            "outcome, and nothing here prevents or detects that."),
        "uncertainty": {
            "published": False,
            "status": WITHHELD,
            "detail": WITHHELD_DETAIL,
        },
        "interpretation": (
            f"Descriptive paired summaries for one fixed suite across {host_scope}. No "
            f"uncertainty interval, no standard error, no p-value, no significance test, no "
            f"power claim, and no causal attribution is produced."),
    }


def rows_for_csv(result):
    return [{
        "estimand": record["estimand"],
        "evidence": record["evidence"],
        "point": record["point"],
        "case_basis": record["case_basis"],
        "tasks_contributing": record["tasks_contributing"],
        "tasks_total": record["tasks_total"],
        "included_pairs": record["included_pairs"],
        "planned_pairs": record["planned_pairs"],
        "uncertainty_published": False,
    } for record in result["estimates"]]


def write(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "comparison.json", result)
    fields = ["estimand", "evidence", "point", "case_basis", "tasks_contributing", "tasks_total",
              "included_pairs", "planned_pairs", "uncertainty_published"]
    with (directory / "comparison.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows_for_csv(result))
    (directory / "comparison.html").write_text(render(result))
    return {"json": str((directory / "comparison.json").resolve()),
            "csv": str((directory / "comparison.csv").resolve()),
            "html": str((directory / "comparison.html").resolve())}


def render(result):
    esc = lambda value: html.escape(str(value), quote=True)
    # Formatting stays outside the f-strings below. A nested same-quote f-string and a
    # backslash inside a replacement field are both PEP 701 syntax and are SyntaxErrors
    # on Python 3.11, which this project supports and tests.
    cell = lambda value: "Not measured" if value is None else esc(value)
    points = lambda value: "Undefined" if value is None else format(value * 100, "+.2f") + " pp"
    seconds = lambda value: "Undefined" if value is None else format(value, "+.4f") + " s"
    contrast = result["contrast"]
    checks = result["compatibility"]
    reach = result["coverage"]
    described = result["descriptives"]
    design = result["design"]
    unit_format = {"jointly_resolved_success_task_weighted": points,
                   "jointly_resolved_infrastructure_error_task_weighted": points,
                   "jointly_passing_duration_task_weighted": seconds}
    empty_row = '<tr><td colspan="4">No task contributed a jointly resolved pair.</td></tr>'
    identity_rows = "".join(
        f"<tr><th>{esc(entry['key'])}</th><td>{cell(entry['baseline'])}</td>"
        f"<td>{cell(entry['candidate'])}</td>"
        f"<td>{'same' if entry['equal'] else 'DECLARED TREATMENT'}</td></tr>"
        for entry in checks["observed_differences"])
    exclusion_rows = "".join(
        f"<tr><th>{esc(name)}</th><td>{count}</td></tr>"
        for name, count in reach["exclusions"].items()) or '<tr><td colspan="2">None</td></tr>'
    arm_rows = "".join(
        f"<tr><th>{esc(name)}</th><td>{esc(json.dumps(body['categories']))}</td>"
        f"<td>{esc(json.dumps(body['statuses']))}</td></tr>"
        for name, body in reach["per_arm"].items())
    cards = []
    for record in result["estimates"]:
        show = unit_format[record["estimand"]]
        headline = (f"{show(record['point'])}<br>"
                    f"<small>No uncertainty published</small>")
        rows = "".join(
            f"<tr><th>{esc(entry['task'])}</th>"
            f"<td>{entry['included_pairs']} / {entry['planned_pairs']}</td>"
            f"<td>{show(entry['value'])}</td>"
            f"<td>{'contributes' if entry['value'] is not None else 'dropped: undefined, not zero'}</td></tr>"
            for entry in record["cluster_detail"])
        extra = ""
        if record["estimand"] == "jointly_passing_duration_task_weighted":
            extra = (f"<p><small>Excluded from the duration summary: "
                     f"{esc(json.dumps(record['excluded_from_duration']))}.</small></p>")
        cards.append(
            f"<article><h3>{esc(record['title'])}</h3><strong>{headline}</strong>"
            f"<p>{esc(record['definition'])}</p>{extra}"
            f"<p class=\"notice\"><strong>{esc(record['evidence'])}.</strong> "
            f"{esc(record['evidence_detail'])}</p>"
            f"<p><small>Case basis: {esc(record['case_basis'])}. Tasks contributing: "
            f"{record['tasks_contributing']} of {record['tasks_total']}. Pairs included: "
            f"{record['included_pairs']} of {record['planned_pairs']} planned.</small></p>"
            f"<div class=\"scroll\"><table><thead><tr><th>Task</th>"
            f"<th>Included / planned pairs</th>"
            f"<th>Within-task mean difference</th><th>Status</th></tr></thead><tbody>"
            f"{rows or empty_row}</tbody></table></div></article>")
    warning_items = "".join(f"<li>{esc(text)}</li>" for text in result["warnings"])
    warning_block = (f"<h2>Warnings</h2><ul>{warning_items}</ul>" if warning_items else "")
    pair_rows = "".join(
        f"<tr><th>{esc(row['task'])}</th><td>{row['repeat']}</td>"
        f"<td>{cell(row['baseline_status'])}</td><td>{cell(row['candidate_status'])}</td>"
        f"<td>{'complete' if row['complete'] else esc(row['exclusion'])}</td></tr>"
        for row in result["pairs"])
    title = f"{contrast['candidate']['profile']} vs {contrast['baseline']['profile']}"
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>EvalNoise / comparison / {esc(title)}</title><style>
{STYLE}
{COMPARISON_STYLE}
</style><main><header><span class="label">EVALNOISE / PAIRED COMPARISON (DESCRIPTIVE)</span><h1>{esc(title)}</h1>
<p>A user-selected paired contrast summarised task by task, across {esc(design['host_scope'])}. These are descriptive counts and means over the recorded pairs. No uncertainty estimate is published.</p>
<small>{esc(contrast['scope'])} / baseline run {esc(contrast['baseline']['run_id'])} / candidate run {esc(contrast['candidate']['run_id'])} / EvalNoise {esc(result['evalnoise_version'])}</small></header>
<p class="notice"><strong>{esc(result['interpretation'])}</strong></p>
<p class="notice"><strong>Uncertainty is withheld: {esc(result['uncertainty']['status'])}.</strong> {esc(result['uncertainty']['detail'])}</p>
<p class="notice"><strong>Contrast selection.</strong> {esc(contrast['selection'])}</p>
<h2>What varied and what was held fixed</h2>
<p>{esc(checks['attestation_note'])} Identity fields checked: {esc(', '.join(checks['checked_identity']))}. The whole configuration hash is deliberately not compared, because the treatment is inside it.</p>
<p>Held fixed: {esc('; '.join(design['held_fixed']))}. Varied: {esc(', '.join(design['varied'] or ['nothing (A/A contrast)']))}.</p>
<div class="scroll"><table><thead><tr><th>Field</th><th>Baseline</th><th>Candidate</th><th>Role</th></tr></thead><tbody>{identity_rows}</tbody></table></div>
<h2>Coverage and denominators</h2>
<p>{reach['complete_pairs']} of {reach['planned_pairs']} planned task/repetition pairs are jointly resolved. A pair counts only when both arms recorded a resolved outcome. Excluded pairs are shown by reason and are never recoded as a success or a failure. Case basis: <strong>{esc(design['case_basis'])}</strong>. {esc(design['case_basis_note'])}</p>
<p>Loss by pair identity: {len(reach['pairs_lost_in_baseline_only'])} lost in the baseline arm only, {len(reach['pairs_lost_in_candidate_only'])} lost in the candidate arm only, {reach['pairs_lost_both_arms']} lost in both. Loss counts balanced: {reach['loss_counts_balanced']}; differential by identity: {reach['differential_missingness']}.</p>
<div class="scroll"><table><thead><tr><th>Exclusion reason</th><th>Pairs</th></tr></thead><tbody>{exclusion_rows}</tbody></table></div>
<div class="scroll"><table><thead><tr><th>Arm</th><th>Outcome categories over planned pairs</th><th>Raw statuses</th></tr></thead><tbody>{arm_rows}</tbody></table></div>
<h2>Jointly resolved descriptive results</h2>
<p>{esc(described['scope'])}</p>
<div class="scroll"><table><thead><tr><th>Quantity</th><th>Baseline</th><th>Candidate</th></tr></thead><tbody>
<tr><th>Passes among jointly resolved pairs</th><td>{described['baseline_passes']} / {described['complete_pairs']} (of {reach['planned_pairs']} planned)</td><td>{described['candidate_passes']} / {described['complete_pairs']} (of {reach['planned_pairs']} planned)</td></tr>
<tr><th>Recorded infrastructure errors</th><td>{described['baseline_infrastructure_errors']}</td><td>{described['candidate_infrastructure_errors']}</td></tr>
<tr><th>Transitions</th><td colspan="2">{described['fail_to_pass']} non-pass to pass; {described['pass_to_fail']} pass to non-pass; {described['both_passed']} both passed; {described['neither_passed']} neither passed</td></tr>
</tbody></table></div>
<h2>Descriptive summaries</h2><p>{esc(design['dependence_note'])}</p><p>{esc(design['seed_note'])}</p><p>{esc(design['population_note'])}</p><p>{esc(design['causal_note'])}</p>
<div class="comparisons">{''.join(cards)}</div>
<p><small>{esc(result['multiplicity'])}</small></p>
{warning_block}
<h2>Every planned pair</h2><div class="scroll"><table><thead><tr><th>Task</th><th>Repeat</th><th>Baseline</th><th>Candidate</th><th>Pairing</th></tr></thead><tbody>{pair_rows}</tbody></table></div>
<h2>Full comparison record</h2><details><summary>Inspect the complete JSON artifact</summary><pre>{esc(json.dumps(result, indent=2))}</pre></details>
<footer>Generated locally by EvalNoise. No scripts, remote fonts, trackers, or network requests. This artifact is a comparison of recorded evidence; it is not a model evaluation and not a significance test.</footer></main></html>"""
