"""Verify the requested puzzle regressions on disposable GitHub runners."""
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

ROOT = Path.cwd()
SOURCE = ROOT / "source"
FIXTURES = ROOT / "validation/textual5762"
OUT = ROOT / "reports" / platform.system().lower()
OUT.mkdir(parents=True, exist_ok=True)
META = json.loads((FIXTURES / "source.json").read_text(encoding="utf-8"))
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
GAME = SOURCE / "src/textual/demo/game.py"
report = {"python": sys.version, "platform": platform.platform(), "source": META, "phases": {}}


def save() -> None:
    """Keep partial results even when a later assertion fails."""
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def command(args: list[str]) -> subprocess.CompletedProcess:
    """Run a checked subprocess inside the source checkout."""
    result = subprocess.run(args, cwd=SOURCE, env=ENV, capture_output=True,
                            text=True, encoding="utf-8")
    if result.returncode:
        (OUT / "command-failure.log").write_text(
            repr(args) + "\n" + result.stdout + result.stderr, encoding="utf-8"
        )
        print(result.stdout + result.stderr, flush=True)
        raise RuntimeError("A validation command failed; inspect command-failure.log")
    return result


def package_identity() -> dict:
    """Confirm pytest will import the editable source rather than another wheel."""
    code = (
        "import json, textual; from pathlib import Path; "
        "assert Path(textual.__file__).resolve() == Path('src/textual/__init__.py').resolve(); "
        "print(json.dumps({'file': textual.__file__, 'version': textual.__version__}))"
    )
    return json.loads(command([sys.executable, "-B", "-X", "utf8", "-c", code]).stdout)


def functions(text: str) -> dict[str, str]:
    """Fingerprint the production methods independently of line numbers."""
    result = {}
    for node in ast.parse(text).body:
        if isinstance(node, ast.ClassDef):
            for member in node.body:
                key = f"{node.name}.{getattr(member, 'name', '')}"
                if key in META["proposedFunctionHashes"]:
                    result[key] = hashlib.sha256(ast.dump(member).encode()).hexdigest()
    return result


def test(
    name: str,
    expected_failures: int,
    *,
    selectors: list[str] | None = None,
    expected_tests: int = 6,
) -> None:
    """Execute the same six real app tests in every phase."""
    xml = OUT / f"{name}.xml"
    args = [sys.executable, "-B", "-X", "utf8", "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-c", str(SOURCE / "pyproject.toml"), f"--junitxml={xml}",
            *(selectors if selectors is not None else [str(FIXTURES / "test_demo_game.py")])]
    result = subprocess.run(args, cwd=SOURCE, env=ENV, capture_output=True,
                            text=True, encoding="utf-8", timeout=180)
    (OUT / f"{name}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    suites = list(ET.parse(xml).getroot().iter("testsuite"))
    counts = {k: sum(int(s.get(k, "0")) for s in suites)
              for k in ("tests", "failures", "errors", "skipped")}
    report["phases"][name] = dict(returncode=result.returncode, **counts)
    save()
    print(json.dumps({"phase": name, **report["phases"][name]}), flush=True)
    assert counts == {"tests": expected_tests, "failures": expected_failures, "errors": 0, "skipped": 0}
    assert result.returncode == (1 if expected_failures else 0)


assert command(["git", "rev-parse", "HEAD"]).stdout.strip() == META["main"]
original = GAME.read_bytes()
report["mainPackage"] = package_identity()
test("current-main", 6)
try:
    command(["git", "apply", "--check", str(FIXTURES / "proposal.patch")])
    command(["git", "apply", str(FIXTURES / "proposal.patch")])
    assert functions(GAME.read_text(encoding="utf-8")) == META["proposedFunctionHashes"]
    report["patchedMethodsMatchOriginalPR"] = True
    test("current-main-with-pr-diff", 0)
finally:
    GAME.write_bytes(original)
    assert GAME.read_bytes() == original
command(["git", "fetch", "origin", META["head"]])
command(["git", "checkout", "--detach", META["head"]])
assert command(["git", "rev-parse", "HEAD"]).stdout.strip() == META["head"]
installation = command([sys.executable, "-m", "pip", "install", "-e", "."])
(OUT / "original-head-install.log").write_text(installation.stdout + installation.stderr, encoding="utf-8")
report["originalHeadPackage"] = package_identity()
assert functions(GAME.read_text(encoding="utf-8")) == META["proposedFunctionHashes"]
test("original-pr-head", 0)
contribution = SOURCE / "tests/test_demo_game.py"
assert not contribution.exists()
contribution.write_bytes((FIXTURES / "test_demo_game.py").read_bytes())
test("original-pr-in-tree", 0,
     selectors=["tests/test_demo_game.py", "tests/test_demo.py"], expected_tests=7)
report["readyContributionPath"] = "tests/test_demo_game.py"
style = command([sys.executable, "-m", "black", "--check", "--diff", str(FIXTURES / "test_demo_game.py")])
(OUT / "black.log").write_text(style.stdout + style.stderr, encoding="utf-8")
report["allChecksPassed"] = True
report["testFileSha256"] = hashlib.sha256((FIXTURES / "test_demo_game.py").read_bytes()).hexdigest()
assert contribution.read_bytes() == (FIXTURES / "test_demo_game.py").read_bytes()
save()
print(json.dumps({"allChecksPassed": True, "phases": report["phases"]}), flush=True)
