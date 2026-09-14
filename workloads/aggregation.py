"""Reviewed synthetic data-processing fixtures, not model-generated solutions."""

import hashlib
import json
import os
import sys

ROWS = 200_000


def aggregate(mode, seed, rows=ROWS):
    offset = seed % 31
    if mode == "batch":
        records = [{"key": str(i).zfill(12), "value": i + offset,
                    "description": f"record-{i:012d}-" + "x" * 160}
                   for i in range(1, rows + 1)]
        records.sort(key=lambda item: item["key"], reverse=True)
        total = sum(item["value"] ** 2 for item in records)
    elif mode in {"stream", "cpu", "wrong"}:
        total = sum((i + offset) ** 2 for i in range(1, rows + 1))
        if mode == "cpu":
            value = b"evalnoise-aggregation"
            for _ in range(3_000_000):
                value = hashlib.sha256(value).digest()
        if mode == "wrong":
            total += 1
    else:
        raise ValueError("Unknown aggregation fixture")
    return {"rows": rows, "sum_of_squares": total}


if __name__ == "__main__":
    print(json.dumps({"evalnoise_artifact": {"version": 1,
          "payload": aggregate(sys.argv[1], int(os.environ["EVALNOISE_SEED"]))}}))
