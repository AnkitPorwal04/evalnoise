"""Descriptive comparisons with explicit denominators; no significance claims."""

from collections import Counter
import csv
import html
import json
from pathlib import Path
import statistics

from .storage import write_json


def summarize(manifest, trials):
    planned = {trial["id"]: batch for batch in manifest["plan"] for trial in batch["trials"]}
    ids = [trial["id"] for trial in trials]
    if len(set(ids)) != len(ids) or any(identity not in planned for identity in ids):
        raise ValueError("Duplicate or unexpected trial IDs; refusing an ambiguous report")
    for trial in trials:
        expected = planned[trial["id"]]
        if trial["profile"] != expected["profile"] or trial["repeat"] != expected["repeat"]:
            raise ValueError("Trial identity does not match the persisted plan")
        if not any(t["id"] == trial["id"] and t["task"] == trial["task"] for t in expected["trials"]):
            raise ValueError("Task identity does not match the persisted plan")
    profiles = []
    for profile in manifest["config"]["profiles"]:
        selected = [trial for trial in trials if trial["profile"] == profile["id"]]
        counts = Counter(trial["status"] for trial in selected)
        durations = [trial["container_duration_s"] for trial in selected
                     if trial["status"] == "passed" and trial.get("container_duration_s") is not None]
        planned_count = sum(len(batch["trials"]) for batch in manifest["plan"] if batch["profile"] == profile["id"])
        profiles.append({**profile, "planned": planned_count, "recorded": len(selected),
                         "missing": planned_count - len(selected), "outcomes": dict(counts),
                         "pass_rate_recorded": counts["passed"] / len(selected) if selected else None,
                         "successful_duration_median_s": statistics.median(durations) if durations else None,
                         "successful_duration_n": len(durations)})
    pairs = []
    baseline = profiles[0]["id"]
    lookup = {(trial["task"], trial["repeat"], trial["profile"]): trial for trial in trials}
    for profile in profiles[1:]:
        complete = []
        for task in manifest["config"]["tasks"]:
            for repeat in range(manifest["config"]["repeats"]):
                left, right = lookup.get((task["id"], repeat, baseline)), lookup.get((task["id"], repeat, profile["id"]))
                if left and right and left["status"] != "cancelled" and right["status"] != "cancelled":
                    complete.append((left["status"] == "passed", right["status"] == "passed"))
        pairs.append({"baseline": baseline, "candidate": profile["id"], "complete_pairs": len(complete),
                      "fail_to_pass": sum(not a and b for a, b in complete),
                      "pass_to_fail": sum(a and not b for a, b in complete),
                      "paired_pass_delta": statistics.mean(int(b) - int(a) for a, b in complete) if complete else None})
    return {"run_id": manifest["run_id"], "status": manifest["status"], "profiles": profiles,
            "comparisons": pairs, "recorded_trials": len(trials), "planned_trials": len(planned),
            "interpretation": "Descriptive results for this fixed workload suite, not independent population trials. No causal or statistical significance claim.",
            "latency_scope": "Container start-to-finish duration for successful trials only. Failed and missing durations are not zero. Profiles may have different successful task sets; comparing these medians alone does not establish a speedup."}


def generate(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    trials = [json.loads(path.read_text()) for path in sorted((directory / "trials").glob("*.json"))]
    summary = summarize(manifest, trials)
    write_json(directory / "summary.json", summary)
    with (directory / "trials.csv").open("w", newline="") as stream:
        fields = ["id", "task", "profile", "repeat", "status", "container_duration_s", "lifecycle_s"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trials)
    esc = lambda value: html.escape(str(value), quote=True)
    rows = []
    for profile in summary["profiles"]:
        rate = profile["pass_rate_recorded"]
        median = profile["successful_duration_median_s"]
        rows.append(f"<tr><th>{esc(profile['id'])}</th><td>{esc(profile['cpus'])} / {esc(profile['memory_mb'])} MiB</td>"
                    f"<td>{profile['recorded']} / {profile['planned']}</td><td>{'Not measured' if rate is None else f'{rate:.1%}'}</td>"
                    f"<td>{'Not measured' if median is None else f'{median:.3f} s'} (n={profile['successful_duration_n']})</td>"
                    f"<td>{esc(', '.join(f'{key}: {value}' for key, value in profile['outcomes'].items()))}</td></tr>")
    cards = []
    for comparison in summary["comparisons"]:
        delta = comparison["paired_pass_delta"]
        cards.append(f"<article><h3>{esc(comparison['candidate'])} vs {esc(comparison['baseline'])}</h3>"
                     f"<strong>{'No complete pairs' if delta is None else f'{delta * 100:+.1f} percentage points'}</strong>"
                     f"<p>{comparison['complete_pairs']} paired task/repetition observations. "
                     f"{comparison['fail_to_pass']} fail-to-pass; {comparison['pass_to_fail']} pass-to-fail.</p></article>")
    details = "".join(f"<details><summary>{esc(t['id'])} <b>{esc(t['status'])}</b></summary>"
                      f"<p>Container: {esc(t['container_name'])}</p>"
                      f"<pre>{esc(json.dumps({k: v for k, v in t.items() if k != 'logs'}, indent=2))}</pre>"
                      f"<h3>Bounded workload logs</h3><pre>{esc(t.get('logs', {}).get('text', 'No logs captured'))}</pre></details>" for t in trials)
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>EvalNoise / {esc(manifest['config']['name'])}</title><style>
:root{{color-scheme:light;--ink:#172c35;--paper:#f4f2eb;--muted:#52656b;--line:#ced5cf;--accent:#12684d}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 Georgia,serif}}main{{max-width:1250px;margin:auto;padding:48px 24px}}header{{border-top:6px solid var(--ink);padding-top:22px}}small,.label,th,summary{{font-family:system-ui,sans-serif}}.label{{letter-spacing:.15em;color:var(--accent);font-size:12px;font-weight:700}}h1{{font-size:clamp(32px,6vw,62px);line-height:1.1;margin:18px 0}}h2{{font-size:27px;margin:38px 0 12px}}p{{max-width:850px}}.notice{{border-left:4px solid var(--accent);padding:12px 22px;background:#e7ede5}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;text-align:left;font-size:14px}}th,td{{padding:15px 12px;border-bottom:1px solid var(--line);vertical-align:top}}th{{font-weight:600}}.comparisons{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:24px}}article{{border-top:2px solid var(--line);padding-top:10px}}article strong{{font:24px system-ui,sans-serif;color:var(--accent)}}details{{border-top:1px solid var(--line);padding:14px 0}}summary{{cursor:pointer;overflow-wrap:anywhere}}summary b{{float:right;margin-left:12px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;max-height:460px;overflow:auto;background:#e9e9e1;padding:16px;font:12px/1.6 monospace}}footer{{margin-top:36px;color:var(--muted);font-size:13px}}@media(max-width:600px){{main{{padding:24px 16px}}summary b{{float:none;display:block}}}}
</style><main><header><span class="label">EVALNOISE / EXPERIMENT NOTEBOOK</span><h1>{esc(manifest['config']['name'])}</h1>
<p>Infrastructure changes the conditions of a test. This report preserves what ran, what finished, and what the evidence supports.</p>
<small>Run {esc(manifest['run_id'])} / {esc(manifest['started_at'])} / Status: {esc(manifest['status'])}</small></header>
<p class="notice">{summary['recorded_trials']} of {summary['planned_trials']} planned trials recorded. {esc(summary['interpretation'])}</p>
<h2>Resource profiles</h2><div class="scroll"><table><thead><tr><th>Profile</th><th>CPU ceiling / RAM ceiling</th><th>Recorded / planned</th><th>Pass / recorded</th><th>Successful median</th><th>Outcomes</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p><small>{esc(summary['latency_scope'])} Sampling interval: {esc(manifest['config']['sample_interval_s'])} s (0 means disabled). CPU limits are not reservations. Memory swap is disabled.</small></p>
<h2>Paired outcome changes</h2><div class="comparisons">{''.join(cards) or '<p>Only one profile: no comparison.</p>'}</div>
<h2>Environment and provenance</h2><details><summary>Inspect manifest, schedule, image identities, and configuration</summary><pre>{esc(json.dumps(manifest, indent=2))}</pre></details>
<h2>Trial evidence</h2>{details or '<p>No trials were recorded. This is not a completed measurement.</p>'}
<footer>Generated locally by EvalNoise. No scripts, remote fonts, trackers, or network requests. Workload logs may contain sensitive data; review before sharing. Evidence is inspectable, not cryptographically attested.</footer></main></html>"""
    (directory / "report.html").write_text(page)
    return summary
