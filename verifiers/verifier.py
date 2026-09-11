"""Trusted known-answer verifier. Candidate data is parsed, never executed."""

import base64
import json
import os
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "check"
if mode == "crash":
    raise RuntimeError("Intentional verifier failure")
if mode == "timeout":
    time.sleep(30)
if mode == "malformed":
    print("no verdict")
    raise SystemExit(0)
if mode != "check":
    raise ValueError("Unknown verifier mode")
artifact = json.loads(base64.b64decode(os.environ["EVALNOISE_ARTIFACT_B64"], validate=True))
n = int(os.environ["EVALNOISE_SEED"]) % 20
expected = n * (n + 1) * (2 * n + 1) // 6
payload = artifact["payload"]
passed = (isinstance(payload, dict) and set(payload) == {"sum_of_squares"}
          and type(payload["sum_of_squares"]) is int and payload["sum_of_squares"] == expected)
print(json.dumps({"evalnoise_verdict": {"version": 1, "passed": passed,
      "reason": "Matches the independent closed-form answer" if passed else "Incorrect sum or payload schema"}}))
