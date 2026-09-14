"""Independent closed-form output verifier; never imports candidate code."""

import base64
import json
import os

ROWS = 200_000


def accepts(payload, seed, rows=ROWS):
    offset = seed % 31
    expected = (rows * (rows + 1) * (2 * rows + 1) // 6
                + offset * rows * (rows + 1) + rows * offset * offset)
    return (isinstance(payload, dict) and set(payload) == {"rows", "sum_of_squares"}
            and type(payload["rows"]) is int and payload["rows"] == rows
            and type(payload["sum_of_squares"]) is int
            and payload["sum_of_squares"] == expected)


if __name__ == "__main__":
    artifact = json.loads(base64.b64decode(os.environ["EVALNOISE_ARTIFACT_B64"], validate=True))
    passed = accepts(artifact["payload"], int(os.environ["EVALNOISE_SEED"]))
    print(json.dumps({"evalnoise_verdict": {"version": 1, "passed": passed,
          "reason": "Matches independent row count and closed-form sum" if passed
          else "Row count, sum or output schema is incorrect"}}))
