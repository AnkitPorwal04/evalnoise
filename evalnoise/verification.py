"""Bounded data-only handoff and immutable task/verifier contract identity."""

from dataclasses import asdict
import hashlib
import json


PROTOCOL_KEYS = ("evalnoise_artifact", "evalnoise_verdict", "evalnoise_probe",
                 "evalnoise_observation")


def contract_hash(task, images, provider=None):
    task = task if isinstance(task, dict) else asdict(task)
    task = {key: value for key, value in task.items() if not (key == "agent" and value is None)}
    contract = {"task": task, "workload_image": images[task["image"]]["id"]}
    if task.get("verifier"):
        contract.update(verifier_image=images[task["verifier"]["image"]]["id"], protocol="json-env-v1")
    if task.get("agent"):
        snapshot = provider or {}
        contract.update(agent_protocol="agent-step-v1",
                        tool_image=images[task["image"]]["id"],
                        provider={key: snapshot.get(key) for key in
                                  ("kind", "model", "cassette_sha256", "entries")})
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()


def normalize(value, key):
    """The same strict envelope rules for a record that did not arrive through logs."""
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("Unsupported protocol version")
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 8192:
        raise ValueError("Protocol payload exceeds 8192 bytes")
    if key in ("evalnoise_artifact", "evalnoise_probe", "evalnoise_observation"):
        if set(value) != {"version", "payload"}:
            raise ValueError(f"{key} requires version and payload only")
    elif set(value) != {"version", "passed", "reason"} or type(value["passed"]) is not bool or not isinstance(value["reason"], str) or len(value["reason"]) > 1000:
        raise ValueError("Verdict requires version, boolean passed, and bounded reason")
    return value, encoded


def envelope(logs, key):
    """Accept exactly one protocol record among timestamp-prefixed Docker log lines."""
    if key not in PROTOCOL_KEYS:
        raise ValueError("Unknown protocol record")
    if logs.get("truncated"):
        raise ValueError("Truncated logs cannot establish an artifact or verdict")
    found = []
    def unique(pairs):
        value = {}
        for name, item in pairs:
            if name in value:
                raise ValueError("Duplicate protocol key")
            value[name] = item
        return value
    for line in logs.get("text", "").splitlines():
        text = line.split(" ", 1)[1] if line[:4].isdigit() and " " in line else line
        try:
            value = json.loads(text, object_pairs_hook=unique,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")))
        except (ValueError, RecursionError):
            if text.lstrip().startswith("{") and key in text:
                raise ValueError(f"Malformed {key} record") from None
            continue
        if isinstance(value, dict) and key in value:
            if set(value) != {key}:
                raise ValueError("Protocol record contains unexpected fields")
            found.append(value[key])
    if len(found) != 1:
        raise ValueError(f"Expected exactly one {key} record, found {len(found)}")
    return normalize(found[0], key)
