"""Independent before/after checks for PrettyTable PR 504, on a disposable runner."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HEAD = "ea8d74e2a48acd6a8fdeb08fa356d3971953d8f4"
BASE = "2a6cd4fb41bc6754eac57b43fc6dbd43b08ae368"
SOURCE = Path.cwd() / "source"
OUT = Path.cwd() / "reports" / platform.system().lower()
OUT.mkdir(parents=True, exist_ok=True)
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(SOURCE), *args])


def methods(source: str) -> set[str]:
    """Read test names without executing the test module."""
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "TestSlicing")
    return {n.name for n in node.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}


def pytest(name: str, selectors: list[str]) -> dict:
    """Run real pytest assertions and retain the complete XML and output."""
    report = OUT / f"{name}.xml"
    command = [sys.executable, "-B", "-X", "utf8", "-m", "pytest", "-q",
               "-p", "no:cacheprovider", f"--junitxml={report}", *selectors]
    result = subprocess.run(command, cwd=SOURCE, env=ENV, capture_output=True,
                            text=True, encoding="utf-8", timeout=240)
    (OUT / f"{name}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    suites = list(ET.parse(report).getroot().iter("testsuite"))
    counts = {key: sum(int(s.get(key, "0")) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    record = dict(returncode=result.returncode, command=command, **counts)
    print(json.dumps({"phase": name, **record}), flush=True)
    return record


assert git("rev-parse", "HEAD").decode().strip() == HEAD
changed = git("diff", "--name-only", BASE, HEAD).decode().splitlines()
assert sorted(changed) == ["src/prettytable/prettytable.py", "tests/test_prettytable.py"]
test_path = "tests/test_prettytable.py"
new_methods = methods((SOURCE / test_path).read_text(encoding="utf-8")) - methods(git("show", f"{BASE}:{test_path}").decode("utf-8"))
assert len(new_methods) == 7
selectors = [f"{test_path}::TestSlicing::{name}" for name in sorted(new_methods)]
implementation = SOURCE / "src/prettytable/prettytable.py"
original = implementation.read_bytes()
baseline = git("show", f"{BASE}:src/prettytable/prettytable.py")
report = {
    "python": sys.version, "platform": platform.platform(), "head": HEAD, "base": BASE,
    "changedFiles": changed, "newTestMethods": sorted(new_methods),
    "headImplementationSha256": hashlib.sha256(original).hexdigest(),
    "baseImplementationSha256": hashlib.sha256(baseline).hexdigest(),
    "baselineScope": "PR tests against the original base implementation; all other files are unchanged between base and head",
}
try:
    implementation.write_bytes(baseline)
    report["baseline"] = pytest("baseline-added-tests", selectors)
finally:
    implementation.write_bytes(original)
    assert implementation.read_bytes() == original
assert report["baseline"]["returncode"] == 1
assert {k: report["baseline"][k] for k in ("tests", "failures", "errors", "skipped", "passed")} == {
    "tests": 18, "failures": 12, "errors": 0, "skipped": 0, "passed": 6}
report["fixed"] = pytest("head-added-tests", selectors)
assert report["fixed"]["returncode"] == 0 and report["fixed"]["passed"] == 18
report["full"] = pytest("head-full-suite", ["tests"])
assert report["full"]["returncode"] == 0
assert report["full"]["tests"] >= 356
assert report["full"]["errors"] == report["full"]["failures"] == 0

extra = r'''
import json
from pathlib import Path
import prettytable
from prettytable import PrettyTable

assert Path(prettytable.__file__).resolve() == Path("src/prettytable/__init__.py").resolve()
results = []
for label, index in [("negative", -1), ("reverse", slice(None, None, -1)),
                     ("stepped", slice(2, 5, 2)), ("empty", slice(0, 0))]:
    source = PrettyTable(["name", "count"])
    source.add_rows([[f"item-{n}", n] for n in range(6)])
    source.align["name"] = "l"
    source.attributes = {"class": "original"}
    source.fields = ["name", "count"]
    expected = source.get_string()
    subset = source[index]
    subset.align["name"] = "r"
    subset.attributes["class"] = "copy"
    subset.fields.reverse()
    assert source.align["name"] == "l"
    assert source.attributes == {"class": "original"}
    assert source.fields == ["name", "count"]
    # An empty selection displays all columns without retaining the old names.
    subset.fields = []
    subset.field_names = ["renamed", "count"]
    assert source.get_string() == expected
    subset.get_string()
    results.append({"indexForm": label, "passed": True})
print(json.dumps({"packageFile": prettytable.__file__, "version": prettytable.__version__, "cases": results}))
'''
result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", extra], cwd=SOURCE,
                        env=ENV, capture_output=True, text=True, encoding="utf-8")
(OUT / "extra-index-forms.log").write_text(result.stdout + result.stderr, encoding="utf-8")
if result.returncode:
    report["extraIndexFormsError"] = result.stderr
    report["allChecksPassed"] = False
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(result.stderr, flush=True)
    raise AssertionError("The additional index-form checks failed; inspect extra-index-forms.log")
report["extraIndexForms"] = json.loads(result.stdout)
assert implementation.read_bytes() == original
report["sourceRestoredExactly"] = True
report["allChecksPassed"] = True
(OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"allChecksPassed": True, "full": report["full"],
                  "extraIndexForms": report["extraIndexForms"]}), flush=True)
