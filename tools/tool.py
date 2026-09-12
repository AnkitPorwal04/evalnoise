"""Stateless seed-derived tool surface. A fixed tool table; nothing is evaluated."""

import base64
import json
import os
import sys

LIMIT_FILE = "limit.txt"
NOTES_FILE = "notes.txt"
NOTES = ("Sum the squares of every integer from 0 through the value in limit.txt, "
         "inclusive, then report it with final_answer.")
MAX_PATH = 64


def limit_for(seed):
    return seed % 20


def workspace(seed):
    return {LIMIT_FILE: str(limit_for(seed)), NOTES_FILE: NOTES}


def expected_answer(seed):
    n = limit_for(seed)
    return n * (n + 1) * (2 * n + 1) // 6


def dispatch(seed, name, arguments):
    files = workspace(seed)
    if name == "list_files":
        if arguments:
            return {"error": "list_files takes no arguments"}
        return {"files": sorted(files)}
    if name == "read_file":
        if set(arguments) != {"path"}:
            return {"error": "read_file takes exactly one argument named path"}
        path = arguments["path"]
        if not isinstance(path, str) or len(path) > MAX_PATH:
            return {"error": "path must be a short string"}
        if path not in files:
            return {"error": f"no such file: {path}"}
        return {"path": path, "content": files[path]}
    return {"error": f"unknown tool: {name}"}


def probe_network():
    import socket
    try:
        socket.create_connection(("example.com", 80), timeout=3).close()
    except OSError as error:
        return {"connected": False, "error": f"{type(error).__name__}: {error}"}
    return {"connected": True, "error": None}


def dwell_seconds(argv):
    if "--dwell" not in argv:
        return 0
    value = float(argv[argv.index("--dwell") + 1])
    if not 0 <= value <= 600:
        raise ValueError("dwell must be between 0 and 600 seconds")
    return value


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "tool"
    # A dwell is a recovery-test fixture only. It delays output without changing it, so a
    # dwelling step replays the same cassette entries as an instant one.
    delay = dwell_seconds(sys.argv[2:])
    if delay:
        import time
        time.sleep(delay)
    if mode == "probe-network":
        payload = probe_network()
    elif mode == "tool":
        seed = int(os.environ["EVALNOISE_SEED"])
        raw = base64.b64decode(os.environ["EVALNOISE_TOOL_CALL_B64"], validate=True)
        call = json.loads(raw.decode("utf-8"))
        if set(call) != {"version", "payload"} or call["version"] != 1:
            payload = {"error": "unsupported tool call envelope"}
        else:
            request = call["payload"]
            if set(request) != {"tool_call_id", "function_name", "arguments"}:
                payload = {"error": "tool call payload requires id, name, and arguments"}
            else:
                payload = {"tool_call_id": request["tool_call_id"],
                           "result": dispatch(seed, request["function_name"], request["arguments"])}
    else:
        raise ValueError(f"Unknown tool mode: {mode}")
    print(json.dumps({"evalnoise_observation": {"version": 1, "payload": payload}},
                     sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
