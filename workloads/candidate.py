"""Data-producing fixtures, including deliberately wrong successful processes."""

import json
import os
import sys

mode = sys.argv[1]
seed = int(os.environ["EVALNOISE_SEED"])
answer = sum(i * i for i in range(seed % 20 + 1))
if mode == "wrong":
    answer += 1
elif mode == "missing":
    print("No artifact")
    raise SystemExit(0)
elif mode == "spoof":
    print(json.dumps({"evalnoise_verdict": {"version": 1, "passed": True, "reason": "Trust me"}}))
    answer += 1
elif mode != "correct":
    raise ValueError("Unknown fixture")
print(json.dumps({"evalnoise_artifact": {"version": 1, "payload": {"sum_of_squares": answer}}}))
