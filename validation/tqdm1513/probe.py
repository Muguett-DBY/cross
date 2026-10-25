"""Verify type inference examples on a disposable runner, with diagnostic controls."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

assert os.environ.get("GITHUB_ACTIONS") == "true", "Runner-only dependency installation."
ROOT = Path.cwd()
WORK = ROOT / "work" / "tqdm1513"
OUT = ROOT / "reports"
WORK.mkdir(parents=True, exist_ok=True)
OUT.mkdir(exist_ok=True)
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
           PIP_CACHE_DIR=str(WORK / "pip-cache"), MYPY_CACHE_DIR=str(WORK / "mypy-cache"))
report = {"python": sys.version, "platform": platform.platform(), "commands": {}, "checks": {}}


def save() -> None:
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run(name: str, args: list[str], *, expected: int = 0) -> str:
    result = subprocess.run(args, cwd=WORK, env=ENV, capture_output=True, timeout=180)
    text = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = "".join(c for c in text if c in "\n\r\t" or ord(c) >= 32)
    (OUT / f"{name}.log").write_text(text, encoding="utf-8")
    report["commands"][name] = {"args": args, "returncode": result.returncode}
    save()
    assert result.returncode == expected, (name, result.returncode, text)
    return text


def check(name: str, source: str, *, flags: tuple[str, ...] = (), expected: int = 0) -> str:
    path = WORK / (name + ".py")
    path.write_text(source, encoding="utf-8")
    (OUT / path.name).write_text(source, encoding="utf-8")
    text = run(name, [sys.executable, "-m", "mypy", "--no-incremental", "--show-error-codes",
                      "--no-error-summary", "--no-pretty", *flags, str(path)], expected=expected)
    report["checks"][name] = {"expectedExit": expected, "sourceSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                             "diagnostics": text.splitlines()}
    save()
    return text


run("install-pinned-tools", [sys.executable, "-m", "pip", "install", "mypy==2.4.0",
                            "types-tqdm==4.70.0.20260906", "tqdm==4.70.1"])
run("versions", [sys.executable, "-m", "pip", "freeze"])
run("mypy-version", [sys.executable, "-m", "mypy", "--version"])

module = '''from tqdm import tqdm
no_items = 10
pbar = tqdm(desc="producer", total=no_items)
reveal_type(pbar)
pbar.update(1)
pbar.close()
'''
text = check("module-default", module)
assert 'Revealed type is "tqdm.std.tqdm[Never]"' in text, text
text = check("module-strict", module, flags=("--strict",))
assert 'Revealed type is "tqdm.std.tqdm[Never]"' in text, text

typed = '''from tqdm import tqdm
def producer(no_items: int) -> None:
    pbar = tqdm(desc="producer", total=no_items)
    reveal_type(pbar)
    pbar.update(1)
    pbar.close()
'''
text = check("typed-function-strict", typed, flags=("--strict",))
assert 'Revealed type is "tqdm.std.tqdm[Never]"' in text, text
text = check("invalid-update", typed.replace("pbar.update(1)", 'pbar.update("wrong")'),
             flags=("--strict",), expected=1)
assert text.count("error:") == 1 and "[arg-type]" in text and '"update"' in text, text

iterable = '''from tqdm import tqdm
pbar = tqdm(range(3))
reveal_type(pbar)
for value in pbar:
    reveal_type(value)
'''
text = check("iterable-strict", iterable, flags=("--strict",))
assert 'Revealed type is "tqdm.std.tqdm[int]"' in text, text
assert 'Revealed type is "int"' in text, text

untyped = typed.replace("def producer(no_items: int) -> None:", "def producer(no_items):")
text = check("untyped-default", untyped)
assert 'Revealed type is "Any"' in text and "unchecked functions" in text, text
text = check("untyped-checked", untyped, flags=("--check-untyped-defs",))
assert 'Revealed type is "tqdm.std.tqdm[Never]"' in text, text
text = check("untyped-invalid-default", untyped.replace("pbar.update(1)", 'pbar.update("wrong")'))
assert "error:" not in text and 'Revealed type is "Any"' in text, text
text = check("untyped-invalid-checked", untyped.replace("pbar.update(1)", 'pbar.update("wrong")'),
             flags=("--check-untyped-defs",), expected=1)
assert text.count("error:") == 1 and "[arg-type]" in text, text

stub = run("stub-provenance", [sys.executable, "-c", '''import hashlib, importlib.metadata, json
from pathlib import Path
distribution = importlib.metadata.distribution("types-tqdm")
stub = next(distribution.locate_file(file) for file in distribution.files if str(file).endswith("tqdm-stubs/std.pyi"))
data = Path(stub).read_bytes()
print(json.dumps({"distribution": distribution.metadata["Name"], "version": distribution.version,
                  "file": str(stub), "sha256": hashlib.sha256(data).hexdigest(),
                  "containsNeverOverload": b"self: tqdm[Never]" in data}))
'''])
report["stub"] = json.loads(stub)
assert report["stub"]["containsNeverOverload"]
report["allChecksPassed"] = True
report["scope"] = "Mypy CLI and published tqdm stubs on Python 3.12; original 2023 versions/configuration not known."
save()
print(json.dumps({"allChecksPassed": True, "checks": len(report["checks"]), "stub": report["stub"]}))
