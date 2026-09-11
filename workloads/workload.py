"""Known-answer calibration workloads. These are deliberately NOT AI agents."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main():
    mode = sys.argv[1]
    started = time.monotonic()
    print(json.dumps({"phase": "start", "workload": mode, "seed": os.environ.get("EVALNOISE_SEED")}))
    if mode == "cpu":
        data = b"evalnoise-calibration" * 1024
        expected = hashlib.sha256(data).hexdigest()
        for _ in range(15000):
            assert hashlib.sha256(data).hexdigest() == expected
    elif mode == "cpu-long":
        # Fixed iteration count, not a sleep: roughly 3.7 s of real hashing on one CPU.
        # The digest is a deterministic known answer, so a truncated run cannot pass.
        digest = hashlib.sha256(b"evalnoise-cpu-long").digest()
        for _ in range(12_000_000):
            digest = hashlib.sha256(digest).digest()
        expected = "75b6ae6d175af27e5b399c236f40547e53dd40643028aadd4ebc784e66587d7d"
        if digest.hex() != expected:
            raise ValueError(f"cpu-long produced {digest.hex()}, not the known answer")
    elif mode == "memory":
        blocks = []
        for _ in range(96):
            blocks.append(bytearray(b"x" * (1024 * 1024)))
        assert sum(len(block) for block in blocks) == 96 * 1024 * 1024
        assert all(block[0] == 120 and block[-1] == 120 for block in blocks)
        time.sleep(.3)
    elif mode == "repository":
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "maths.py").write_text("def total(items):\n    return sum(items)\n")
            (root / "test_maths.py").write_text(
                "import unittest\nfrom maths import total\n"
                "class TestTotal(unittest.TestCase):\n"
                "    def test_empty(self): self.assertEqual(total([]), 0)\n"
                "    def test_signed(self): self.assertEqual(total([-2, 5]), 3)\n")
            subprocess.run([sys.executable, "-m", "unittest", "discover", "-v"], cwd=root, check=True)
    elif mode == "timeout":
        time.sleep(30)
    elif mode == "failure":
        print("Intentional nonzero calibration outcome", file=sys.stderr)
        return 7
    elif mode == "exit137":
        return 137
    else:
        raise ValueError(f"Unknown workload: {mode}")
    print(json.dumps({"phase": "verified", "workload": mode, "elapsed_s": time.monotonic() - started}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
