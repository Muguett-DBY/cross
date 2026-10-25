"""Compare workspace-wide and subset analysis of editable typed packages."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path.cwd()
WORK = ROOT / "work" / "pyright11109"
PROJECT = WORK / "workspace"
OUT = ROOT / "reports" / platform.system().lower()
WORK.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
PROJECT.mkdir(exist_ok=True)
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
           PIP_CACHE_DIR=str(WORK / "pip-cache"), UV_CACHE_DIR=str(WORK / "uv-cache"),
           UV_PYTHON_DOWNLOADS="never", npm_config_cache=str(WORK / "npm-cache"))
report = {"python": sys.version, "platform": platform.platform(), "commands": {}, "checks": {}}


def save() -> None:
    """Preserve partial evidence before asserting expected diagnostic counts."""
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run(name: str, args: list[str], *, cwd: Path = PROJECT, expect=0):
    """Capture subprocess output with a bounded runtime."""
    result = subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, timeout=300)
    text = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = "".join(char for char in text if char in "\n\r\t" or ord(char) >= 32)
    (OUT / f"{name}.log").write_text(text, encoding="utf-8")
    report["commands"][name] = {"args": args, "returncode": result.returncode}
    save()
    if expect is not None:
        assert result.returncode == expect, (name, result.returncode, text[-6000:])
    return result


def put(path: Path, contents: str) -> None:
    """Create only explicitly named fixture files inside the runner workspace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def check(name: str, targets: list[str], expected: int, *, version: str, entry: Path) -> dict:
    """Verify real CLI diagnostics without suppressing the privacy rule."""
    result = run(name, ["node", str(entry), "--outputjson", *targets], expect=None)
    actual = json.loads(result.stdout)
    report["checks"][name] = actual
    save()
    assert actual["version"] == version
    assert actual["summary"]["errorCount"] == expected, actual
    assert actual["summary"]["warningCount"] == 0
    assert result.returncode == (1 if expected else 0)
    errors = [diag for diag in actual["generalDiagnostics"] if diag["severity"] == "error"]
    assert len(errors) == expected
    if expected:
        assert all(diag.get("rule") == "reportPrivateImportUsage" for diag in errors)
        assert all(diag["file"].replace("\\", "/").endswith("packageC/moo.py") for diag in errors)
    return actual


node_tools = WORK / "node-tools"
put(node_tools / "package.json", json.dumps({"private": True, "devDependencies": {
    "pyright": "1.1.414", "pyright-legacy": "npm:pyright@1.1.407"}}))
npm = "npm.cmd" if os.name == "nt" else "npm"
run("install-pyright", [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=node_tools)
CHECKERS = {"1.1.407": node_tools / "node_modules/pyright-legacy/index.js",
            "1.1.414": node_tools / "node_modules/pyright/index.js"}
assert all(path.is_file() for path in CHECKERS.values())
tools = WORK / "tools"
venv.EnvBuilder(with_pip=True).create(tools)
tool_python = tools / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
run("install-uv", [str(tool_python), "-m", "pip", "install", "uv==0.13.5"], cwd=WORK)
run("uv-version", [str(tool_python), "-m", "uv", "--version"], cwd=WORK)
put(PROJECT / "pyproject.toml", '''[project]
name = "workspace-import-probe"
version = "0.0.0"
requires-python = ">=3.12,<3.13"
dependencies = ["packagea", "packageb", "packagec"]
[tool.uv]
package = false
[tool.uv.workspace]
members = ["packages/*"]
[tool.uv.sources]
packagea = { workspace = true }
packageb = { workspace = true }
packagec = { workspace = true }
''')
for letter, dependencies in (("A", []), ("B", ["packagea"]), ("C", ["packageb"])):
    package = "package" + letter
    put(PROJECT / "packages" / package / "pyproject.toml", f'''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[project]
name = "{package.lower()}"
version = "1.0.0"
requires-python = ">=3.12,<3.13"
dependencies = {json.dumps(dependencies)}
[tool.hatch.build.targets.wheel]
packages = ["src/{package}"]
''')
    folder = PROJECT / "packages" / package / "src" / package
    put(folder / "__init__.py", "")
    put(folder / "py.typed", "")
put(PROJECT / "packages/packageA/src/packageA/foo.py", "class myclass:\n    pass\n")
BAR = PROJECT / "packages/packageB/src/packageB/bar.py"
put(BAR, "from packageA.foo import myclass\n")
put(PROJECT / "packages/packageC/src/packageC/moo.py", "from packageB.bar import myclass\n\nvalue = myclass()\n")
configuration = {"include": ["packages"], "venvPath": ".", "venv": ".venv",
                 "typeCheckingMode": "basic", "reportPrivateImportUsage": "error"}
put(PROJECT / "pyrightconfig.json", json.dumps(configuration, indent=2) + "\n")
run("uv-sync-editable-workspace", [str(tool_python), "-m", "uv", "sync", "--python", sys.executable])
python = PROJECT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
runtime = run("runtime-import", [str(python), "-c",
    "import json, packageB.bar, packageC.moo; "
    "assert isinstance(packageC.moo.value, packageB.bar.myclass); "
    "print(json.dumps({'runtimeImportWorks':True, 'packageBFile':packageB.bar.__file__}))"])
report["runtime"] = json.loads(runtime.stdout)
for version, entry in CHECKERS.items():
    put(BAR, "from packageA.foo import myclass\n")
    check(version + "-implicit-export-whole-workspace", [], 0 if version == "1.1.407" else 1,
          version=version, entry=entry)
    check(version + "-implicit-export-subset", ["packages/packageC"], 1, version=version, entry=entry)

    put(BAR, "from packageA.foo import myclass as myclass\n")
    check(version + "-redundant-alias-whole-workspace", [], 0, version=version, entry=entry)
    check(version + "-redundant-alias-subset", ["packages/packageC"], 0, version=version, entry=entry)

    put(BAR, 'from packageA.foo import myclass\n\n__all__ = ["myclass"]\n')
    check(version + "-explicit-all-whole-workspace", [], 0, version=version, entry=entry)
    check(version + "-explicit-all-subset", ["packages/packageC"], 0, version=version, entry=entry)
report["configuration"] = configuration
report["allChecksPassed"] = True
report["originalPrivateWorkspaceTested"] = False
report["scope"] = "Actual uv workspace with three editable typed src-layout packages; Pyright 1.1.407 and 1.1.414."
report["reporterVersionKnown"] = False
save()
print(json.dumps({"allChecksPassed": True,
    "counts": {name: value["summary"]["errorCount"] for name, value in report["checks"].items()}}))
