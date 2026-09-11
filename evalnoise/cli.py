"""Small CLI; running arbitrary configured workloads requires acknowledgement."""

import argparse
import json
from pathlib import Path
import sys

from .config import ConfigError, load, plan
from .docker import Docker, DockerError
from .report import generate
from .runner import execute


def main(argv=None):
    parser = argparse.ArgumentParser(prog="evalnoise", description="Measure infrastructure sensitivity, preserve evidence.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check the Linux Docker engine and record relevant platform details")
    for name in ("validate", "plan", "run"):
        command = commands.add_parser(name)
        command.add_argument("config", type=Path)
        if name == "run":
            command.add_argument("--output", type=Path, default=Path("runs"))
            command.add_argument("--trust-config", action="store_true", help="Acknowledge that configured images and commands are trusted code")
    report = commands.add_parser("report", help="Regenerate reports from persisted evidence without Docker")
    report.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            print(json.dumps(Docker().doctor(), indent=2))
        elif args.command == "report":
            print(json.dumps(generate(args.directory), indent=2))
        else:
            experiment = load(args.config)
            if args.command == "validate":
                print(json.dumps({"valid": True, "config_sha256": experiment.digest(),
                                  "trials": experiment.repeats * len(experiment.tasks) * len(experiment.profiles)}, indent=2))
            elif args.command == "plan":
                print(json.dumps({"config_sha256": experiment.digest(), "batches": plan(experiment)}, indent=2))
            else:
                if not args.trust_config:
                    raise ConfigError("Review images and commands, then pass --trust-config. Containers are not a hostile-code security boundary.")
                directory = execute(experiment, args.output)
                summary = generate(directory)
                print(json.dumps({"directory": str(directory.resolve()), "status": summary["status"],
                                  "report": str((directory / "report.html").resolve())}, indent=2))
                return 0 if summary["status"] == "completed" else 2
        return 0
    except (ConfigError, DockerError, OSError, ValueError, KeyError) as error:
        print(f"evalnoise: {error}", file=sys.stderr)
        return 2
