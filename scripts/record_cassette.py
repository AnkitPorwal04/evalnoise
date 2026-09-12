"""Record fixture cassettes from a scripted policy, offline.

This is not a model. It is a deterministic scripted policy over the same pure tool
functions the container image runs, so the recorded observations are byte-identical to
what a real tool container produces. Run it whenever the tool surface, the prompts, or
the agent parameters change.
"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import tool as toolbox
from evalnoise.agent import assistant_message, initial_messages, observation_message
from evalnoise.budget import estimate_prompt_tokens
from evalnoise.provider import request_sha256

MODEL = "fixture/sum-reader-v1"
PARAMETERS = {"max_output_tokens": 256, "temperature": 0, "top_p": 1}
SEEDS = (42, 43)


def call(index, name, arguments):
    return {"tool_call_id": f"c{index}", "function_name": name, "arguments": arguments}


def reading_policy(seed):
    yield call(1, "list_files", {}), []
    yield call(2, "read_file", {"path": toolbox.LIMIT_FILE}), [
        {"outcome": "transient_error", "error": "simulated transport fault, recorded for retry coverage"}]
    yield call(3, "final_answer", {"sum_of_squares": toolbox.expected_answer(seed)}), []


def lazy_policy(seed):
    yield call(1, "final_answer", {"sum_of_squares": 0}), []


def record(policy, seeds):
    entries = {}
    for seed in seeds:
        messages = initial_messages()
        for tool_call, failures in policy(seed):
            digest = request_sha256(MODEL, PARAMETERS, messages)
            usage = {"prompt_tokens": estimate_prompt_tokens(messages),
                     "completion_tokens": 16 + 4 * len(json.dumps(tool_call, sort_keys=True)) // 10,
                     "cache_tokens": 0}
            entry = {"request_sha256": digest,
                     "response": {"stop_reason": "tool_call", "text": None,
                                  "tool_call": tool_call, "usage": usage,
                                  "response_id": f"fixture-{digest[:8]}"}}
            if failures:
                entry["attempts"] = failures
            if digest in entries and entries[digest] != entry:
                raise SystemExit(f"Conflicting recordings for request {digest}")
            entries[digest] = entry
            if tool_call["function_name"] == "final_answer":
                break
            payload = {"tool_call_id": tool_call["tool_call_id"],
                       "result": toolbox.dispatch(seed, tool_call["function_name"],
                                                  tool_call["arguments"])}
            messages = messages + [assistant_message(tool_call),
                                   observation_message(tool_call, payload)]
    return list(entries.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=("reading", "lazy"), default="reading")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--label", default="scripted-fixture-policy")
    args = parser.parse_args()
    policy = reading_policy if args.policy == "reading" else lazy_policy
    document = {
        "schema_version": 1, "protocol": "agent-step-v1", "kind": "recorded",
        "model": MODEL, "provider_label": args.label, "seeds": list(SEEDS),
        "recorded_at": "fixture", "entries": record(policy, SEEDS),
        "note": ("Recorded offline from a deterministic scripted policy over the same pure tool "
                 "functions the container image runs. No provider was contacted and nothing was "
                 "charged. Token counts are synthetic. This is a reviewed in-repo fixture, not a "
                 "public benchmark dataset and not evidence about any model."),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(f"{args.out}: {len(document['entries'])} entries for seeds {list(SEEDS)}")


if __name__ == "__main__":
    main()
