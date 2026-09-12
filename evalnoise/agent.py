"""Bounded tool/action loop with a host-side model boundary.

The model call happens in this process. Every tool action runs as a fresh hardened
container through the ordinary `trial()` path, so each step keeps the resource audit,
telemetry, classification, ownership labels, and cleanup guarantees of a normal workload.
No container receives credentials or network access.

Step records use Harbor's ATIF field names so a one-way exporter stays a mapping rather
than a rewrite. Nothing here imports Harbor.
"""

import json
import time

from .budget import BudgetRefused
from .config import Task
from .endpoint import utc
from .provider import ProviderError, ProviderExhausted, request_sha256
from .storage import write_json
from .verification import envelope, normalize

PROTOCOL = "agent-step-v1"
SYSTEM_PROMPT = ("You are solving a small arithmetic task inside a sandbox. Inspect the "
                 "workspace with the provided tools, then report the result with "
                 "final_answer. Emit exactly one tool call per turn.")
INSTRUCTION = ("Read the workspace and report the sum of the squares described by its "
               "notes. Use final_answer with an integer value.")
MAX_OBSERVATION_CHARS = 4096

TOOLS = {
    "list_files": {},
    "read_file": {"path": str},
    "final_answer": {"sum_of_squares": int},
}
CONTAINER_TOOLS = ("list_files", "read_file")


def tool_schema():
    return [{"name": name, "arguments": sorted(spec)} for name, spec in sorted(TOOLS.items())]


def validate_tool_call(call):
    if not isinstance(call, dict):
        raise ValueError("Tool call must be an object")
    name = call.get("function_name")
    if name not in TOOLS:
        raise ValueError(f"Unknown tool: {name!r}")
    arguments = call.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object")
    expected = TOOLS[name]
    if set(arguments) != set(expected):
        raise ValueError(f"Tool {name} requires exactly {sorted(expected)}")
    for field, kind in expected.items():
        value = arguments[field]
        if kind is int and (type(value) is not int or isinstance(value, bool)):
            raise ValueError(f"Tool {name} argument {field} must be an integer")
        if kind is str and (not isinstance(value, str) or not 1 <= len(value) <= 64):
            raise ValueError(f"Tool {name} argument {field} must be a short string")
    return call


def initial_messages():
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": INSTRUCTION, "tools": tool_schema()}]


def observation_message(call, payload):
    return {"role": "tool", "tool_call_id": call["tool_call_id"],
            "content": json.dumps(payload, sort_keys=True, separators=(",", ":"))}


def assistant_message(call):
    return {"role": "assistant", "tool_call": call}


def _record(result, agent, directory):
    result["agent"] = agent
    write_json(directory / "trials" / f"{result['id']}.json", result)


def _settlement_outcome(metrics, status, reason, detail):
    """An accounting overrun outranks the reason the call ended.

    Every settlement path routes through here so the step trace, the ledger, and the trial
    status cannot disagree about whether a ceiling was breached.
    """
    error = (metrics or {}).get("accounting_error")
    if error is None:
        return status, reason, detail
    overruns = "; ".join(error["overruns"])
    context = f" after {reason}: {detail}" if detail else ""
    return "budget_exhausted", "usage_overran_reservation", overruns + context


def run_agent(backend, run_id, directory, result, task, profile, image, stop, provider, ledger):
    """Drives the loop, mutating `result`. A final answer leaves it pending verification."""
    spec = task.agent
    started = time.monotonic()
    agent = {"schema_version": 1, "protocol": PROTOCOL, "name": spec.name, "version": spec.version,
             "model": {"name": spec.model, "provider": provider.snapshot.get("provider_label")},
             "parameters": dict(spec.parameters), "max_steps": spec.max_steps,
             "max_attempts": spec.max_attempts, "tool_image": image["id"],
             "steps": [], "termination": None, "final_artifact": None,
             "provider_s_total": 0.0, "tool_container_s_total": 0.0, "agent_wall_s": None,
             "provider_attempts": 0, "clock_scope": (
                 "Host-side provider time and container time are separate domains and are "
                 "never summed into a container measurement. The parent record aggregates "
                 "steps; it is not itself a container observation.")}
    result["execution_status"] = "agent_incomplete"
    result["status"] = "agent_incomplete"
    _record(result, agent, directory)
    messages = initial_messages()

    def finish(status, reason, detail=None):
        agent["termination"] = {"reason": reason, "detail": detail}
        agent["agent_wall_s"] = time.monotonic() - started
        result["execution_status"] = status
        result["status"] = "pending_verification" if status == "agent_answered" else status
        _record(result, agent, directory)

    for index in range(1, spec.max_steps + 1):
        if stop.is_set():
            result["cancelled"] = True
            return finish("cancelled", "cancelled", "Cancellation observed before the next step")
        step = {"step_id": index, "source": "agent", "timestamp": utc(), "model_name": spec.model,
                "message": "", "tool_calls": None, "observation": None, "metrics": None,
                "llm_call_count": 1,
                "extra": {"request_sha256": request_sha256(spec.model, spec.parameters, messages)}}
        try:
            reservation = ledger.admit_call(spec.model, messages,
                                            spec.parameters["max_output_tokens"],
                                            spec.max_attempts)
        except BudgetRefused as error:
            step["extra"]["budget_refusal"] = str(error)
            agent["steps"].append(step)
            return finish("budget_exhausted", "budget_refused_before_provider_call", str(error))
        step["extra"]["admission"] = reservation
        call_started = time.monotonic()
        try:
            completion = provider.complete(spec.model, spec.parameters, messages, spec.max_attempts)
        except ProviderExhausted as error:
            # Failed attempts are real spend against the attempt ceiling. Settle them into
            # the ledger so the exhausted path can never escape the bound, and keep the same
            # attempt trace on the step as a successful call would carry.
            step["extra"]["provider_attempts"] = [a.data() for a in error.attempts]
            step["metrics"] = ledger.commit_failed_call(len(error.attempts), reservation)
            agent["provider_attempts"] += len(error.attempts)
            agent["steps"].append(step)
            return finish(*_settlement_outcome(
                step["metrics"], "agent_error", "provider_attempts_exhausted", str(error)))
        except ProviderError as error:
            step["extra"]["provider_attempts"] = []
            step["metrics"] = ledger.commit_failed_call(0, reservation)
            agent["steps"].append(step)
            return finish(*_settlement_outcome(
                step["metrics"], "agent_error", "provider_error", str(error)))
        finally:
            agent["provider_s_total"] += time.monotonic() - call_started
        step["extra"]["provider_attempts"] = [a.data() for a in completion.attempts]
        step["extra"]["response_id"] = completion.response_id
        step["extra"]["stop_reason"] = completion.stop_reason
        agent["provider_attempts"] += len(completion.attempts)
        # Usage that was actually reported is recorded before any accounting verdict, so an
        # overrun is an explicit error beside retained evidence rather than lost evidence.
        step["metrics"] = ledger.commit_call(spec.model, completion.usage,
                                             len(completion.attempts), reservation)
        if step["metrics"]["accounting_error"] is not None:
            agent["steps"].append(step)
            return finish(*_settlement_outcome(step["metrics"], "budget_exhausted",
                                               "usage_overran_reservation", None))
        if completion.stop_reason != "tool_call":
            step["message"] = completion.text or ""
            agent["steps"].append(step)
            return finish("agent_error", "no_tool_call",
                          "The recorded response ended without a tool call")
        try:
            call = validate_tool_call(completion.tool_call)
        except ValueError as error:
            agent["steps"].append(step)
            return finish("agent_error", "invalid_tool_call", str(error))
        step["tool_calls"] = [call]
        if call["function_name"] == "final_answer":
            candidate = {"version": 1, "payload": dict(call["arguments"])}
            try:
                value, _ = normalize(candidate, "evalnoise_artifact")
            except ValueError as error:
                agent["steps"].append(step)
                return finish("agent_error", "invalid_final_artifact", str(error))
            agent["final_artifact"] = value
            step["observation"] = {"kind": "final_answer", "accepted": True}
            agent["steps"].append(step)
            return finish("agent_answered", "final_answer", None)
        try:
            ledger.admit_step()
        except BudgetRefused as error:
            agent["steps"].append(step)
            return finish("budget_exhausted", "budget_refused_before_tool_container", str(error))
        outcome = _run_tool(backend, run_id, directory, result, task, profile, image, stop,
                            call, index)
        step["observation"] = outcome["observation"]
        step["extra"]["tool_trial"] = outcome["trial"]
        step["extra"]["tool_container_s"] = outcome["lifecycle_s"]
        agent["tool_container_s_total"] += outcome["lifecycle_s"]
        agent["steps"].append(step)
        if outcome["cleanup_error"]:
            result["cleanup_error"] = outcome["cleanup_error"]
        if outcome["error"] is not None:
            status = "cancelled" if outcome["cancelled"] else "agent_error"
            if outcome["cancelled"]:
                result["cancelled"] = True
            return finish(status, outcome["reason"], outcome["error"])
        messages = messages + [assistant_message(call),
                               observation_message(call, outcome["observation"]["content"])]
        _record(result, agent, directory)
    return finish("step_limit_reached", "step_limit",
                  f"No final answer within {spec.max_steps} steps")


def _run_tool(backend, run_id, directory, result, task, profile, image, stop, call, index):
    from .runner import trial
    payload = {"version": 1, "payload": {"tool_call_id": call["tool_call_id"],
                                         "function_name": call["function_name"],
                                         "arguments": call["arguments"]}}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    tool_task = Task(task.id, task.image, task.command)
    checked = trial(backend, run_id, directory / "agent",
                    {"id": f"{result['id']}-s{index:02d}", "task": task.id,
                     "repeat": result["repeat"], "seed": result["seed"],
                     "contract_sha256": result["contract_sha256"], "step_id": index},
                    tool_task, profile, image, 0, stop, artifact=encoded,
                    artifact_env="EVALNOISE_TOOL_CALL_B64")
    raw = {key: value for key, value in checked.items() if key != "logs"}
    outcome = {"trial": raw, "lifecycle_s": checked.get("lifecycle_s") or 0.0,
               "cleanup_error": checked["cleanup_error"], "cancelled": checked["status"] == "cancelled",
               "observation": {"kind": "tool_error", "content": None,
                               "tool_status": checked["status"]},
               "error": None, "reason": None}
    if checked["status"] == "cancelled":
        outcome["error"] = "Cancellation interrupted a tool step"
        outcome["reason"] = "cancelled"
        return outcome
    if checked["status"] != "passed":
        outcome["error"] = f"Tool container ended as {checked['status']}"
        outcome["reason"] = "tool_container_failed"
        return outcome
    if checked["cleanup_error"]:
        outcome["error"] = f"Tool container cleanup failed: {checked['cleanup_error']}"
        outcome["reason"] = "tool_cleanup_failed"
        return outcome
    try:
        value, _ = envelope(checked.get("logs", {}), "evalnoise_observation")
    except ValueError as error:
        outcome["error"] = str(error)
        outcome["reason"] = "observation_protocol_error"
        return outcome
    content = value["payload"]
    if len(json.dumps(content, sort_keys=True, separators=(",", ":"))) > MAX_OBSERVATION_CHARS:
        outcome["error"] = "Observation exceeds the retained bound"
        outcome["reason"] = "observation_too_large"
        return outcome
    outcome["observation"] = {"kind": "tool_result", "content": content,
                              "tool_status": checked["status"]}
    return outcome
