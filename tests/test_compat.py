"""Language-level compatibility with the oldest supported Python.

`pyproject.toml` declares `requires-python = ">=3.11"` and CI runs 3.11 through 3.14, but
day-to-day development happens on a newer interpreter. `ast.parse(feature_version=(3, 11))`
does **not** catch PEP 701 f-strings: the 3.12+ tokenizer accepts a nested same-quote
f-string regardless of `feature_version`, so that check is a false negative and is
deliberately not used here. This module tokenizes instead.
"""

import io
from pathlib import Path
import shutil
import subprocess
import token
import tokenize
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("evalnoise", "tools", "scripts", "probes", "workloads", "verifiers", "tests")
MINIMUM = (3, 11)


def sources():
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def pep701_usages(source):
    """Report f-string replacement fields that Python 3.11 cannot parse.

    Two constructs were legalised by PEP 701 on 3.12 and are a SyntaxError on 3.11: a
    nested string reusing the enclosing quote, and any backslash inside a replacement
    field. Both have already reached this repository, so both are checked.

    On 3.11 the tokenizer emits one STRING token per f-string, so there is nothing to walk
    and the file's own compilation is the check instead.
    """
    start = getattr(token, "FSTRING_START", None)
    end = getattr(token, "FSTRING_END", None)
    if start is None or end is None:
        return []
    middle = token.FSTRING_MIDDLE
    found, quotes = [], []
    for item in tokenize.generate_tokens(io.StringIO(source).readline):
        if item.type == start:
            quotes.append(item.string[-1])
        elif item.type == end:
            if quotes:
                quotes.pop()
        elif not quotes or item.type in (middle, tokenize.NL, tokenize.NEWLINE):
            continue
        elif item.type == token.STRING and quotes[-1] in item.string[:3]:
            found.append((item.start[0], f"nested {quotes[-1]!r} quote: {item.string}"))
        elif "\\" in item.string:
            found.append((item.start[0], f"backslash in replacement field: {item.string}"))
    return found


class SyntaxCompatibilityTests(unittest.TestCase):
    def test_no_source_uses_pep_701_syntax(self):
        offenders = []
        for path in sources():
            for line, text in pep701_usages(path.read_text()):
                offenders.append(f"{path.relative_to(ROOT)}:{line}: {text}")
        version = ".".join(str(part) for part in MINIMUM)
        self.assertEqual(offenders, [],
                         f"PEP 701 syntax is a SyntaxError on Python {version}: "
                         + "; ".join(offenders))

    def test_the_detector_actually_detects(self):
        if getattr(token, "FSTRING_START", None) is None:
            self.skipTest("this interpreter predates PEP 701 tokenization")
        self.assertTrue(pep701_usages('x = f"{a["k"]}"\n'))
        self.assertTrue(pep701_usages('y = f"{a or \'<td x=\\"1\\">\'}"\n'))
        self.assertEqual(pep701_usages("x = f\"{a['k']}\"\n"), [])
        self.assertEqual(pep701_usages('x = f"{a}"\n'), [])
        self.assertEqual(pep701_usages('x = f"a\\nb {c}"\n'), [])

    def test_every_source_file_compiles(self):
        for path in sources():
            with self.subTest(path=str(path.relative_to(ROOT))):
                compile(path.read_text(), str(path), "exec")


@unittest.skipUnless(shutil.which("python3.11"), "no python3.11 interpreter on PATH")
class RealInterpreterTests(unittest.TestCase):
    def test_python311_compiles_every_package(self):
        result = subprocess.run(
            ["python3.11", "-m", "compileall", "-q", *(str(ROOT / p) for p in PACKAGES)],
            capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
