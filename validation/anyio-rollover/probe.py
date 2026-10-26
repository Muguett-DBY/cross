"""Verify the synchronized AnyIO collaboration on disposable runners only."""
import hashlib
import json
import os
import subprocess
import sys
import time
import tomllib
import venv
import xml.etree.ElementTree as ET
from pathlib import Path

assert os.environ.get("GITHUB_ACTIONS") == "true", "Disposable runner only."
MODE = sys.argv[1]
assert MODE in ("runtime", "precommit")
BASE = "28f77c102b3b131db469d17be90020030f84f06f"
UNFIXED = "cd21b36e95fb0606bd1c78771616a48d175dfb1b"
EXPECTED_TESTS_SHA = "815fe3f75c516d03c9a4c63a3fe8fdf0d06fea1209cb330e3dd0589a79d70b4b"
ROOT = Path.cwd()
WORK = ROOT / "work"
SRC = WORK / "anyio"
OUT = ROOT / "reports"
WORK.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
           PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PIP_NO_CACHE_DIR="1",
           PRE_COMMIT_HOME=str(WORK / "pre-commit-cache"))
report = {"mode": MODE, "authorHead": BASE, "unfixedSource": UNFIXED,
          "python": sys.version, "platform": sys.platform, "commands": {},
          "allChecksPassed": False, "newLocalDependenciesInstalled": False}


def save():
    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def run(name, args, cwd=SRC, expected=(0,), timeout=600):
    if args[0] == "git":
        args = ["git", "-c", "core.autocrlf=false", *args[1:]]
    log = OUT / (name + ".log")
    started = time.monotonic()
    try:
        with log.open("wb") as stream:
            result = subprocess.run(args, cwd=cwd, env=ENV, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        report["commands"][name] = {"args": args, "exitCode": 124,
                                    "expected": list(expected), "timedOut": True}
        save()
        raise
    output = log.read_bytes()
    report["commands"][name] = {"args": args, "exitCode": result.returncode,
                                "expected": list(expected),
                                "seconds": round(time.monotonic() - started, 3)}
    save()
    print(name, result.returncode, flush=True)
    assert result.returncode in expected, (name, output.decode(errors="replace")[-4000:])
    return output


def junit(path):
    root = ET.parse(path)
    result = {field: sum(int(s.get(field, "0")) for s in root.iter("testsuite"))
              for field in ("tests", "failures", "errors", "skipped")}
    result["failureNames"] = [c.get("name") for c in root.iter("testcase")
                              if c.find("failure") is not None or c.find("error") is not None]
    result["caseNames"] = [c.get("name") for c in root.iter("testcase")]
    return result


run("clone", ["git", "clone", "--depth=1",
             "https://github.com/Fire162/anyio.git", str(SRC)], cwd=ROOT)
run("fetch-author", ["git", "fetch", "--depth=1", "origin", BASE])
run("checkout-author", ["git", "checkout", "--detach", BASE])
assert run("author-readback", ["git", "rev-parse", "HEAD"]).decode().strip() == BASE
original_tests = (SRC / "tests/test_tempfile.py").read_text(encoding="utf-8")
patched = (ROOT / "validation/anyio-rollover/test_tempfile.py").read_text(encoding="utf-8")
assert hashlib.sha256(patched.encode()).hexdigest() == EXPECTED_TESTS_SHA
(SRC / "tests/test_tempfile.py").write_text(patched, encoding="utf-8", newline="\n")
(OUT / "test_tempfile.py").write_text(patched, encoding="utf-8", newline="\n")
report["testsSha256"] = EXPECTED_TESTS_SHA
run("initial-diff-check", ["git", "diff", "--check"])
assert run("initial-scope", ["git", "diff", "--name-only"]).decode().splitlines() == [
    "tests/test_tempfile.py"
]

folder = WORK / "venv"
venv.EnvBuilder(with_pip=True).create(folder)
python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

if MODE == "precommit":
    run("install-precommit", [str(python), "-m", "pip", "install",
                             "--disable-pip-version-check", "--no-cache-dir",
                             "pre-commit==4.7.0"])
    run("precommit-all", [str(python), "-m", "pre_commit", "run", "--all-files"],
        timeout=900)
    assert (SRC / "tests/test_tempfile.py").read_text(encoding="utf-8") == patched
    run("precommit-version", [str(python), "-m", "pre_commit", "--version"])
    report["preCommitAllPassed"] = True
else:
    project = tomllib.loads((SRC / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = project["dependency-groups"]["test"]
    assert all(isinstance(item, str) for item in requirements)
    run("install-tests", [str(python), "-m", "pip", "install",
                         "--disable-pip-version-check", "--no-cache-dir",
                         "-e", ".[trio]", *requirements, "pytest==9.1.1"])
    run("installed-versions", [str(python), "-m", "pip", "freeze"])
    report["dependencyGroup"] = requirements
    ENV["PYTHONPATH"] = str(SRC / "src")
    provenance = run("import-provenance", [str(python), "-B", "-c",
        "import anyio,json,sys; from pathlib import Path; "
        "assert Path(anyio.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve()); "
        "print(json.dumps({'package':anyio.__file__,'python':sys.version}))", str(SRC)])
    report["provenance"] = json.loads(provenance)

    def pytest(name, selectors, expected=(0,)):
        xml = OUT / (name + ".xml")
        run(name, [str(python), "-B", "-m", "pytest", "-q",
                   "--basetemp=" + str(WORK / "pytest" / name),
                   "--junitxml=" + str(xml), *selectors], expected=expected)
        return junit(xml)

    full = pytest("tempfile-full", ["tests/test_tempfile.py"])
    assert full["tests"] >= 100 and full["failures"] == full["errors"] == 0
    report["tempfileSuite"] = full
    selectors = ["tests/test_tempfile.py::TestSpooledTemporaryFile::" + name for name in (
        "test_rollover_failure_retains_text",
        "test_text_rollover_preserves_newlines",
        "test_rollover_copy_error_closes_file")]
    source_path = SRC / "src/anyio/_core/_tempfile.py"
    original_source = source_path.read_bytes()
    source_text = source_path.read_text(encoding="utf-8")
    report["authorSourceSha256"] = hashlib.sha256(original_source).hexdigest()
    run("fetch-unfixed", ["git", "fetch", "--depth=1",
                         "https://github.com/agronholm/anyio.git", UNFIXED])
    unfixed = run("read-unfixed", ["git", "show",
                                  UNFIXED + ":src/anyio/_core/_tempfile.py"])
    try:
        source_path.write_bytes(unfixed)
        before = pytest("unfixed-controls", selectors, expected=(1,))
        assert before["tests"] == before["failures"] == 16
        assert before["errors"] == before["skipped"] == 0
        report["unfixedControls"] = before

        close_line = "                    newfile.close()\n"
        assert source_text.count(close_line) == 1
        source_path.write_text(source_text.replace(close_line, "                    pass\n"),
                               encoding="utf-8", newline="\n")
        no_close = pytest("missing-close-controls", selectors, expected=(1,))
        assert no_close["tests"] == 16 and no_close["failures"] == 8
        assert no_close["errors"] == no_close["skipped"] == 0
        assert all("copy_error_closes_file" in name for name in no_close["failureNames"])
        report["missingCloseControls"] = no_close

        copy_line = "                        newfile.buffer.write(buffer.buffer.getvalue())"
        assert source_text.count(copy_line) == 1
        retranscoded = source_text.replace(copy_line,
            "                        buffer.seek(0)\n"
            "                        newfile.write(buffer.read())")
        source_path.write_text(retranscoded, encoding="utf-8", newline="\n")
        translated = pytest("newline-controls", selectors, expected=(1,))
        assert translated["tests"] == 16 and translated["failures"] == 4
        assert translated["errors"] == translated["skipped"] == 0
        assert all("text_rollover_preserves_newlines" in name
                   for name in translated["failureNames"])
        report["newlineControls"] = translated
    finally:
        source_path.write_bytes(original_source)

    restored = pytest("restored-controls", selectors)
    assert restored["tests"] == 16 and restored["failures"] == restored["errors"] == 0
    assert restored["skipped"] == 0
    report["restoredControls"] = restored
    assert source_path.read_bytes() == original_source
    report["authorSourceRestoredByteForByte"] = True
    report["backends"] = ["asyncio", "asyncio+" + ("winloop" if os.name == "nt" else "uvloop"),
                          "asyncio+eager", "trio"]

run("final-diff-check", ["git", "diff", "--check"])
assert run("final-scope", ["git", "diff", "--name-only"]).decode().splitlines() == [
    "tests/test_tempfile.py"
]
assert (SRC / "tests/test_tempfile.py").read_text(encoding="utf-8") == patched
report["diff"] = run("final-diff", ["git", "diff", "--", "tests/test_tempfile.py"]).decode()
report["allChecksPassed"] = True
report["scope"] = "Test-only collaboration; no library, changelog or documentation changes."
save()
print(json.dumps({"mode": MODE, "allChecksPassed": True,
                  "tests": report.get("tempfileSuite", {}).get("tests")}))
