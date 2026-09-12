"""Recorded, deterministic model responses for offline agent runs.

There is deliberately no HTTP client, no socket use, and no credential lookup in this
module. A recorded provider replays responses that were recorded against a scripted
fixture policy; it never generates, interpolates, or falls back to a nearest entry.

The cassette key is the SHA-256 of the *full* canonical request: protocol, model,
parameters, and the entire message list including every prior tool observation verbatim.
A transcript that skipped a tool call, or that received a different observation, produces
a different digest and therefore finds no recorded entry. That is what makes a replay
faithful to the interaction that was recorded. It is not a claim that the fixture answer
is underivable without tools.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .config import ConfigError, keys, number

PROTOCOL = "agent-step-v1"
CASSETTE_SCHEMA = 1
MAX_CASSETTE_BYTES = 1_048_576
MAX_ENTRIES = 512
MAX_RECORDED_ATTEMPTS = 8
MAX_TEXT = 4096
STOP_REASONS = ("tool_call", "final_text")
ATTEMPT_OUTCOMES = ("transient_error", "protocol_error", "timeout")


class ProviderError(RuntimeError):
    pass


class ProviderExhausted(ProviderError):
    """Every recorded attempt failed. Carries the attempts so they stay in evidence."""

    def __init__(self, message, attempts):
        super().__init__(message)
        self.attempts = tuple(attempts)


@dataclass(frozen=True)
class Usage:
    """Simulated reported token counts. No request left this host, so nothing was charged."""

    prompt_tokens: int
    completion_tokens: int
    cache_tokens: int

    def data(self):
        return {"prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "cache_tokens": self.cache_tokens}


@dataclass(frozen=True)
class Attempt:
    index: int
    outcome: str
    error: str | None

    def data(self):
        return {"index": self.index, "outcome": self.outcome, "error": self.error}


@dataclass(frozen=True)
class Completion:
    stop_reason: str
    text: str | None
    tool_call: dict | None
    usage: Usage
    response_id: str | None
    attempts: tuple

    def data(self):
        return {"stop_reason": self.stop_reason, "text": self.text, "tool_call": self.tool_call,
                "usage": self.usage.data(), "response_id": self.response_id,
                "attempts": [attempt.data() for attempt in self.attempts]}


def canonical_request(model, parameters, messages):
    return {"protocol": PROTOCOL, "model": model, "parameters": parameters, "messages": messages}


def request_sha256(model, parameters, messages):
    """Canonical over the whole conversation, so a replay cannot ignore tool observations."""
    encoded = json.dumps(canonical_request(model, parameters, messages),
                         sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _tool_call(value):
    keys(value, ("tool_call_id", "function_name", "arguments"))
    for field in ("tool_call_id", "function_name"):
        if not isinstance(value[field], str) or not 1 <= len(value[field]) <= 64:
            raise ConfigError(f"Recorded tool call {field} must be a short string")
    if not isinstance(value["arguments"], dict) or len(value["arguments"]) > 8:
        raise ConfigError("Recorded tool call arguments must be a small JSON object")
    return {"tool_call_id": value["tool_call_id"], "function_name": value["function_name"],
            "arguments": value["arguments"]}


def _response(value):
    keys(value, ("stop_reason", "usage"), ("text", "tool_call", "response_id"))
    if value["stop_reason"] not in STOP_REASONS:
        raise ConfigError(f"Recorded stop_reason must be one of {STOP_REASONS}")
    usage = value["usage"]
    keys(usage, ("prompt_tokens", "completion_tokens", "cache_tokens"))
    counts = {name: int(number(usage[name], f"usage.{name}", 0, 10_000_000, True))
              for name in ("prompt_tokens", "completion_tokens", "cache_tokens")}
    if counts["cache_tokens"] > counts["prompt_tokens"]:
        raise ConfigError("Recorded cache_tokens cannot exceed prompt_tokens")
    text = value.get("text")
    if text is not None and (not isinstance(text, str) or len(text) > MAX_TEXT):
        raise ConfigError(f"Recorded text must be null or at most {MAX_TEXT} characters")
    call = value.get("tool_call")
    if value["stop_reason"] == "tool_call" and call is None:
        raise ConfigError("A tool_call stop_reason requires a recorded tool_call")
    if value["stop_reason"] == "final_text" and call is not None:
        raise ConfigError("A final_text stop_reason must not carry a tool_call")
    identifier = value.get("response_id")
    if identifier is not None and (not isinstance(identifier, str) or len(identifier) > 64):
        raise ConfigError("Recorded response_id must be null or a short string")
    return {"stop_reason": value["stop_reason"], "text": text,
            "tool_call": _tool_call(call) if call is not None else None,
            "usage": Usage(**counts), "response_id": identifier}


def _attempt(value):
    keys(value, ("outcome",), ("error",))
    if value["outcome"] not in ATTEMPT_OUTCOMES:
        raise ConfigError(f"A recorded failed attempt outcome must be one of {ATTEMPT_OUTCOMES}")
    error = value.get("error")
    if error is not None and (not isinstance(error, str) or len(error) > 256):
        raise ConfigError("A recorded attempt error must be null or a short string")
    return {"outcome": value["outcome"], "error": error}


def _entry(value):
    keys(value, ("request_sha256",), ("attempts", "response", "note"))
    digest = value["request_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ConfigError("A cassette entry needs a lowercase hexadecimal request_sha256")
    attempts = value.get("attempts") or []
    if not isinstance(attempts, list) or len(attempts) > MAX_RECORDED_ATTEMPTS:
        raise ConfigError(f"Recorded attempts must be a list of at most {MAX_RECORDED_ATTEMPTS}")
    response = value.get("response")
    if response is None and not attempts:
        raise ConfigError("A cassette entry must record a response, failed attempts, or both")
    return {"request_sha256": digest, "failed_attempts": [_attempt(a) for a in attempts],
            "response": _response(response) if response is not None else None}


def parse_cassette(value, digest):
    keys(value, ("schema_version", "protocol", "kind", "model", "entries"),
         ("provider_label", "recorded_at", "note", "seeds"))
    if type(value["schema_version"]) is not int or value["schema_version"] != CASSETTE_SCHEMA:
        raise ConfigError(f"Only cassette schema_version {CASSETTE_SCHEMA} is supported")
    if value["protocol"] != PROTOCOL:
        raise ConfigError(f"Cassette protocol must be {PROTOCOL!r}")
    if value["kind"] != "recorded":
        raise ConfigError("Only recorded cassettes are supported in the offline slice")
    if not isinstance(value["model"], str) or not 1 <= len(value["model"]) <= 64:
        raise ConfigError("Cassette model must be a short string")
    if not isinstance(value["entries"], list) or not 1 <= len(value["entries"]) <= MAX_ENTRIES:
        raise ConfigError(f"A cassette holds 1..{MAX_ENTRIES} entries")
    entries = {}
    for item in value["entries"]:
        parsed = _entry(item)
        if parsed["request_sha256"] in entries:
            raise ConfigError("A cassette must not record the same request twice")
        entries[parsed["request_sha256"]] = parsed
    for field in ("provider_label", "recorded_at", "note"):
        if value.get(field) is not None and not isinstance(value[field], str):
            raise ConfigError(f"Cassette {field} must be a string when present")
    seeds = value.get("seeds")
    if seeds is not None and (not isinstance(seeds, list) or len(seeds) > 128
                              or any(type(s) is not int for s in seeds)):
        raise ConfigError("Cassette seeds must be a bounded list of integers when present")
    snapshot = {"kind": "recorded", "protocol": PROTOCOL, "model": value["model"],
                "cassette_sha256": digest, "entries": len(entries),
                "provider_label": value.get("provider_label"),
                "recorded_at": value.get("recorded_at"), "seeds": seeds,
                "note": ("Replayed from a recorded in-repo fixture cassette. No request left this "
                         "host and nothing was charged. Token counts are simulated reported "
                         "values, not provider-billed usage.")}
    return snapshot, entries


def load_cassette(reference, base):
    """Freeze a bounded, path-restricted cassette at preflight and hash exactly those bytes."""
    if not isinstance(reference, str) or not reference:
        raise ConfigError("A recorded provider requires a cassette path")
    candidate = Path(reference)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ConfigError("Cassette paths must be relative and must not traverse upwards")
    if len(candidate.parts) > 4:
        raise ConfigError("Cassette paths are limited to four path components")
    path = (Path(base) / candidate).resolve()
    root = Path(base).resolve()
    if root != path and root not in path.parents:
        raise ConfigError("Cassette must resolve inside the configuration's directory tree")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ConfigError(f"Unreadable cassette {reference!r}: {error}") from error
    if len(raw) > MAX_CASSETTE_BYTES:
        raise ConfigError(f"Cassette exceeds {MAX_CASSETTE_BYTES} bytes")
    digest = hashlib.sha256(raw).hexdigest()

    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ConfigError(f"Duplicate cassette JSON key: {key}")
            result[key] = item
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(
                               ConfigError("Non-finite JSON in a cassette")))
    except UnicodeDecodeError as error:
        raise ConfigError(f"Cassette is not valid UTF-8: {error}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"Invalid cassette JSON: {error}") from error
    snapshot, entries = parse_cassette(value, digest)
    snapshot["path"] = str(candidate)
    return RecordedProvider(snapshot, entries)


class RecordedProvider:
    """Replays recorded responses keyed by the full canonical request."""

    kind = "recorded"

    def __init__(self, snapshot, entries):
        self.snapshot = snapshot
        self._entries = entries

    def complete(self, model, parameters, messages, max_attempts):
        digest = request_sha256(model, parameters, messages)
        entry = self._entries.get(digest)
        if entry is None:
            raise ProviderError(
                f"No recorded response for request {digest}. The recorded provider never "
                "generates a reply; record this exact conversation prefix or fix the transcript.")
        attempts = []
        for failure in entry["failed_attempts"]:
            if len(attempts) >= max_attempts:
                raise ProviderExhausted(
                    f"Recorded attempts exhausted the configured limit of {max_attempts}", attempts)
            attempts.append(Attempt(len(attempts) + 1, failure["outcome"], failure["error"]))
        if entry["response"] is None:
            raise ProviderExhausted(
                f"Every recorded attempt for request {digest} failed", attempts)
        if len(attempts) >= max_attempts:
            raise ProviderExhausted(
                f"Recorded attempts exhausted the configured limit of {max_attempts}", attempts)
        attempts.append(Attempt(len(attempts) + 1, "ok", None))
        response = entry["response"]
        return Completion(response["stop_reason"], response["text"], response["tool_call"],
                          response["usage"], response["response_id"], tuple(attempts))
