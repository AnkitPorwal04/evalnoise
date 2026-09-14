"""Create fresh descriptive comparisons and an allowlisted evidence extract."""

import argparse
from pathlib import Path

from evalnoise.compare import compare, load_run, write
from evalnoise.investigation import digest
from evalnoise.storage import write_json


def evidence(run):
    trials = run["trials"]
    return {"kind": "demonstration_evidence_extract", "schema_version": 1,
            "run_id": run["manifest"]["run_id"],
            "manifest_sha256": digest(run["manifest"]),
            "source_sha256": digest([run["manifest"], trials]),
            "note": "Allowlisted projection, not the complete evidence bundle or an attestation. Raw evidence is retained locally.",
            "trials": [{"id": t["id"], "sha256": digest(t),
                        "task": t["task"], "profile": t["profile"], "repeat": t["repeat"],
                        "status": t["status"], "execution_status": t.get("execution_status"),
                        "exit_code": t.get("inspection", {}).get("state", {}).get("ExitCode"),
                        "oom_observed": t.get("inspection", {}).get("state", {}).get("OOMKilled"),
                        "verifier_passed": t.get("verification", {}).get("verdict", {}).get("passed"),
                        "workload_seconds": t.get("container_duration_s"),
                        "telemetry_error_count": len(t.get("telemetry_errors", [])),
                        "cleanup_error_present": t.get("cleanup_error") is not None}
                       for t in trials]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run = load_run(args.run)
    comparisons = [(candidate, compare(args.run, "baseline", args.run, candidate, treatment=[field]))
                   for candidate, field in (("memory-tight", "memory_mb"),
                                            ("cpu-tight", "cpus"), ("concurrent", "concurrency"))]
    args.output.mkdir(parents=False, exist_ok=False)
    extract = evidence(run)
    write_json(args.output / "evidence.json", extract)
    for candidate, result in comparisons:
        write(result, args.output / candidate)
    print(f"Evidence digest: {extract['source_sha256']}")
    print(f"Comparisons and evidence: {args.output}")


if __name__ == "__main__":
    main()
