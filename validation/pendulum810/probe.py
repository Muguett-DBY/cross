"""Check Kazakhstan offsets and data-source precedence on disposable runners."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import venv
from pathlib import Path

assert os.environ.get("GITHUB_ACTIONS") == "true", "Disposable-runner execution only."
ROOT = Path.cwd()
WORK = ROOT / "work"
OUT = ROOT / "reports"
WORK.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
           PIP_CACHE_DIR=str(WORK / "pip-cache"))
example = (ROOT / "validation/pendulum810/example.py").read_text(encoding="utf-8")
report = {"python": sys.version, "platform": platform.platform(), "commands": {},
          "versions": {}, "exampleSha256": hashlib.sha256(example.encode()).hexdigest()}


def save() -> None:
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run(name: str, args: list[str], env: dict[str, str] | None = None) -> bytes:
    result = subprocess.run(args, cwd=WORK, env=env or ENV, capture_output=True, timeout=180)
    (OUT / (name + ".log")).write_bytes(result.stdout + result.stderr)
    report["commands"][name] = {"args": args, "returncode": result.returncode}
    save()
    assert result.returncode == 0, (name, result.stderr.decode(errors="replace")[-3000:])
    return result.stdout


check_source = r'''
import contextlib, hashlib, importlib.metadata, io, json, sys, zoneinfo
from pathlib import Path
import pendulum, tzdata

mode = sys.argv[1]
updated = mode == "updated"
if mode == "observe":
    updated = pendulum.datetime(2024, 3, 2, 12, tz="Asia/Almaty").offset == 18000
result = {"pendulum": importlib.metadata.version("pendulum"),
          "tzdata": tzdata.__version__, "ianaVersion": tzdata.IANA_VERSION,
          "tzpath": list(zoneinfo.TZPATH), "pendulumPath": pendulum.__file__,
          "tzdataPath": tzdata.__file__, "has2024aTransition": updated, "zones": {}}
for zone in ["Asia/Almaty", "Asia/Qostanay"]:
    before = pendulum.datetime(2024, 2, 29, 12, tz=zone)
    after = pendulum.datetime(2024, 3, 2, 12, tz=zone)
    assert before.offset == 21600
    assert after.offset == (18000 if updated else 21600), (zone, after)
    utc_before = pendulum.datetime(2024, 2, 29, 17, 59, 59, tz="UTC")
    utc_after = utc_before.add(seconds=1)
    converted_before = utc_before.in_timezone(zone)
    converted_after = utc_after.in_timezone(zone)
    assert converted_before.isoformat() == "2024-02-29T23:59:59+06:00"
    expected = "2024-02-29T23:00:00+05:00" if updated else "2024-03-01T00:00:00+06:00"
    assert converted_after.isoformat() == expected, (zone, converted_after)
    assert converted_after.in_timezone("UTC") == utc_after
    result["zones"][zone] = {"before": before.isoformat(), "after": after.isoformat(),
                            "utcBeforeTransition": converted_before.isoformat(),
                            "utcAtTransition": converted_after.isoformat(),
                            "roundTripPreserved": True}
assert pendulum.datetime(2024, 3, 2, 12, tz="Asia/Aqtobe").offset == 18000
example = Path(sys.argv[2]).read_text(encoding="utf-8")
stream = io.StringIO()
with contextlib.redirect_stdout(stream):
    exec(compile(example, "example.py", "exec"), {})
result["exampleOutput"] = stream.getvalue()
assert result["exampleOutput"] == (
    "2024-02-29T12:00:00+06:00\n" +
    ("2024-03-02T12:00:00+05:00\n" if updated else "2024-03-02T12:00:00+06:00\n")
)
result["exampleSha256"] = hashlib.sha256(example.encode()).hexdigest()
print(json.dumps(result))
'''

checker = WORK / "check_offsets.py"
checker.write_text(check_source, encoding="utf-8")
example_file = WORK / "example.py"
example_file.write_text(example, encoding="utf-8")
(OUT / "example.py").write_text(example, encoding="utf-8")
for version in ("3.0.0", "3.3.0"):
    folder = WORK / ("pendulum-" + version)
    venv.EnvBuilder(with_pip=True).create(folder)
    python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run(version + "-install-old", [str(python), "-m", "pip", "install", "--no-cache-dir",
                                  "pendulum==" + version, "tzdata==2023.4"])
    old_dir = WORK / ("old-zonefiles-" + version)
    copy_code = (
        "from pathlib import Path; import shutil,tzdata; "
        "shutil.copytree(Path(tzdata.__file__).parent/'zoneinfo',Path(__import__('sys').argv[1]))"
    )
    run(version + "-preserve-old-fixture", [str(python), "-B", "-c", copy_code, str(old_dir)])
    results = {}

    def check(name: str, expected: str, tzpath: str | None) -> None:
        env = dict(ENV)
        if tzpath is None:
            env.pop("PYTHONTZPATH", None)
        else:
            env["PYTHONTZPATH"] = tzpath
        checked = run(version + "-" + name,
                      [str(python), "-B", str(checker), expected, str(example_file)], env)
        value = json.loads(checked)
        assert value["pendulum"] == version and value["exampleSha256"] == report["exampleSha256"]
        results[name] = value

    check("old-package-fallback", "old", "")
    run(version + "-install-2024a", [str(python), "-m", "pip", "install", "--no-cache-dir",
                                    "tzdata==2024.1"])
    check("2024a-package-fallback", "updated", "")
    assert results["2024a-package-fallback"]["ianaVersion"] == "2024a"
    check("old-directory-overrides-new-package", "old", str(old_dir))
    check("empty-tzpath-restores-package", "updated", "")
    run(version + "-install-current", [str(python), "-m", "pip", "install", "--no-cache-dir",
                                      "tzdata==2026.5"])
    check("current-package-fallback", "updated", "")
    check("runner-default-data-source", "observe", None)
    report["versions"][version] = results
    save()
report["allChecksPassed"] = True
report["scope"] = (
    "Fresh native Python 3.12 processes with installed Pendulum wheels and pinned tzdata packages; "
    "old zone files supplied through a subprocess-only PYTHONTZPATH. No OS timezone files changed."
)
save()
print(json.dumps({"allChecksPassed": True, "versions": list(report["versions"])}))
