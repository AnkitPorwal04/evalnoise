"""Fixed-horizon bounded block contrasts, not task-population bootstrap inference."""

from collections import Counter
import hashlib
import html
import json
import math
from pathlib import Path
import statistics

from .compare import (CompareError, arm, check_seeds, check_statuses,
                      check_engine_stability, compatibility, load_run, pairs)
from .config import parse, plan
from .storage import write_json


def bounded_mean(values, alpha=0.05):
    if (type(alpha) not in (int, float) or not 0 < alpha < 1
            or not math.isfinite(alpha)):
        raise ValueError("alpha must be finite and strictly between zero and one")
    if not values or len(values) > 10000:
        raise ValueError("Require 1..10000 fixed-horizon block values")
    if any(type(x) not in (int, float) or not -1 <= x <= 1 or not math.isfinite(x)
           for x in values):
        raise ValueError("Block contrasts must be finite numbers in [-1, 1]")
    point = statistics.mean(values)
    radius = math.sqrt(2 * (math.log(2) - math.log(alpha)) / len(values))
    return {"point": point, "lower": max(-1, point - radius),
            "upper": min(1, point + radius), "radius": radius,
            "blocks": len(values), "alpha": alpha,
            "method": "conditional_hoeffding_fixed_horizon",
            "target": "mean of history-conditional expected fixed-suite block contrasts",
            "scope": "Not a stationary mean, task-population effect, or causal effect. No optional stopping."}


def analyze(directory, baseline, candidate, treatment=(), alpha=0.05):
    run = load_run(directory)
    manifest = run["manifest"]
    experiment = parse(manifest["config"])
    if manifest.get("status") != "completed":
        raise CompareError("Block analysis requires a completed fixed-horizon run")
    if len(experiment.profiles) != 2 or baseline == candidate:
        raise CompareError("Require two distinct arms and exactly two configured profiles")
    if any(task.agent for task in experiment.tasks):
        raise CompareError("This block protocol currently supports container tasks, not agent runs")
    if manifest["plan"] != plan(experiment):
        raise CompareError("Persisted plan does not match the fixed seeded block schedule")
    check_engine_stability(manifest, manifest.get("run_id"), required=True)
    left, right = arm(run, baseline), arm(run, candidate)
    for side in (left, right):
        check_statuses(side["trials"], manifest.get("run_id"))
        check_seeds(side)
    checked = compatibility(left, right, treatment, True)
    checked["attestation_note"] = "Both arms use the same validated manifest, plan, and image resolution."
    rows = pairs(left, right, [task.id for task in experiment.tasks], experiment.repeats)
    if any(not row["complete"] for row in rows):
        raise CompareError("Missing or unresolved planned pair: no block may be discarded")
    if any(trial.get("cleanup_error") for trial in run["trials"]):
        raise CompareError("Cleanup failure invalidates the completed block protocol")
    blocks = []
    for repeat in range(experiment.repeats):
        selected = [row for row in rows if row["repeat"] == repeat]
        delta = statistics.mean(int(row["candidate_status"] == "passed")
                                - int(row["baseline_status"] == "passed") for row in selected)
        blocks.append({"block": repeat, "seed": experiment.seed + repeat,
                       "order": [batch["profile"] for batch in manifest["plan"]
                                 if batch["repeat"] == repeat],
                       "tasks": len(selected), "delta": delta})
    source = json.dumps({"manifest": manifest, "trials": run["trials"]},
                        sort_keys=True, allow_nan=False).encode()
    return {"schema_version": 1, "kind": "fixed_suite_block_analysis",
            "run_id": manifest["run_id"], "source_sha256": hashlib.sha256(source).hexdigest(),
            "baseline": baseline, "candidate": candidate, "compatibility": checked,
            "planned_pairs": len(rows), "included_pairs": len(rows),
            "statuses": {name: dict(Counter(row[name + "_status"] for row in rows))
                         for name in ("baseline", "candidate")},
            "blocks": blocks, "interval": bounded_mean([b["delta"] for b in blocks], alpha),
            "limitations": ["Outcome is clean pass versus all resolved non-pass outcomes, not reasoning quality.",
                            "The bound targets conditional expectations for this protocol; dependence and carryover may change that target.",
                            "A complete fixed horizon is required. Repeated looks or outcome-selected runs invalidate the stated coverage.",
                            "The analyzer checks artifacts, not honesty of data collection or absence of host interference.",
                            "Timing intervals and generalization to other tasks are not supplied."]}


def write(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "blocks.json", result)
    encoded = html.escape(json.dumps(result, indent=2, allow_nan=False))
    page = ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>EvalNoise fixed-suite block study</title><style>'
            'body{max-width:960px;margin:40px auto;padding:20px;background:#f5f3eb;color:#183c31;font:18px Georgia}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px monospace}'
            '</style><h1>Fixed-suite block study</h1>'
            '<p>Conservative, fixed-horizon bound on a history-conditional protocol contrast. '
            'Not a task-population or causal effect.</p><pre>' + encoded + '</pre></html>')
    (directory / "blocks.html").write_text(page)
    return str(directory / "blocks.html")
