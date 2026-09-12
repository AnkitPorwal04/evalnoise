"""Small CLI; running arbitrary configured workloads requires acknowledgement."""

import argparse
import json
from pathlib import Path
import sys

from .budget import BudgetError
from .config import ConfigError, load, plan
from .coordination import CoordinationError
from .provider import ProviderError
from .docker import Docker, DockerError
from .probe import run as probe_run
from .recovery import RecoveryError, cleanup, diagnose
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
    check = commands.add_parser("diagnose", help="Read-only recovery diagnosis for one run you own")
    check.add_argument("directory", type=Path)
    remove = commands.add_parser("cleanup", help="Remove only this run's own leftover containers")
    remove.add_argument("directory", type=Path)
    remove.add_argument("--confirm", action="store_true", help="Required acknowledgement; without it nothing is removed")
    enforcement = commands.add_parser("probe", help="Preflight enforcement probe using a separate reviewed image")
    enforcement.add_argument("config", type=Path)
    enforcement.add_argument("--image", default="evalnoise-probe:local")
    enforcement.add_argument("--output", type=Path, default=Path("runs"))
    enforcement.add_argument("--trust-config", action="store_true", help="Acknowledge that the probe image is trusted code this command will execute")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            print(json.dumps(Docker().doctor(), indent=2))
        elif args.command == "report":
            print(json.dumps(generate(args.directory), indent=2))
        elif args.command == "diagnose":
            print(json.dumps(diagnose(Docker(), args.directory), indent=2))
        elif args.command == "cleanup":
            print(json.dumps(cleanup(Docker(), args.directory, args.confirm), indent=2))
        elif args.command == "probe":
            if not args.trust_config:
                raise ConfigError("The probe starts a configured image as trusted code. Review the "
                                  "image, then pass --trust-config.")
            experiment = load(args.config)
            result = probe_run(Docker(), args.image, experiment.profiles, args.output)
            print(json.dumps(result, indent=2))
            return 0 if result["conclusive"] and result["enforced_as_requested"] else 2
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
                directory = execute(experiment, args.output, config_dir=args.config.resolve().parent)
                summary = generate(directory)
                print(json.dumps({"directory": str(directory.resolve()), "status": summary["status"],
                                  "report": str((directory / "report.html").resolve())}, indent=2))
                return 0 if summary["status"] == "completed" else 2
        return 0
    except (BudgetError, ConfigError, CoordinationError, DockerError, ProviderError,
            RecoveryError, OSError, ValueError, KeyError) as error:
        print(f"evalnoise: {error}", file=sys.stderr)
        return 2
