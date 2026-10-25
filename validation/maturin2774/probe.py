"""Check local sibling dependencies on disposable runners, without a registry."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path.cwd()
WORK = ROOT / "work" / "maturin2774"
OUT = ROOT / "reports" / platform.system().lower()
WORK.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
PROJECT = WORK / "project"
DEP = WORK / "dep"
NAME = "custard-maturin-fixture-dep"
VERSIONS = {"maturin": "1.15.0", "uv": "0.13.5"}
report = {"python": sys.version, "platform": platform.platform(), "commands": {}}


def save() -> None:
    """Retain partial observations if a subsequent check fails."""
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run(name: str, args: list[str], *, cwd=PROJECT, env=None, expect=0):
    """Capture command output without interpreting terminal control sequences."""
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, timeout=420)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    output = "".join(c for c in output if c in "\n\r\t" or ord(c) >= 32)
    (OUT / f"{name}.log").write_text(output, encoding="utf-8")
    report["commands"][name] = {"args": args, "returncode": result.returncode}
    save()
    if expect is not None:
        assert result.returncode == expect, (name, result.returncode, output[-5000:])
    return result.returncode, output


def put(path: Path, contents: str) -> None:
    """Create a fixture file entirely inside this runner's workspace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def project_metadata(dependency: str, *, sources: bool = False) -> str:
    """Keep published requirements separate from optional tool-specific sources."""
    return f'''[build-system]
requires = ["maturin==1.15.0"]
build-backend = "maturin"

[project]
name = "custard-maturin-fixture-project"
version = "1.0.0"
requires-python = ">=3.9"
dependencies = ["{dependency}"]

[tool.maturin]
python-source = "src"
module-name = "custard_fixture_project._native"
''' + (f'''
[tool.uv.sources]
{NAME} = {{ path = "../dep", editable = true }}
''' if sources else "")


def make_env(kind: str):
    """Install tooling only in an isolated environment on the ephemeral runner."""
    folder = WORK / f"venv-{kind}"
    venv.EnvBuilder(with_pip=True).create(folder)
    scripts = folder / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    env = dict(os.environ, VIRTUAL_ENV=str(folder), PYTHONDONTWRITEBYTECODE="1",
               PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1",
               PIP_CACHE_DIR=str(WORK / "pip-cache"),
               UV_CACHE_DIR=str(WORK / "uv-cache"), UV_PYTHON_DOWNLOADS="never",
               CARGO_TARGET_DIR=str(WORK / "cargo-target"), PYO3_PYTHON=str(python))
    env["PATH"] = str(scripts) + os.pathsep + env["PATH"]
    run(f"{kind}-prepare", [str(python), "-m", "pip", "install",
        "maturin==1.15.0", "uv==0.13.5", "hatchling", "editables"], cwd=WORK, env=env)
    env.update(PIP_NO_INDEX="1", UV_NO_INDEX="1")
    _, output = run(f"{kind}-versions", [str(python), "-c",
        "import json; from importlib.metadata import version; "
        "print(json.dumps({n:version(n) for n in ('maturin','uv','hatchling','pip')}))"], env=env)
    report[f"{kind}Versions"] = json.loads(output)
    for name, version in VERSIONS.items():
        assert report[f"{kind}Versions"][name] == version
    return str(python), env


def check_import(kind: str, python: str, env: dict) -> None:
    """Require the real Rust extension and editable sibling dependency to work."""
    _, output = run(f"{kind}-import", [python, "-c",
        "import json, custard_fixture_project, localdep_probe; "
        "from importlib.metadata import requires; "
        "assert custard_fixture_project.answer() == 49; "
        "print(json.dumps({'result':custard_fixture_project.answer(),"
        "'depFile':localdep_probe.__file__,"
        "'requires':requires('custard-maturin-fixture-project')}))"], env=env)
    actual = json.loads(output)
    assert Path(actual["depFile"]).resolve() == (DEP / "src/localdep_probe/__init__.py").resolve()
    assert len(actual["requires"]) == 1
    assert re.sub(r"\s+", "", actual["requires"][0]) == NAME + ">=1.0"
    report[f"{kind}Import"] = actual
    save()


put(DEP / "pyproject.toml", f'''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[project]
name = "{NAME}"
version = "1.0.0"
[tool.hatch.build.targets.wheel]
packages = ["src/localdep_probe"]
''')
put(DEP / "src/localdep_probe/__init__.py", "VALUE = 7\n")
put(PROJECT / "Cargo.toml", '''[package]
name = "custard-maturin-fixture-project"
version = "1.0.0"
edition = "2021"
[lib]
name = "_native"
crate-type = ["cdylib"]
[dependencies]
pyo3 = { version = "=0.23.5", features = ["extension-module"] }
''')
put(PROJECT / "src/lib.rs", '''use pyo3::prelude::*;
#[pyfunction]
fn answer() -> u32 { 42 }
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(answer, m)?)?;
    Ok(())
}
''')
put(PROJECT / "src/custard_fixture_project/__init__.py", '''from ._native import answer as native_answer
from localdep_probe import VALUE
def answer():
    return native_answer() + VALUE
''')
put(PROJECT / "pyproject.toml", project_metadata(NAME + " @ {root:parent:uri}/dep"))
uv_python, uv_env = make_env("uv")
code, output = run("hatch-template-rejected", [uv_python, "-m", "maturin", "develop", "--uv"], env=uv_env, expect=None)
assert code != 0 and ("missing scheme" in output or "Not a URL" in output), output[-5000:]

put(PROJECT / "pyproject.toml", project_metadata(NAME + ">=1.0", sources=True))
code, output = run("uv-sources-alone", [uv_python, "-m", "maturin", "develop", "--uv"], env=uv_env, expect=None)
report["uvSourcesAloneWorks"] = code == 0
if code:
    assert NAME in output and ("No solution found" in output or "not found" in output), output[-5000:]
run("uv-install-sibling", [uv_python, "-m", "uv", "pip", "install", "--python", uv_python,
    "--no-build-isolation", "-e", "../dep"], env=uv_env)
run("uv-develop-after-sibling", [uv_python, "-m", "maturin", "develop", "--uv"], env=uv_env)
check_import("uv", uv_python, uv_env)

put(PROJECT / "pyproject.toml", project_metadata(NAME + ">=1.0"))
pip_python, pip_env = make_env("pip")
code, output = run("pip-before-sibling", [pip_python, "-m", "maturin", "develop"], env=pip_env, expect=None)
assert code != 0 and NAME in output and "No matching distribution" in output, output[-5000:]
run("pip-install-sibling", [pip_python, "-m", "pip", "install",
    "--no-build-isolation", "-e", "../dep"], env=pip_env)
run("pip-develop-after-sibling", [pip_python, "-m", "maturin", "develop"], env=pip_env)
check_import("pip", pip_python, pip_env)
report["fixtureHashes"] = {str(path.relative_to(WORK)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in [DEP / "pyproject.toml", PROJECT / "Cargo.toml", PROJECT / "src/lib.rs"]}
report["allRecommendedStepsPassed"] = True
report["hatchEnvironmentLifecycleTested"] = False
save()
print(json.dumps({"uvSourcesAloneWorks": report["uvSourcesAloneWorks"],
    "allRecommendedStepsPassed": True, "pipImport": report["pipImport"], "uvImport": report["uvImport"]}))
