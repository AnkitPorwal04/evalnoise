"""A single known-answer Codex subscription smoke check. Not a provider benchmark.

Scope, stated narrowly on purpose:

This module answers exactly one question -- "can this host complete one tiny, known-answer
prompt through the officially supported `codex exec` path, using the operator's already
logged-in ChatGPT subscription?" -- and nothing else. It is **not** a provider, not a
benchmark, not an agent integration, and it never participates in the `RecordedProvider`
cassette path or the synthetic budget ledger. Its artifact type is `subscription_smoke`
and it is written to its own directory so no benchmark evidence is mutated.

How it reaches the subscription:

Only by executing the installed `codex` binary. There is deliberately no HTTP client, no
socket, no reverse proxy, no API key, and no read, copy, or symlink of `auth.json` or any
other credential file anywhere in this module. The child process authenticates itself the
same way an interactive `codex` invocation would, by using `CODEX_HOME`/`HOME`.

Honest limits, which the artifact repeats rather than hides:

- **This is not filesystem privacy.** `--sandbox read-only` restricts *writes* by
  model-generated commands. The child still runs as the operator's user and still needs a
  real `HOME` to find its credentials, so a read-only sandbox is not a confidentiality
  boundary and is never described as one.
- **The effective tool catalog is not verifiable before the model call** on Codex CLI
  0.153.4. `codex debug prompt-input` renders only the message list; `codex debug models`
  renders only the model catalog. Neither exposes the `tools` array that is actually sent.
  EvalNoise therefore treats an unverifiable catalog as a hard stop of its own: the
  outcome on this version is a persisted `blocked` result and no model call. This is
  EvalNoise's boundary, not a user preference, and no parameter or flag overrides it.
- **Detecting a tool call in the JSONL transcript afterwards is a detector, not a
  control.** It can only tell you a tool was already offered and used. It is recorded as a
  failure, but it is never presented as the thing that prevents tool use.
- **Feature disabling is verified by name, not by effect.** Every feature this module
  disables is first checked to exist in `codex features list` output, so a rename shows up
  as a blocked run instead of a silent no-op. That still does not prove the resulting
  catalog is empty.
"""

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from .storage import write_json

ARTIFACT_TYPE = "subscription_smoke"
ARTIFACT_SCHEMA = 1

# Pinned because the disable/override surface below was verified against exactly this
# build. A different build may rename features or config fields, and an unrecognized name
# must become a visible block, not a silent no-op.
EXPECTED_VERSION = "0.153.4"
EXPECTED_VERSION_OUTPUT = f"codex-cli {EXPECTED_VERSION}"

# The only auth state this check will proceed from. An API-key login is refused: the
# operator authorized subscription use, and an API key would bill a different account.
REQUIRED_AUTH = "Logged in using ChatGPT"
CHATGPT_LOGIN_METHOD = "chatgpt"

DEFAULT_TIMEOUT_S = 60.0
MAX_STDOUT_BYTES = 1_048_576
MAX_STDERR_BYTES = 65_536
PREFLIGHT_TIMEOUT_S = 20.0
TERMINATE_GRACE_S = 5.0

# Known-answer, tiny, and entirely public. Nothing about the host is in the prompt.
PROMPT = "What is 2+2? Respond with JSON only."
EXPECTED_ANSWER = 4
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}

# Every name here is asserted to exist in `codex features list` before use.
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "multi_agent", "apps", "hooks", "remote_plugin",
    "plugins", "goals", "browser_use", "browser_use_external", "computer_use",
    "image_generation", "view_image", "sleep_tool", "skill_search", "tool_suggest",
    "code_mode_host", "web_search_cached", "web_search_request", "memories",
)

# Every key here was validated against `--strict-config` on the pinned build by pairing it
# with a deliberately unknown key, so config load always failed before any model call.
# `project_root_fallback` is intentionally absent: no such field exists on 0.153.4, and
# passing it under --strict-config would abort the run.
CONFIG_OVERRIDES = (
    "tools.web_search=false",
    "project_doc_max_bytes=0",
    "project_root_markers=[]",
    "mcp_servers={}",
    "analytics.enabled=false",
    "history.persistence=\"none\"",
    "shell_environment_policy.inherit=none",
    "shell_environment_policy.ignore_default_excludes=false",
    "otel.log_user_prompt=false",
    "hide_agent_reasoning=true",
)

# Nothing outside this list is handed to the child. HOME/CODEX_HOME are required for the
# subscription credential lookup the operator authorized; the rest keep the binary usable.
ENV_ALLOWLIST = ("HOME", "PATH", "CODEX_HOME", "TMPDIR", "TERM")
FORBIDDEN_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)", re.IGNORECASE)

# Any JSONL item type that can execute or reach outside the turn. Seeing one is a failure.
EXECUTING_ITEM_TYPES = frozenset({
    "command_execution", "function_call", "tool_call", "local_shell_call", "mcp_tool_call",
    "web_search_call", "file_change", "patch_apply", "custom_tool_call", "exec_command",
})
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_PERSISTED_TEXT = 4096


class SubscriptionCheckError(RuntimeError):
    """A refusal or an unusable environment. Never used to report a wrong model answer."""


@dataclass(frozen=True)
class Execution:
    argv: tuple
    returncode: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool

    def data(self):
        return {"returncode": self.returncode, "duration_s": round(self.duration_s, 3),
                "timed_out": self.timed_out, "stdout_truncated": self.stdout_truncated,
                "stderr_truncated": self.stderr_truncated}


@dataclass
class Preflight:
    """Zero-model evidence gathered before deciding whether a model call is permitted."""

    version_ok: bool = False
    version_reported: str | None = None
    auth_ok: bool = False
    auth_reported: str | None = None
    features_ok: bool = False
    features_missing: tuple = ()
    features_total: int = 0
    mcp_servers_enabled: tuple = ()
    prompt_input_ok: bool = False
    prompt_input_sha256: str | None = None
    tool_catalog_verified: bool = False
    tool_catalog_reason: str = "not attempted"
    blocked_reason: str | None = None

    def data(self):
        return {"version_ok": self.version_ok, "version_reported": self.version_reported,
                "auth_ok": self.auth_ok, "auth_reported": self.auth_reported,
                "features_ok": self.features_ok,
                "features_missing": list(self.features_missing),
                "features_total": self.features_total,
                "mcp_servers_enabled": list(self.mcp_servers_enabled),
                "prompt_input_ok": self.prompt_input_ok,
                "prompt_input_sha256": self.prompt_input_sha256,
                "tool_catalog_verified": self.tool_catalog_verified,
                "tool_catalog_reason": self.tool_catalog_reason,
                "blocked_reason": self.blocked_reason}


def child_env(environ=None):
    """Build the child environment from an allowlist, then prove no secret slipped in."""
    environ = os.environ if environ is None else environ
    env = {name: environ[name] for name in ENV_ALLOWLIST if environ.get(name)}
    if "HOME" not in env:
        raise SubscriptionCheckError(
            "HOME is required so the Codex CLI can find the ChatGPT credentials you "
            "authorized. This check never reads those files itself.")
    leaked = sorted(name for name in env if FORBIDDEN_ENV.search(name))
    if leaked:
        raise SubscriptionCheckError(f"Refusing to pass credential-shaped variables: {leaked}")
    return env


def _sanitize(text, limit=MAX_PERSISTED_TEXT):
    """Strip control bytes before anything reaches an artifact, a terminal, or a report."""
    cleaned = CONTROL_CHARACTERS.sub("", text)
    return cleaned[:limit]


class _Sink:
    def __init__(self, limit):
        self.limit = limit
        self.chunks = []
        self.size = 0
        self.truncated = False

    def feed(self, chunk):
        room = self.limit - self.size
        if room <= 0:
            self.truncated = True
            return False
        if len(chunk) > room:
            self.chunks.append(chunk[:room])
            self.size = self.limit
            self.truncated = True
            return False
        self.chunks.append(chunk)
        self.size += len(chunk)
        return True

    def text(self):
        return b"".join(self.chunks).decode("utf-8", "replace")


def _group_alive(group):
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _terminate_group(process, group):
    """Signal the whole group, escalating until no member survives.

    The group id is captured at spawn rather than read back from the parent, because
    `codex` may spawn helpers and then exit itself; reading `getpgid` from an exited
    parent loses the id and strands them. Reaping the parent between probes matters too:
    an unreaped zombie leader keeps the group id alive and would otherwise be mistaken
    for a surviving helper, causing a pointless SIGKILL escalation.
    """
    if group is None or group == os.getpgrp():
        return
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        process.poll()
        if not _group_alive(group):
            return
        try:
            os.killpg(group, signal_number)
        except (ProcessLookupError, PermissionError, OSError):
            return
        deadline = time.monotonic() + TERMINATE_GRACE_S
        while time.monotonic() < deadline:
            process.poll()
            if not _group_alive(group):
                return
            time.sleep(0.02)


def run_bounded(argv, *, cwd, env, timeout_s, stdout_limit=MAX_STDOUT_BYTES,
                stderr_limit=MAX_STDERR_BYTES, popen=subprocess.Popen):
    """Stream a child with hard byte and wall-clock caps, killing its group on any breach."""
    started = time.monotonic()
    process = popen(list(argv), cwd=str(cwd), env=dict(env), stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        group = os.getpgid(process.pid)
    except (ProcessLookupError, OSError):
        group = process.pid
    out, err = _Sink(stdout_limit), _Sink(stderr_limit)
    overflow = threading.Event()

    def pump(stream, sink):
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    return
                if not sink.feed(chunk):
                    overflow.set()
                    return
        except (ValueError, OSError):
            return
        finally:
            try:
                stream.close()
            except (ValueError, OSError):
                pass

    readers = [threading.Thread(target=pump, args=(process.stdout, out), daemon=True),
               threading.Thread(target=pump, args=(process.stderr, err), daemon=True)]
    for reader in readers:
        reader.start()

    timed_out = False
    deadline = started + timeout_s
    while True:
        if process.poll() is not None:
            break
        if overflow.is_set():
            _terminate_group(process, group)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _terminate_group(process, group)
            break
        time.sleep(0.02)
    for reader in readers:
        reader.join(timeout=TERMINATE_GRACE_S)
    _terminate_group(process, group)
    return Execution(argv=tuple(argv), returncode=process.poll(), stdout=out.text(),
                     stderr=err.text(), duration_s=time.monotonic() - started,
                     timed_out=timed_out, stdout_truncated=out.truncated,
                     stderr_truncated=err.truncated)


def _codex(binary="codex"):
    resolved = shutil.which(binary)
    if not resolved:
        raise SubscriptionCheckError(
            f"The `{binary}` executable is not on PATH. Install Codex CLI {EXPECTED_VERSION} "
            "and sign in with ChatGPT before running this check.")
    return resolved


def parse_features(text):
    """`name<spaces>status<spaces>enabled` rows. Returns {name: (status, enabled)}."""
    features = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] in ("true", "false"):
            features[parts[0]] = (parts[1], parts[2] == "true")
    return features


def parse_mcp_enabled(text):
    """Names of MCP servers reported as enabled; each is an external tool surface."""
    enabled = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if parts and "enabled" in parts[1:]:
            enabled.append(parts[0])
    return tuple(enabled)


def inspect_tool_catalog(execution):
    """Look for a real tool catalog in `codex debug prompt-input` output.

    Returns `(verified, reason, sha256)`. The digest is over the raw JSON so a change in
    the effective prompt is detectable, while the content itself -- which contains local
    skill descriptions and absolute home paths -- is never persisted or logged.
    """
    if execution.returncode != 0:
        return False, "codex debug prompt-input failed", None
    digest = hashlib.sha256(execution.stdout.encode("utf-8", "replace")).hexdigest()
    try:
        document = json.loads(execution.stdout)
    except ValueError:
        return False, "codex debug prompt-input did not return JSON", digest

    catalog = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("tools", "functions") and isinstance(value, list):
                    catalog.extend(value)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)
    if not catalog:
        # The decisive fact on 0.153.4: the rendered prompt input carries messages only.
        # The `tools` array is assembled for the API request and is never shown here, so
        # "no catalog found" must be read as "unverifiable", never as "catalog is empty".
        return False, ("this Codex build renders no tool catalog in `codex debug "
                       "prompt-input`, so the effective tool list cannot be verified "
                       "before the model call"), digest
    names = sorted({entry.get("name") for entry in catalog
                    if isinstance(entry, dict) and entry.get("name")})
    executing = [name for name in names if name in EXECUTING_ITEM_TYPES or "exec" in name
                 or "shell" in name or "patch" in name]
    if executing:
        return False, f"tool catalog offers executing tools: {executing}", digest
    return True, f"tool catalog verified as non-executing: {names}", digest


def preflight(*, binary="codex", env=None, runner=run_bounded, expected_version=EXPECTED_VERSION_OUTPUT):
    """Run every zero-model check. Never calls a model and never returns a partial pass."""
    executable = _codex(binary)
    environment = child_env(env)
    scratch = tempfile.mkdtemp(prefix="evalnoise-codex-preflight-")
    state = Preflight()
    try:
        def call(args, timeout_s=PREFLIGHT_TIMEOUT_S):
            return runner([executable, *args], cwd=scratch, env=environment, timeout_s=timeout_s)

        version = call(["--version"])
        state.version_reported = _sanitize(version.stdout.strip(), 200) or None
        state.version_ok = version.returncode == 0 and state.version_reported == expected_version
        if not state.version_ok:
            state.blocked_reason = (
                f"Codex CLI version pin failed: expected {expected_version!r}, found "
                f"{state.version_reported!r}. The disable and config surface used here was "
                "verified only against the pinned build.")
            return state

        auth = call(["login", "status"])
        # Verified on 0.153.4: `login status` reports on stderr while every other probe
        # used here reports on stdout. Reading stdout alone silently looks logged out.
        reported = auth.stdout.strip() or auth.stderr.strip()
        state.auth_reported = _sanitize(reported, 200) or None
        state.auth_ok = auth.returncode == 0 and state.auth_reported == REQUIRED_AUTH
        if not state.auth_ok:
            state.blocked_reason = (
                f"Expected {REQUIRED_AUTH!r} before any model call, found "
                f"{state.auth_reported!r}. Run `codex login` and choose ChatGPT. This check "
                "refuses API-key auth, which would bill an account you did not authorize.")
            return state

        features = call(["features", "list"])
        catalog = parse_features(features.stdout)
        state.features_total = len(catalog)
        state.features_missing = tuple(name for name in DISABLED_FEATURES if name not in catalog)
        state.features_ok = features.returncode == 0 and not state.features_missing
        if not state.features_ok:
            state.blocked_reason = (
                f"These features are not present in this build: {list(state.features_missing)}. "
                "Passing --disable for an unknown feature would be a silent no-op, so the "
                "run is blocked rather than assumed safe.")
            return state

        servers = call(["mcp", "list"])
        state.mcp_servers_enabled = parse_mcp_enabled(servers.stdout)

        rendered = call(["debug", "prompt-input", PROMPT,
                         *_feature_flags(), *_config_flags()])
        state.prompt_input_ok = rendered.returncode == 0
        verified, reason, digest = inspect_tool_catalog(rendered)
        state.prompt_input_sha256 = digest
        state.tool_catalog_verified = verified
        state.tool_catalog_reason = reason
        if not verified:
            state.blocked_reason = (
                f"Tool catalog could not be verified as empty or non-executing: {reason}. "
                "EvalNoise blocks the model call here by policy, rather than relying on "
                "spotting a tool call in the transcript afterwards, which would only be a "
                "detector. There is no override for this.")
        return state
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _feature_flags():
    flags = []
    for name in DISABLED_FEATURES:
        flags.extend(("--disable", name))
    return flags


def _config_flags():
    flags = []
    for override in CONFIG_OVERRIDES:
        flags.extend(("-c", override))
    return flags


def exec_argv(executable, schema_path):
    """The exact hardened `codex exec` argv. Built once so the fingerprint covers it."""
    return [executable, "exec",
            "--strict-config", "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never", "--json",
            "--output-schema", str(schema_path),
            "-c", f"forced_login_method=\"{CHATGPT_LOGIN_METHOD}\"",
            *_config_flags(), *_feature_flags(), PROMPT]


def fingerprint():
    """Stable digest of the enforced surface, so a silent control change is detectable."""
    payload = {"features_disabled": list(DISABLED_FEATURES),
               "config_overrides": list(CONFIG_OVERRIDES),
               "env_allowlist": list(ENV_ALLOWLIST),
               "output_schema": OUTPUT_SCHEMA, "prompt": PROMPT,
               "version_pin": EXPECTED_VERSION}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def parse_events(stdout):
    """Strict JSONL reading. Reasoning and transcripts are counted, never persisted.

    Anything that is not a well-formed JSON object with a string `type` is a protocol
    error. Assistant text is the only content extracted; reasoning summaries and unknown
    payloads contribute a type name to a counter and nothing else, because their contents
    are not ours to store.
    """
    assistant, counts, executing = [], {}, []
    usage = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            raise SubscriptionCheckError("Codex emitted a line that is not valid JSON") from None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise SubscriptionCheckError("Codex emitted a JSON value without a string type")
        kind = event["type"]
        counts[kind] = counts.get(kind, 0) + 1
        item = event.get("item")
        if isinstance(item, dict):
            item_type = item.get("type")
            if isinstance(item_type, str):
                counts[f"item.{item_type}"] = counts.get(f"item.{item_type}", 0) + 1
                if item_type in EXECUTING_ITEM_TYPES:
                    executing.append(item_type)
                if item_type in ("agent_message", "assistant_message") and kind.endswith("completed"):
                    text = item.get("text")
                    if isinstance(text, str):
                        assistant.append(text)
        found = event.get("usage") if isinstance(event.get("usage"), dict) else None
        if isinstance(item, dict) and isinstance(item.get("usage"), dict):
            found = item["usage"]
        if found is not None:
            usage = found
    return assistant, counts, tuple(executing), usage


def parse_usage(raw):
    """Token counts only. No price, and explicitly no derived USD for a subscription."""
    if not isinstance(raw, dict):
        return None
    def integer(*names):
        for name in names:
            value = raw.get(name)
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and value >= 0:
                return value
        return None
    return {"input_tokens": integer("input_tokens", "prompt_tokens"),
            "output_tokens": integer("output_tokens", "completion_tokens"),
            "cached_input_tokens": integer("cached_input_tokens", "cache_read_input_tokens",
                                           "cache_tokens")}


def evaluate(execution):
    """Classify one finished `codex exec` result. Pure: no subprocess, no model, no I/O.

    Split out so the result-interpretation rules can be tested directly against synthetic
    `Execution` values. Reaching this function in a real run still requires a preflight
    that verified the tool catalog; it grants no path around that.
    """
    outcome = {"status": "failed", "failure": None, "answer": None, "usage": None,
               "event_counts": None}
    if execution.timed_out:
        outcome["failure"] = ("Exceeded the single-invocation deadline; process group "
                              "killed. There are no retries, by design.")
        return outcome
    if execution.stdout_truncated or execution.stderr_truncated:
        outcome["failure"] = "Output exceeded its byte cap and the process group was killed."
        return outcome
    try:
        assistant, counts, executing, usage = parse_events(execution.stdout)
    except SubscriptionCheckError as error:
        outcome["failure"] = str(error)
        return outcome
    outcome["event_counts"] = counts
    outcome["usage"] = parse_usage(usage)
    if executing:
        outcome["failure"] = (f"Executing tool items appeared in the transcript: "
                              f"{sorted(set(executing))}. Detected after the fact; this is "
                              f"evidence of a control gap, not a control.")
        return outcome
    if execution.returncode != 0:
        outcome["failure"] = (f"codex exec exited {execution.returncode}: "
                              f"{_sanitize(execution.stderr, 500)}")
        return outcome
    if len(assistant) != 1:
        outcome["failure"] = f"Expected exactly one assistant message, found {len(assistant)}."
        return outcome
    try:
        answer = json.loads(assistant[0]).get("answer")
    except ValueError:
        outcome["failure"] = "Assistant message did not satisfy the output schema."
        outcome["answer"] = _sanitize(assistant[0], 200)
        return outcome
    outcome["answer"] = answer if isinstance(answer, int) and not isinstance(answer, bool) else None
    if outcome["answer"] == EXPECTED_ANSWER:
        outcome["status"] = "passed"
    else:
        outcome["failure"] = f"Expected {EXPECTED_ANSWER}, received {outcome['answer']!r}."
    return outcome


def check(*, output, binary="codex", confirmed=False, env=None, runner=run_bounded,
          timeout_s=DEFAULT_TIMEOUT_S):
    """Preflight, then at most one model call, then one `subscription_smoke` artifact."""
    if not confirmed:
        raise SubscriptionCheckError(
            "This command may spend your ChatGPT subscription quota on one real model "
            "call, if and only if every preflight check passes first. Re-run with "
            "--confirm-subscription-use to authorize that.")
    started = time.time()
    state = preflight(binary=binary, env=env, runner=runner)
    record = {"schema_version": ARTIFACT_SCHEMA, "artifact_type": ARTIFACT_TYPE,
              "started_at": started, "codex_version": state.version_reported,
              "auth_method": state.auth_reported, "config_fingerprint": fingerprint(),
              "preflight": state.data(), "model_called": False, "status": "blocked",
              "prompt": PROMPT, "expected_answer": EXPECTED_ANSWER,
              "answer": None, "usage": None,
              # A subscription call has no per-call price. Null means "not applicable and
              # not measured"; zero would be a false claim that the call was free.
              "subscription_cost_usd": None,
              "billing_note": ("A model call may consume ChatGPT subscription quota; "
                               "preflight alone sends no model request, so a record with "
                               "model_called false claims no quota use. No per-call USD "
                               "price exists for this path, so no cost is computed or "
                               "implied."),
              "caveats": list(CAVEATS)}

    if state.blocked_reason:
        record["status"] = "blocked"
        record["blocked_reason"] = state.blocked_reason
        return _persist(output, record)

    executable = _codex(binary)
    environment = child_env(env)
    scratch = tempfile.mkdtemp(prefix="evalnoise-codex-smoke-")
    try:
        schema_path = Path(scratch) / "schema.json"
        schema_path.write_text(json.dumps(OUTPUT_SCHEMA), encoding="utf-8")
        argv = exec_argv(executable, schema_path)
        record["model_called"] = True
        execution = runner(argv, cwd=scratch, env=environment, timeout_s=timeout_s)
        record["execution"] = execution.data()
        record.update(evaluate(execution))
        return _persist(output, record)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


CAVEATS = (
    "One known-answer smoke check. It is not a provider benchmark and closes no M3 gate.",
    "`--sandbox read-only` bounds writes by model-generated commands. The child runs as "
    "your user with a real HOME so it can find the credentials you authorized, so this is "
    "explicitly not filesystem privacy or credential isolation.",
    "On Codex CLI 0.153.4 the effective tool catalog cannot be verified before the model "
    "call; `codex debug prompt-input` renders messages only. An unverifiable catalog blocks "
    "the run. That is EvalNoise's own boundary and nothing overrides it.",
    "EvalNoise reads no auth file and holds no API key. The official `codex` binary "
    "authenticates the subscription itself, and auth state is learned only from the exit "
    "status and text of `codex login status`.",
    "Spotting a tool call in the JSONL afterwards is a detector, not a prevention control.",
    "`forced_login_method=\"chatgpt\"` is set explicitly for this invocation. If the stored "
    "auth method ever changes, this setting can surface as a logout or a refused login "
    "rather than a silent fallback to another account.",
    "Feature disabling is verified by name against `codex features list`, which proves the "
    "flag is real on this build, not that the resulting catalog is empty.",
    "No reasoning text, transcript, or prompt rendering is persisted. Only allowlisted "
    "metrics, event-type counts, and the final schema-checked answer are stored.",
    "Subscription cost is unknown, not zero. Plan quota consumption is not measured by "
    "this check, so a run that did reach the model claims no figure either way.",
)


def _persist(output, record):
    record["finished_at"] = time.time()
    directory = Path(output)
    write_json(directory / "subscription-smoke.json", record)
    return record
