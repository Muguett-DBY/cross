"""Verify the join retention fix on isolated native and PyPy CI runners."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import venv
import xml.etree.ElementTree as ET
from pathlib import Path

assert os.environ.get("GITHUB_ACTIONS") == "true", "Disposable-runner execution only."
BASE = "a876c7e1cf11e7c746e91fce4632ebfa93865428"
ROOT = Path.cwd()
WORK = ROOT / "work"
OUT = ROOT / "reports"
SRC = WORK / "funcy"
WORK.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
           PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PIP_CACHE_DIR=str(WORK / "pip-cache"))
report = {"base": BASE, "python": sys.version, "platform": platform.platform(),
          "implementation": platform.python_implementation(), "commands": {}, "filesSha256": {}}


def save() -> None:
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run(name: str, args: list[str], cwd: Path = WORK, expected: tuple[int, ...] = (0,)) -> bytes:
    result = subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, timeout=240)
    (OUT / (name + ".log")).write_bytes(result.stdout + result.stderr)
    report["commands"][name] = {"args": args, "returncode": result.returncode,
                                "expectedReturncodes": list(expected)}
    save()
    assert result.returncode in expected, (name, result.returncode,
                                           result.stderr.decode(errors="replace")[-2500:])
    return result.stdout


def junit(path: Path) -> dict:
    root = ET.parse(path)
    value = {field: sum(int(s.get(field, "0")) for s in root.iter("testsuite"))
             for field in ["tests", "failures", "errors", "skipped"]}
    value["failureNames"] = [c.get("name") for c in root.iter("testcase")
                             if c.find("failure") is not None or c.find("error") is not None]
    return value


run("clone", ["git", "clone", "--depth=1", "https://github.com/Suor/funcy.git", str(SRC)])
if run("current-head", ["git", "-C", str(SRC), "rev-parse", "HEAD"]).decode().strip() != BASE:
    run("fetch-base", ["git", "-C", str(SRC), "fetch", "--depth=1", "origin", BASE])
    run("checkout-base", ["git", "-C", str(SRC), "checkout", "--detach", BASE])
assert run("base-readback", ["git", "-C", str(SRC), "rev-parse", "HEAD"]).decode().strip() == BASE
original = (SRC / "funcy/colls.py").read_bytes()
for name, target in [("colls.py", "funcy/colls.py"), ("test_colls.py", "tests/test_colls.py")]:
    content = (ROOT / "validation/funcy-join" / name).read_bytes()
    (SRC / target).write_bytes(content)
    (OUT / name).write_bytes(content)
    report["filesSha256"][target] = hashlib.sha256(content).hexdigest()

folder = WORK / "venv"
venv.EnvBuilder(with_pip=True).create(folder)
python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
run("install", [str(python), "-m", "pip", "install", "--no-cache-dir",
                "pytest==7.4.3", "whatever==0.7", "flake8==7.3.0"])
ENV["PYTHONPATH"] = str(SRC)
provenance = (
    "import sys,json,platform,funcy; from pathlib import Path; "
    "assert Path(funcy.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve()); "
    "print(json.dumps({'python':sys.version,'implementation':platform.python_implementation(),"
    "'packagePath':funcy.__file__}))"
)
report["provenance"] = json.loads(run("provenance", [str(python), "-B", "-c", provenance, str(SRC)]))
run("installed-versions", [str(python), "-m", "pip", "freeze"])
full_path = OUT / "full-suite.xml"
run("full-suite", [str(python), "-B", "-m", "pytest", "-W", "error", "-q",
                   "--junitxml=" + str(full_path)], cwd=SRC)
report["fullSuite"] = junit(full_path)
assert report["fullSuite"]["tests"] > 230
assert report["fullSuite"]["failures"] == report["fullSuite"]["errors"] == 0
run("flake8-library", [str(python), "-m", "flake8", "funcy"], cwd=SRC)
run("flake8-tests", [str(python), "-m", "flake8", "--select=F,E5,W", "tests"], cwd=SRC)

updated = (SRC / "funcy/colls.py").read_bytes()
try:
    (SRC / "funcy/colls.py").write_bytes(original)
    before = OUT / "base-controls.xml"
    run("base-controls", [str(python), "-B", "-m", "pytest", "-W", "error", "-q",
        "tests/test_colls.py::test_join_releases_processed_mappings",
        "tests/test_colls.py::test_join_keeps_iterator_inputs_lazy", "--junitxml=" + str(before)],
        cwd=SRC, expected=(1,))
    report["baseControls"] = junit(before)
    assert report["baseControls"] == {"tests": 2, "failures": 1, "errors": 0,
                                      "skipped": 0, "failureNames": ["test_join_releases_processed_mappings"]}
finally:
    (SRC / "funcy/colls.py").write_bytes(updated)

after = OUT / "restored-controls.xml"
run("restored-controls", [str(python), "-B", "-m", "pytest", "-W", "error", "-q",
    "tests/test_colls.py::test_join_releases_processed_mappings",
    "tests/test_colls.py::test_join_keeps_iterator_inputs_lazy", "--junitxml=" + str(after)], cwd=SRC)
report["restoredControls"] = junit(after)
assert report["restoredControls"] == {"tests": 2, "failures": 0, "errors": 0,
                                      "skipped": 0, "failureNames": []}
run("diff-check", ["git", "-C", str(SRC), "diff", "--check"])
report["diff"] = run("final-diff", ["git", "-C", str(SRC), "diff"]).decode()
assert run("changed-files", ["git", "-C", str(SRC), "diff", "--name-only"]).decode().splitlines() == [
    "funcy/colls.py", "tests/test_colls.py"]
report["allChecksPassed"] = True
report["scope"] = "Complete pytest -W error and flake8 library/test checks; causal base regression and iterator-laziness control. No docs or type signature changes; type-checker matrix/build checks not repeated."
save()
print(json.dumps({"allChecksPassed": True, "suite": report["fullSuite"],
                  "implementation": report["implementation"]}))
