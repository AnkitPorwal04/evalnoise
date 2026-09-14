"""Resolve reviewed local images into a fresh, immutable study configuration."""

import argparse
from pathlib import Path

from evalnoise.config import parse
from evalnoise.docker import Docker
from evalnoise.storage import write_json


def configuration(workload, verifier):
    return {"schema_version": 1, "name": "aggregation-demonstration", "seed": 2026,
            "repeats": 3, "sample_interval_s": 2,
            "tasks": [{"id": mode, "image": workload,
                       "command": ["python", "/opt/evalnoise/aggregation.py", mode],
                       "verifier": {"image": verifier,
                                    "command": ["python", "/opt/verifier/aggregation.py"],
                                    "version": "aggregation-v1", "cpus": 1,
                                    "memory_mb": 128, "timeout_s": 10}}
                      for mode in ("batch", "stream", "cpu", "wrong")],
            "profiles": [
                {"id": "baseline", "cpus": 1, "memory_mb": 256, "timeout_s": 20, "concurrency": 1},
                {"id": "memory-tight", "cpus": 1, "memory_mb": 48, "timeout_s": 20, "concurrency": 1},
                {"id": "cpu-tight", "cpus": 0.25, "memory_mb": 256, "timeout_s": 20, "concurrency": 1},
                {"id": "concurrent", "cpus": 1, "memory_mb": 256, "timeout_s": 20, "concurrency": 2}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--verifier", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    backend = Docker()
    backend.doctor()
    config = configuration(backend.image(args.workload)["id"], backend.image(args.verifier)["id"])
    parse(config)
    args.output.mkdir(parents=False, exist_ok=False)
    write_json(args.output / "experiment.json", config)
    print(args.output / "experiment.json")


if __name__ == "__main__":
    main()
