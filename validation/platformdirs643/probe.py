"""Check an existing platformdirs PR on disposable GitHub-hosted runners."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

assert os.environ.get("GITHUB_ACTIONS") == "true", "This probe runs only on disposable Actions runners."
ROOT = Path.cwd()
SOURCE = ROOT / "source"
OUT = ROOT / "reports" / platform.system().lower()
OUT.mkdir(parents=True, exist_ok=True)
BASE = "6fe448ccf1837798f0adc3a577c61ee70c7d5c58"
HEAD = "411d04d238e91b8513a07806a557e4a4fcea09a8"
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
           PIP_CACHE_DIR=str(ROOT / "work/pip-cache"))
report = {"python": sys.version, "platform": platform.platform(), "base": BASE, "head": HEAD,
          "commands": {}, "junit": {}}


def save() -> None:
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run(name: str, args: list[str], *, expect: int | None = 0) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=SOURCE, env=ENV, capture_output=True, timeout=420)
    log = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    log = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", log)
    log = "".join(c for c in log if c in "\n\r\t" or ord(c) >= 32)
    (OUT / (name + ".log")).write_text(log, encoding="utf-8")
    report["commands"][name] = {"args": args, "returncode": result.returncode}
    save()
    if expect is not None:
        assert result.returncode == expect, (name, result.returncode, log[-5000:])
    return result


def tests(name: str, targets: list[str], *, expected_failures: set[str] | None = None) -> None:
    junit = OUT / (name + ".xml")
    result = run(name, [sys.executable, "-B", "-m", "pytest", "-q", "-ra", "--color=no",
                        "--basetemp", str(ROOT / "work" / name), "--junitxml", str(junit), *targets],
                 expect=None)
    document = ET.parse(junit)
    cases = [{"name": case.attrib["name"],
              "outcome": "error" if case.find("error") is not None else
                         "failed" if case.find("failure") is not None else
                         "skipped" if case.find("skipped") is not None else "passed"}
             for case in document.iter("testcase")]
    report["junit"][name] = cases
    save()
    assert not any(case["outcome"] == "error" for case in cases), cases
    failed = {case["name"] for case in cases if case["outcome"] == "failed"}
    if expected_failures is None:
        assert result.returncode == 0 and not failed, (name, failed)
    else:
        # A runner may lack symlink privileges. Only an explicit skip can remove that expectation.
        skipped = {case["name"] for case in cases if case["outcome"] == "skipped"}
        assert failed == expected_failures - skipped, (name, failed, skipped, expected_failures)
        assert result.returncode == 1


actual = run("head-identity", ["git", "rev-parse", "HEAD"]).stdout.decode().strip()
assert actual == HEAD
metadata = tomllib.loads((SOURCE / "pyproject.toml").read_text(encoding="utf-8"))
dependencies = metadata["dependency-groups"]["test"]
assert all(isinstance(item, str) for item in dependencies)
run("install-declared-tests", [sys.executable, "-m", "pip", "install", "-e", ".", *dependencies])
run("tool-versions", [sys.executable, "-m", "pip", "freeze"])
identity = run("import-identity", [sys.executable, "-c",
    "import json,platformdirs,platformdirs.testing; print(json.dumps({'package':platformdirs.__file__,"
    "'testing':platformdirs.testing.__file__}))"])
report["import"] = json.loads(identity.stdout)
assert Path(report["import"]["testing"]).resolve() == (SOURCE / "src/platformdirs/testing.py").resolve()
testing_file = SOURCE / "src/platformdirs/testing.py"
original = testing_file.read_bytes()
baseline = run("base-module", ["git", "show", BASE + ":src/platformdirs/testing.py"]).stdout
report["moduleSha256"] = {"head": hashlib.sha256(original).hexdigest(),
                          "base": hashlib.sha256(baseline).hexdigest()}
boundary = str(ROOT / "validation/platformdirs643/test_boundaries.py")
report["boundarySha256"] = hashlib.sha256(Path(boundary).read_bytes()).hexdigest()
expected = {"test_pin_on_entry_not_manager_creation", "test_nested_relative_exception_restores_outer",
            "test_parent_relative_pathlike_root", "test_relative_symlink_spelling_and_target"}
if os.name == "nt":
    expected.add("test_windows_drive_relative_root")
try:
    testing_file.write_bytes(baseline)
    tests("base-author-regressions", ["tests/test_testing.py", "-k", "relative_root_survives_chdir"],
          expected_failures={"test_isolated_dirs_relative_root_survives_chdir[.]",
                             "test_isolated_dirs_relative_root_survives_chdir[isolated]"})
    tests("base-boundaries", [boundary], expected_failures=expected)
finally:
    testing_file.write_bytes(original)
tests("head-full-suite", ["tests"])
tests("head-boundaries", [boundary])
run("tracked-source-unchanged", ["git", "diff", "--exit-code"])
report["allControlsPassed"] = True
report["baseControl"] = "Exact original testing.py on otherwise unchanged implementation at PR head."
report["newProductionCode"] = False
save()
print(json.dumps({"allControlsPassed": True, "junit": {name: {outcome: sum(c['outcome'] == outcome for c in cases)
       for outcome in ['passed','failed','skipped','error']} for name,cases in report['junit'].items()}}))
