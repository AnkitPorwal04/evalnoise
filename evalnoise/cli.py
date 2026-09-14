"""Small CLI; running arbitrary configured workloads requires acknowledgement."""

import argparse
import json
from pathlib import Path
import sys

from .budget import BudgetError
from .blocks import analyze as analyze_blocks, write as write_blocks
from .compare import CompareError, compare, write as write_comparison
from .config import ConfigError, load, plan
from .coordination import CoordinationError
from .provider import ProviderError
from .docker import Docker, DockerError
from .probe import run as probe_run
from .recovery import RecoveryError, cleanup, diagnose
from .report import generate
from .resample import ResampleError
from .runner import execute
from .subscription import SubscriptionCheckError, check as subscription_check


def main(argv=None):
    parser = argparse.ArgumentParser(prog="evalnoise", description="Measure infrastructure sensitivity, preserve evidence.")
    commands = parser.add_subparsers(dest="command", required=True)
    workbench = commands.add_parser("workbench", help="Read-only local artifact browser; no Docker controls")
    workbench.add_argument("--root", type=Path, default=Path("runs"))
    workbench.add_argument("--port", type=int, default=4178)
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
    contrast = commands.add_parser(
        "compare",
        help="Offline paired comparison of two recorded profiles; no Docker and no model calls")
    contrast.add_argument("baseline_run", type=Path, help="Run directory holding the baseline arm")
    contrast.add_argument("candidate_run", type=Path, nargs="?", default=None,
                          help="Run directory holding the candidate arm; defaults to the baseline run")
    contrast.add_argument("--baseline", required=True, help="Baseline profile ID, named before the numbers exist")
    contrast.add_argument("--candidate", required=True, help="Candidate profile ID, named before the numbers exist")
    contrast.add_argument("--treatment", action="append", default=[], metavar="FIELD",
                          help="Declare a field that is allowed to differ. Undeclared differences are refused.")
    contrast.add_argument("--output", type=Path, default=None,
                          help="Directory for comparison.json/.csv/.html; omit to print JSON only")
    block = commands.add_parser("block-analyze", help="Fixed-horizon conditional block bound for a completed two-arm study")
    block.add_argument("directory", type=Path)
    block.add_argument("--baseline", required=True)
    block.add_argument("--candidate", required=True)
    block.add_argument("--treatment", action="append", default=[])
    block.add_argument("--alpha", type=float, default=0.05)
    block.add_argument("--output", type=Path)
    subscription = commands.add_parser(
        "codex-check",
        help="One known-answer Codex subscription smoke check; not a provider benchmark")
    subscription.add_argument("--confirm-subscription-use", action="store_true",
                              help="Acknowledge that this may spend your ChatGPT subscription quota on one real model call, if every preflight check passes first")
    subscription.add_argument("--output", type=Path, default=Path("runs/codex-check"))
    subscription.add_argument("--timeout-s", type=float, default=60.0)
    subscription.add_argument("--codex-binary", default="codex")
    args = parser.parse_args(argv)
    try:
        if args.command == "workbench":
            from .workbench import serve
            serve(args.root, args.port)
            return 0
        if args.command == "block-analyze":
            result = analyze_blocks(args.directory, args.baseline, args.candidate,
                                    args.treatment, args.alpha)
            if args.output is not None:
                result["report"] = write_blocks(result, args.output)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "codex-check":
            result = subscription_check(output=args.output, binary=args.codex_binary,
                                        confirmed=args.confirm_subscription_use,
                                        timeout_s=args.timeout_s)
            print(json.dumps(result, indent=2, sort_keys=True))
            return {"passed": 0, "failed": 2, "blocked": 3}[result["status"]]
        if args.command == "doctor":
            print(json.dumps(Docker().doctor(), indent=2))
        elif args.command == "report":
            print(json.dumps(generate(args.directory), indent=2))
        elif args.command == "compare":
            result = compare(args.baseline_run, args.baseline,
                             args.candidate_run or args.baseline_run, args.candidate,
                             treatment=args.treatment)
            if args.output is not None:
                result["artifacts"] = write_comparison(result, args.output)
            print(json.dumps(result, indent=2))
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
    except (BudgetError, CompareError, ConfigError, CoordinationError, DockerError,
            ProviderError, RecoveryError, ResampleError, SubscriptionCheckError,
            OSError, ValueError, KeyError) as error:
        print(f"evalnoise: {error}", file=sys.stderr)
        return 2
