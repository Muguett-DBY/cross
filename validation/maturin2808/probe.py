"""Reproduce registry configuration discovery in a nested workspace sdist."""

from __future__ import annotations

import functools
import hashlib
import http.server
import io
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import threading
import tomllib
import venv
from pathlib import Path

ROOT = Path.cwd()
WORK = ROOT / "work" / "maturin2808"
OUT = ROOT / "reports" / platform.system().lower()
WORK.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
WORKSPACE = WORK / "workspace"
PYTHON_CRATE = WORKSPACE / "crates/python"
REGISTRY = WORK / "registry"
NAME = "custard-registry-fixture-dep"
ALIAS = "custardfixture"
report = {"python": sys.version, "platform": platform.platform(), "commands": {}}


def save() -> None:
    """Preserve observations even if a later command fails."""
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def put(path: Path, contents: str | bytes) -> None:
    """Write fixture data only inside the ephemeral runner workspace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents.encode() if isinstance(contents, str) else contents)


def run(name: str, args: list[str], *, cwd: Path, env: dict, expect=0):
    """Save checked subprocess output without terminal control bytes."""
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, timeout=300)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    output = "".join(char for char in output if char in "\n\r\t" or ord(char) >= 32)
    (OUT / f"{name}.log").write_text(output, encoding="utf-8")
    report["commands"][name] = {"args": args, "returncode": result.returncode}
    save()
    if expect is not None:
        assert result.returncode == expect, (name, result.returncode, output[-7000:])
    return result.returncode, output


class Handler(http.server.SimpleHTTPRequestHandler):
    """Expose a synthetic public sparse registry only on runner loopback."""

    def log_message(self, format: str, *args) -> None:
        """Keep HTTP requests in the report rather than the job's terminal."""
        report.setdefault("registryRequests", []).append(self.path)


def prepare_registry(server: http.server.ThreadingHTTPServer) -> str:
    """Provide a zero-dependency crate without publishing to a real registry."""
    prefix = NAME + "-1.0.0"
    cargo = f'''[package]
name = "{NAME}"
version = "1.0.0"
edition = "2021"
'''
    crate = io.BytesIO()
    with tarfile.open(fileobj=crate, mode="w:gz") as archive:
        for path, source in {"Cargo.toml": cargo, "src/lib.rs": "pub fn answer() -> u32 { 42 }\n"}.items():
            contents = source.encode()
            member = tarfile.TarInfo(prefix + "/" + path)
            member.size = len(contents)
            member.mode = 0o644
            member.mtime = 0
            archive.addfile(member, io.BytesIO(contents))
    raw = crate.getvalue()
    base = f"http://127.0.0.1:{server.server_port}"
    put(REGISTRY / "config.json", json.dumps({"dl": base + "/api/v1/crates/{crate}/{version}/download"}))
    put(REGISTRY / "api/v1/crates" / NAME / "1.0.0/download", raw)
    checksum = hashlib.sha256(raw).hexdigest()
    entry = {"name": NAME, "vers": "1.0.0", "deps": [], "cksum": checksum,
             "features": {}, "yanked": False}
    put(REGISTRY / NAME[:2] / NAME[2:4] / NAME, json.dumps(entry) + "\n")
    report["registry"] = {"index": "sparse+" + base + "/", "crate": NAME,
                          "version": "1.0.0", "crateSha256": checksum,
                          "authenticationTested": False, "privateCredentialsUsed": False}
    return f'''[registries.{ALIAS}]
index = "sparse+{base}/"
credential-provider = "cargo:token"
'''


def project_toml(include: bool) -> str:
    """Change only explicit sdist inclusion between the two cases."""
    return '''[build-system]
requires = ["maturin==1.15.0"]
build-backend = "maturin"
[project]
name = "custard-registry-sdist-probe"
version = "1.0.0"
requires-python = ">=3.9"
[tool.maturin]
bindings = "bin"
auditwheel = "skip"
''' + ('include = [{ path = ".cargo/config.toml", format = "sdist" }]\n' if include else "")


def inspect_sdist(path: Path) -> dict:
    """Inspect actual archive layout and read the root build configuration."""
    with tarfile.open(path) as archive:
        names = archive.getnames()
        prefix = names[0].split("/")[0] + "/"
        files = [name.removeprefix(prefix) for name in names]
        project = archive.extractfile(prefix + "pyproject.toml").read().decode()
        configs = {name.removeprefix(prefix): archive.extractfile(name).read().decode()
                   for name in names if name.endswith("/.cargo/config.toml")}
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "files": files, "pyproject": project, "configs": configs,
            "manifestPath": tomllib.loads(project)["tool"]["maturin"]["manifest-path"]}


def env_for(phase: str, base: dict) -> dict:
    """Prevent Cargo caches or home configuration from masking missing metadata."""
    cargo_home = WORK / ("cargo-home-" + phase)
    cargo_home.mkdir(exist_ok=True)
    assert not (cargo_home / "config.toml").exists()
    return dict(base, CARGO_HOME=str(cargo_home), CARGO_TARGET_DIR=str(WORK / ("target-" + phase)))


put(WORKSPACE / "Cargo.toml", '''[workspace]
members = ["crates/lib", "crates/python"]
resolver = "2"
''')
put(WORKSPACE / "crates/lib/Cargo.toml", f'''[package]
name = "custard-registry-fixture-lib"
version = "1.0.0"
edition = "2021"
[dependencies]
{NAME} = {{ version = "=1.0.0", registry = "{ALIAS}" }}
''')
put(WORKSPACE / "crates/lib/src/lib.rs", "pub fn answer() -> u32 { custard_registry_fixture_dep::answer() }\n")
put(PYTHON_CRATE / "Cargo.toml", '''[package]
name = "custard-registry-sdist-probe"
version = "1.0.0"
edition = "2021"
[[bin]]
name = "registry_probe"
path = "src/main.rs"
[dependencies]
custard-registry-fixture-lib = { path = "../lib" }
''')
put(PYTHON_CRATE / "src/main.rs", 'fn main() { println!("{}", custard_registry_fixture_lib::answer()); }\n')
put(PYTHON_CRATE / "pyproject.toml", project_toml(False))
REGISTRY.mkdir(parents=True, exist_ok=True)
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(REGISTRY)))
config = prepare_registry(server)
put(WORKSPACE / ".cargo/config.toml", config)
put(PYTHON_CRATE / ".cargo/config.toml", config)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    folder = WORK / "venv"
    venv.EnvBuilder(with_pip=True).create(folder)
    scripts = folder / ("Scripts" if os.name == "nt" else "bin")
    python = str(scripts / ("python.exe" if os.name == "nt" else "python"))
    base_env = dict(os.environ, VIRTUAL_ENV=str(folder), PYTHONDONTWRITEBYTECODE="1",
                    PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_CACHE_DIR=str(WORK / "pip-cache"))
    base_env["PATH"] = str(scripts) + os.pathsep + base_env["PATH"]
    for key in tuple(base_env):
        if key.startswith("CARGO_REGISTRIES_CUSTARDFIXTURE_"):
            base_env.pop(key)
    run("tool-setup", [python, "-m", "pip", "install", "maturin==1.15.0"], cwd=WORK, env=base_env)
    _, version = run("maturin-version", [python, "-m", "maturin", "--version"], cwd=WORK, env=base_env)
    assert version.strip() == "maturin 1.15.0"
    _, cargo_version = run("cargo-version", ["cargo", "--version"], cwd=WORK, env=base_env)
    report["cargoVersion"] = cargo_version.strip()

    for phase, include in (("default", False), ("explicit-include", True)):
        put(PYTHON_CRATE / "pyproject.toml", project_toml(include))
        dist = WORK / ("dist-" + phase)
        dist.mkdir(exist_ok=True)
        run(phase + "-sdist", [python, "-m", "maturin", "sdist", "--out", str(dist)],
            cwd=PYTHON_CRATE, env=env_for("producer-" + phase, base_env))
        archives = list(dist.glob("*.tar.gz"))
        assert len(archives) == 1
        archive = inspect_sdist(archives[0])
        report[phase + "Archive"] = archive
        save()
        assert archive["manifestPath"].replace("\\", "/") == "crates/python/Cargo.toml"
        expected_config = ".cargo/config.toml"
        assert (expected_config in archive["configs"]) == include
        if include:
            assert archive["configs"][expected_config] == config
        wheel_dir = WORK / ("wheels-" + phase)
        wheel_dir.mkdir(exist_ok=True)
        install_env = env_for("consumer-" + phase, base_env)
        code, output = run(phase + "-pip-wheel", [python, "-m", "pip", "wheel", "--no-index",
            "--no-build-isolation", "--no-deps", "--wheel-dir", str(wheel_dir), str(archives[0])],
            cwd=WORK, env=install_env, expect=None)
        isolated_wheel_dir = WORK / ("wheels-isolated-" + phase)
        isolated_wheel_dir.mkdir(exist_ok=True)
        isolated_env = env_for("consumer-isolated-" + phase, base_env)
        isolated_code, isolated_output = run(phase + "-pip-wheel-isolated", [python, "-m", "pip",
            "wheel", "--no-deps", "--no-cache-dir", "--wheel-dir", str(isolated_wheel_dir),
            str(archives[0])], cwd=WORK, env=isolated_env, expect=None)
        assert "Installing build dependencies" in isolated_output
        if not include:
            assert code != 0 and "registry index was not found in any configuration" in output
            assert ALIAS in output
            assert isolated_code != 0 and "registry index was not found in any configuration" in isolated_output
            assert ALIAS in isolated_output
        else:
            assert code == 0, output[-7000:]
            assert isolated_code == 0, isolated_output[-7000:]
            wheels = list(isolated_wheel_dir.glob("*.whl"))
            assert len(wheels) == 1
            run("install-fixed-wheel", [python, "-m", "pip", "install", "--no-index", "--no-deps", str(wheels[0])],
                cwd=WORK, env=install_env)
            executable = scripts / ("registry_probe.exe" if os.name == "nt" else "registry_probe")
            _, actual = run("installed-binary", [str(executable)], cwd=WORK, env=install_env)
            assert actual.strip() == "42"
            report["installedBinaryOutput"] = actual.strip()
    report["allChecksPassed"] = True
    report["configurationValuesPreserved"] = True
    report["originalPrivateRegistryTested"] = False
    report["isolatedWheelBuildVerified"] = True
    report["wheelBuildModes"] = ["preinstalled-backend", "default-pep517-isolation"]
    report["bindings"] = "bin"
    save()
    print(json.dumps({"allChecksPassed": True, "installedBinaryOutput": report["installedBinaryOutput"],
        "defaultConfigPaths": list(report["defaultArchive"]["configs"]),
        "explicitConfigPaths": list(report["explicit-includeArchive"]["configs"])}))
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
