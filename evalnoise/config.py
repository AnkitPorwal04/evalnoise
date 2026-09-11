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


@dataclass(frozen=True)
class Task:
    id: str
    image: str
    command: tuple[str, ...]
    expected_exit: int = 0


@dataclass(frozen=True)
class Profile:
    id: str
    cpus: float
    memory_mb: int
    timeout_s: float
    concurrency: int = 1


@dataclass(frozen=True)
class Experiment:
    name: str
    seed: int
    repeats: int
    tasks: tuple[Task, ...]
    profiles: tuple[Profile, ...]
    sample_interval_s: float = 0
    schema_version: int = 1

    def data(self):
        return asdict(self)

    def digest(self):
        return hashlib.sha256(json.dumps(self.data(), sort_keys=True).encode()).hexdigest()


def parse(value):
    keys(value, ("schema_version", "name", "seed", "repeats", "tasks", "profiles"), ("sample_interval_s",))
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
    tasks = []
    for task in value["tasks"]:
        keys(task, ("id", "image", "command"), ("expected_exit",))
        image, command = task["image"], task["command"]
        if not isinstance(image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}", image):
            raise ConfigError("Invalid image reference")
        if not isinstance(command, list) or not 1 <= len(command) <= 64:
            raise ConfigError("command must be an argv array of 1..64 strings, not a shell string")
        if any(not isinstance(arg, str) or len(arg) > 8192 or "\0" in arg for arg in command) or not command[0]:
            raise ConfigError("Invalid command argument")
        tasks.append(Task(identifier(task["id"]), image, tuple(command),
                          int(number(task.get("expected_exit", 0), "expected_exit", 0, 255, True))))
    profiles = []
    for profile in value["profiles"]:
        keys(profile, ("id", "cpus", "memory_mb", "timeout_s"), ("concurrency",))
        profiles.append(Profile(identifier(profile["id"]),
                                number(profile["cpus"], "cpus", .1, 64),
                                int(number(profile["memory_mb"], "memory_mb", 16, 65536, True)),
                                number(profile["timeout_s"], "timeout_s", .1, 3600),
                                int(number(profile.get("concurrency", 1), "concurrency", 1, 16, True))))
    for entries in (tasks, profiles):
        if len({entry.id for entry in entries}) != len(entries):
            raise ConfigError("Task IDs and profile IDs must each be unique")
    if repeats * len(tasks) * len(profiles) > 10000:
        raise ConfigError("An experiment is limited to 10000 trials")
    return Experiment(name, seed, repeats, tuple(tasks), tuple(profiles), interval)


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
