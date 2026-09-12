"""Strict, bounded experiment configuration and randomized block scheduling."""

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import random
import re


class ConfigError(ValueError):
    pass


def keys(value, required, optional=()):
    if not isinstance(value, dict):
        raise ConfigError("Expected a JSON object")
    missing, extra = set(required) - value.keys(), value.keys() - set(required) - set(optional)
    if missing or extra:
        raise ConfigError(f"Missing keys: {sorted(missing)}; unknown keys: {sorted(extra)}")


def number(value, name, minimum, maximum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
        raise ConfigError(f"{name} must be a finite number")
    if integer and not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", value):
        raise ConfigError("IDs must match [a-z][a-z0-9_-]{0,47}")
    return value


def model_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._/-]{0,63}", value):
        raise ConfigError("Model IDs must match [a-z0-9][a-z0-9._/-]{0,63}")
    return value


@dataclass(frozen=True)
class Verifier:
    image: str
    command: tuple[str, ...]
    version: str
    cpus: float
    memory_mb: int
    timeout_s: float


@dataclass(frozen=True)
class Agent:
    name: str
    version: str
    model: str
    max_steps: int
    max_attempts: int
    parameters: dict


@dataclass(frozen=True)
class Task:
    id: str
    image: str
    command: tuple[str, ...]
    expected_exit: int = 0
    verifier: Verifier | None = None
    agent: Agent | None = None


@dataclass(frozen=True)
class Profile:
    id: str
    cpus: float
    memory_mb: int
    timeout_s: float
    concurrency: int = 1
    cpuset_cpus: str | None = None
    sample_interval_s: float | None = None


def cpuset(value):
    """Expand a `--cpuset-cpus` mask into explicit indices, rejecting ambiguous forms."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,4}(-\d{1,4})?(,\d{1,4}(-\d{1,4})?)*", value):
        raise ConfigError("cpuset_cpus must be a comma-separated list of indices or ascending ranges")
    indices = []
    for part in value.split(","):
        start, _, end = part.partition("-")
        low, high = int(start), int(end) if end else int(start)
        if high < low:
            raise ConfigError(f"cpuset_cpus range {part!r} is not ascending")
        indices.extend(range(low, high + 1))
    if len(set(indices)) != len(indices):
        raise ConfigError("cpuset_cpus repeats a CPU index")
    if len(indices) > 4096:
        raise ConfigError("cpuset_cpus selects an implausible number of CPUs")
    return tuple(indices)


def prune(value):
    """Unset optional v0.4 fields are omitted so v0.3 config and contract hashes are unchanged."""
    value = dict(value)
    for field in ("provider", "budget"):
        if value.get(field) is None:
            value.pop(field, None)
    tasks = value.get("tasks")
    if isinstance(tasks, (list, tuple)):
        value["tasks"] = type(tasks)(
            {k: v for k, v in task.items() if not (k == "agent" and v is None)} for task in tasks)
    return value


@dataclass(frozen=True)
class Experiment:
    name: str
    seed: int
    repeats: int
    tasks: tuple[Task, ...]
    profiles: tuple[Profile, ...]
    sample_interval_s: float = 0
    schema_version: int = 1
    provider: dict | None = None
    budget: dict | None = None

    def data(self):
        return prune(asdict(self))

    def digest(self):
        return hashlib.sha256(json.dumps(self.data(), sort_keys=True).encode()).hexdigest()


def process_spec(value):
    image, command = value["image"], value["command"]
    if not isinstance(image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}", image):
        raise ConfigError("Invalid image reference")
    if not isinstance(command, list) or not 1 <= len(command) <= 64:
        raise ConfigError("command must be an argv array of 1..64 strings, not a shell string")
    if any(not isinstance(arg, str) or len(arg) > 8192 or "\0" in arg for arg in command) or not command[0]:
        raise ConfigError("Invalid command argument")
    return image, tuple(command)


PARAMETER_BOUNDS = {"temperature": (0, 2, False), "top_p": (0, 1, False),
                    "max_output_tokens": (1, 32768, True)}


def agent_parameters(value):
    """Only a fixed numeric whitelist. No environment, credentials, or free-form fields."""
    keys(value, ("max_output_tokens",), ("temperature", "top_p"))
    parameters = {}
    for name, item in value.items():
        low, high, integer = PARAMETER_BOUNDS[name]
        parameters[name] = number(item, f"agent.parameters.{name}", low, high, integer)
    return parameters


def agent_spec(value):
    keys(value, ("name", "version", "model", "max_steps", "parameters"), ("max_attempts",))
    return Agent(identifier(value["name"]), identifier(value["version"]),
                 model_id(value["model"]),
                 int(number(value["max_steps"], "agent.max_steps", 1, 20, True)),
                 int(number(value.get("max_attempts", 1), "agent.max_attempts", 1, 5, True)),
                 agent_parameters(value["parameters"]))


def provider_spec(value):
    keys(value, ("kind", "cassette"))
    if value["kind"] != "recorded":
        raise ConfigError("Only the recorded provider is supported; no live client exists")
    if not isinstance(value["cassette"], str) or not 1 <= len(value["cassette"]) <= 128:
        raise ConfigError("provider.cassette must be a short relative path")
    return {"kind": "recorded", "cassette": value["cassette"]}


def budget_spec(value):
    keys(value, ("max_calls", "max_attempts", "max_tool_steps", "max_input_tokens",
                 "max_output_tokens", "max_cost_micros", "prices"))
    budget = {name: int(number(value[name], f"budget.{name}", 0, 100_000_000, True))
              for name in ("max_calls", "max_attempts", "max_tool_steps", "max_input_tokens",
                           "max_output_tokens", "max_cost_micros")}
    if not isinstance(value["prices"], dict) or not 1 <= len(value["prices"]) <= 20:
        raise ConfigError("budget.prices must declare 1..20 models")
    prices = {}
    for model, price in value["prices"].items():
        keys(price, ("input_micros_per_mtok", "output_micros_per_mtok"))
        prices[model_id(model)] = {
            name: int(number(price[name], f"budget.prices.{model}.{name}", 0, 1_000_000_000, True))
            for name in ("input_micros_per_mtok", "output_micros_per_mtok")}
    return budget, prices


def parse(value):
    keys(value, ("schema_version", "name", "seed", "repeats", "tasks", "profiles"),
         ("sample_interval_s", "provider", "budget"))
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ConfigError("Only schema_version 1 is supported")
    name = identifier(value["name"])
    seed = int(number(value["seed"], "seed", 0, 2**32 - 1, True))
    repeats = int(number(value["repeats"], "repeats", 1, 100, True))
    interval = number(value.get("sample_interval_s", 0), "sample_interval_s", 0, 60)
    if 0 < interval < 2:
        raise ConfigError("Sampling must be disabled (0) or at least 2 seconds apart")
    for field, cap in (("tasks", 100), ("profiles", 20)):
        if not isinstance(value[field], list) or not 1 <= len(value[field]) <= cap:
            raise ConfigError(f"{field} must contain 1..{cap} entries")
    provider = provider_spec(value["provider"]) if value.get("provider") is not None else None
    budget, prices = budget_spec(value["budget"]) if value.get("budget") is not None else (None, None)
    tasks = []
    for task in value["tasks"]:
        keys(task, ("id", "image", "command"), ("expected_exit", "verifier", "agent"))
        image, command = process_spec(task)
        verifier = None
        if task.get("verifier") is not None:
            spec = task["verifier"]
            keys(spec, ("image", "command", "version", "cpus", "memory_mb", "timeout_s"))
            verifier_image, verifier_command = process_spec(spec)
            verifier = Verifier(verifier_image, verifier_command, identifier(spec["version"]),
                                number(spec["cpus"], "verifier.cpus", .1, 64),
                                int(number(spec["memory_mb"], "verifier.memory_mb", 16, 65536, True)),
                                number(spec["timeout_s"], "verifier.timeout_s", .1, 3600))
        agent = agent_spec(task["agent"]) if task.get("agent") is not None else None
        if agent is not None and verifier is None:
            raise ConfigError(
                "An agent task requires a verifier; a candidate must never grade its own answer")
        if agent is not None and provider is None:
            raise ConfigError("An agent task requires a root provider block")
        if agent is not None and budget is None:
            raise ConfigError("An agent task requires a root budget block")
        if agent is not None and (prices is None or agent.model not in prices):
            raise ConfigError(f"budget.prices declares no price for model {agent.model!r}")
        tasks.append(Task(identifier(task["id"]), image, tuple(command),
                          int(number(task.get("expected_exit", 0), "expected_exit", 0, 255, True)),
                          verifier, agent))
    profiles = []
    for profile in value["profiles"]:
        keys(profile, ("id", "cpus", "memory_mb", "timeout_s"),
             ("concurrency", "cpuset_cpus", "sample_interval_s"))
        cpus = number(profile["cpus"], "cpus", .1, 64)
        override = profile.get("sample_interval_s")
        if override is not None:
            override = number(override, "profile.sample_interval_s", 0, 60)
            if 0 < override < 2:
                raise ConfigError("Sampling must be disabled (0) or at least 2 seconds apart")
        mask = profile.get("cpuset_cpus")
        if mask is not None:
            if len(cpuset(mask)) < math.ceil(cpus):
                raise ConfigError("cpuset_cpus selects fewer CPUs than the requested cpus ceiling")
        profiles.append(Profile(identifier(profile["id"]), cpus,
                                int(number(profile["memory_mb"], "memory_mb", 16, 65536, True)),
                                number(profile["timeout_s"], "timeout_s", .1, 3600),
                                int(number(profile.get("concurrency", 1), "concurrency", 1, 16, True)),
                                mask, override))
    for entries in (tasks, profiles):
        if len({entry.id for entry in entries}) != len(entries):
            raise ConfigError("Task IDs and profile IDs must each be unique")
    if repeats * len(tasks) * len(profiles) > 10000:
        raise ConfigError("An experiment is limited to 10000 trials")
    # Recovery enumerates every expected container name from the plan, so the container
    # count is bounded here as well as the trial count.
    containers = repeats * len(profiles) * sum(
        (task.agent.max_steps if task.agent else 1) + (1 if task.verifier else 0) for task in tasks)
    if containers > 10000:
        raise ConfigError(f"An experiment is limited to 10000 planned containers; this plans {containers}")
    return Experiment(name, seed, repeats, tuple(tasks), tuple(profiles), interval, 1,
                      provider, {**budget, "prices": prices} if budget is not None else None)


def load(path):
    path = Path(path)
    if path.stat().st_size > 1_000_000:
        raise ConfigError("Configuration exceeds 1 MB")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ConfigError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        return parse(json.loads(path.read_text(), object_pairs_hook=unique))
    except json.JSONDecodeError as error:
        raise ConfigError(f"Invalid JSON: {error}") from error


def plan(experiment):
    """Profiles never overlap; task order is shared within each repetition block."""
    rng = random.Random(experiment.seed)
    batches = []
    profile_indices = {profile.id: i for i, profile in enumerate(experiment.profiles)}
    task_indices = {task.id: i for i, task in enumerate(experiment.tasks)}
    for repeat in range(experiment.repeats):
        profiles, tasks = list(experiment.profiles), list(experiment.tasks)
        rng.shuffle(profiles)
        rng.shuffle(tasks)
        for profile in profiles:
            batch = len(batches)
            batches.append({"batch": batch, "repeat": repeat, "profile": profile.id,
                             "trials": [{"id": f"r{repeat:03d}-p{profile_indices[profile.id]:02d}-t{task_indices[task.id]:03d}",
                                        "task": task.id, "seed": experiment.seed + repeat}
                                       for task in tasks]})
    return batches
