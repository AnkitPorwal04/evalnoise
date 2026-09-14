"""Produce an offline demonstration from a strict, fixed-study projection only."""

import argparse
from collections import Counter
from html import escape
import json
import math
from pathlib import Path

from evalnoise.compare import load_run
from evalnoise.investigation import digest
from evalnoise.storage import write_json

TASKS = ("batch", "stream", "cpu", "wrong")
PROFILES = ("baseline", "memory-tight", "cpu-tight", "concurrent")
STATUSES = ("passed", "verification_failed", "oom_killed")


def project(run):
    """Do not carry through arbitrary labels, strings, logs, or metadata."""
    rows = []
    seen = set()
    for trial in run["trials"]:
        task, profile, repeat = trial.get("task"), trial.get("profile"), trial.get("repeat")
        status = trial.get("status")
        if task not in TASKS or profile not in PROFILES or type(repeat) is not int or repeat not in range(3):
            raise ValueError("Not the reviewed four-task, four-profile demonstration")
        cell = (task, profile, repeat)
        if cell in seen or status not in STATUSES:
            raise ValueError("Duplicate cell or unreviewed outcome; review before export")
        seen.add(cell)
        seconds = trial.get("container_duration_s")
        if seconds is not None and (type(seconds) not in (float, int) or
                                   not 0 <= seconds <= 3600 or not math.isfinite(seconds)):
            raise ValueError("Invalid duration")
        state = trial.get("inspection", {}).get("state", {})
        verdict = trial.get("verification", {}).get("verdict", {}).get("passed")
        code, oom = state.get("ExitCode"), state.get("OOMKilled")
        if type(code) is not int or not 0 <= code <= 255 or type(oom) is not bool:
            raise ValueError("Missing typed execution evidence")
        if verdict is not None and type(verdict) is not bool:
            raise ValueError("Invalid verifier evidence")
        rows.append(dict(task=task, profile=profile, repeat=repeat, status=status,
                         seconds=seconds, exit_code=code, oom_observed=oom, verifier_passed=verdict))
    if len(rows) != 48:
        raise ValueError("All 48 reviewed study cells are required")
    rows.sort(key=lambda row: (row["profile"], row["task"], row["repeat"]))
    return {"kind": "reviewed_static_demonstration", "schema_version": 1,
            "source_sha256": digest([run["manifest"], run["trials"]]), "trials": rows}


def render(data):
    counts = Counter((row["profile"], row["status"]) for row in data["trials"])
    summary = "".join("<tr><th>" + profile + "</th>" + "".join(
        f"<td>{counts[profile, status]}</td>" for status in STATUSES) + "</tr>" for profile in PROFILES)
    rows = "".join("<tr>" + "".join(f"<td>{escape(str(row[key]))}</td>" for key in
        ("task", "profile", "repeat", "status", "exit_code", "oom_observed", "verifier_passed")) + "</tr>"
        for row in data["trials"])
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>EvalNoise | Evidence, not just an exit code</title>
<style>body{margin:0;background:#f4f1e9;color:#183a31;font:18px/1.6 Georgia,serif}main{max-width:1080px;margin:auto;padding:48px 24px}h1{font-size:clamp(32px,6vw,64px);line-height:1.05;max-width:800px}h2{margin-top:48px}small{font:13px monospace;letter-spacing:1px}.intro{max-width:750px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font:14px/1.5 monospace}th,td{text-align:left;padding:12px;border-bottom:1px solid #b9c7bd}th{white-space:nowrap}details{margin:24px 0}a{color:inherit}code{overflow-wrap:anywhere}footer{border-top:1px solid #b9c7bd;margin-top:48px;padding-top:24px;font-size:14px}</style>
<main><small>EVALNOISE / LOCAL RELEASE PREVIEW</small><h1>Evidence, not just an exit code.</h1>
<p class="intro">48 synthetic aggregation trials. Four resource profiles. An independent answer checker.
This static walkthrough shows what was recorded, without a worker, Docker endpoint, login, or executable dashboard.</p>
<h2>Read the outcomes separately</h2><p>Memory-limited execution failures are not verifier-rejected answers.
The deliberately wrong implementation can exit zero and still fail verification.</p>
<div class="scroll"><table><thead><tr><th>Profile</th><th>Verified passes</th><th>Wrong answers</th><th>Observed OOM kills</th></tr></thead><tbody>''' + summary + '''</tbody></table></div>
<h2>Inspect all 48 observations</h2><p>No failed observation is hidden. Repetition numbers are zero-based;
<code>None</code> means the verifier did not supply a verdict, not a passing answer.</p>
<details><summary>Open the complete selected-evidence table</summary><div class="scroll"><table><thead><tr>
<th>Task</th><th>Profile</th><th>Repeat</th><th>Outcome</th><th>Exit</th><th>OOM flag</th><th>Verifier</th>
</tr></thead><tbody>''' + rows + '''</tbody></table></div></details>
<p><a href="evidence.json" download>Download the exact displayed data (JSON)</a></p>
<h2>What this does not establish</h2><p>These are reviewed known-answer fixtures on one Docker Desktop host,
not AI capability scores, a public benchmark, or a general performance recommendation. No confidence intervals
or causal claims are presented. Short sampled telemetry is not a memory peak. The projection omits raw logs,
credentials, host identifiers and paths; it is not a complete evidence bundle or a signed attestation.</p>
<footer>Canonical source digest: <code>''' + data["source_sha256"] + '''</code><p>This preview was generated locally.
It has not been publicly deployed. Publication requires a separate review and approval.</p></footer></main></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = project(load_run(args.run))
    page = render(data)
    args.output.mkdir(exist_ok=False)
    write_json(args.output / "evidence.json", data)
    (args.output / "index.html").write_text(page, encoding="utf-8")
    print(json.dumps({"output": str(args.output), "trials": 48, "publicly_deployed": False}))


if __name__ == "__main__":
    main()
