"""Independently review the pinned Windows timezone mapping PR on CI runners."""

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
HEAD = "ab35f7d2f0c9da3ffa56d6d9fd203b14e9647083"
BASE = "e06dacedc4cdea90fa1286b3965d180ab94cf251"
CLDR_BLOB = "2dc8c386cb3aed1a8c21b4ba7bb7b8d4f3535507"
ROOT = Path.cwd()
WORK = ROOT / "work"
OUT = ROOT / "reports"
SRC = WORK / "pendulum"
WORK.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
           PYTHONTZPATH="", PIP_CACHE_DIR=str(WORK / "pip-cache"),
           CARGO_HOME=str(WORK / "cargo-cache"), PENDULUM_EXTENSIONS="1")
report = {"head": HEAD, "base": BASE, "python": sys.version,
          "platform": platform.platform(), "commands": {}, "cldrBlob": CLDR_BLOB}


def save() -> None:
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run(name: str, args: list[str], *, cwd: Path = WORK,
        expected: tuple[int, ...] = (0,), timeout: int = 240) -> bytes:
    result = subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, timeout=timeout)
    (OUT / (name + ".log")).write_bytes(result.stdout + result.stderr)
    report["commands"][name] = {"args": args, "returncode": result.returncode,
                                "expectedReturncodes": list(expected)}
    save()
    assert result.returncode in expected, (
        name, result.returncode, result.stderr.decode(errors="replace")[-3500:]
    )
    return result.stdout


def junit(path: Path) -> dict[str, int]:
    tree = ET.parse(path)
    return {field: sum(int(s.get(field, "0")) for s in tree.iter("testsuite"))
            for field in ["tests", "failures", "errors", "skipped"]}


run("git-init", ["git", "init", str(SRC)])
run("git-remote", ["git", "-C", str(SRC), "remote", "add", "origin",
                   "https://github.com/python-pendulum/pendulum.git"])
run("git-head", ["git", "-C", str(SRC), "fetch", "--depth=1", "origin",
                 "refs/pull/1059/head"])
run("git-base", ["git", "-C", str(SRC), "fetch", "--depth=1", "origin", BASE])
run("git-checkout", ["git", "-C", str(SRC), "checkout", "--detach", HEAD])
assert run("git-head-readback", ["git", "-C", str(SRC), "rev-parse", "HEAD"]).decode().strip() == HEAD

folder = WORK / "venv"
venv.EnvBuilder(with_pip=True).create(folder)
python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
run("install", [str(python), "-m", "pip", "install", "--no-cache-dir", "-e", str(SRC) + "[test]",
                "pytest==9.0.3", "pytest-benchmark==4.0.0", "tzdata==2026.5"], timeout=600)
ENV["PYTHONPATH"] = str(SRC / "src")
run("installed-versions", [str(python), "-m", "pip", "freeze"])

report["testSuites"] = {}
report["executionModes"] = {}
for name, flag in [("pure-python", "0"), ("rust-extension", "1")]:
    ENV["PENDULUM_EXTENSIONS"] = flag
    mode_check = (
        "import json,os,pendulum,pendulum.helpers as h; "
        "import pendulum._helpers as pure,pendulum._pendulum as native; "
        "expected=pure if os.environ['PENDULUM_EXTENSIONS']=='0' else native; "
        "assert h.precise_diff is expected.precise_diff; "
        "print(json.dumps({'flag':os.environ['PENDULUM_EXTENSIONS'],"
        "'selectedModule':expected.__name__,'packagePath':pendulum.__file__}))"
    )
    report["executionModes"][name] = json.loads(run(
        name + "-mode", [str(python), "-B", "-c", mode_check], cwd=SRC))
    full = OUT / (name + "-tests.xml")
    run(name + "-tests", [str(python), "-B", "-m", "pytest", "tests", "-q",
                          "--benchmark-disable", "--junitxml=" + str(full)],
        cwd=SRC, timeout=360)
    value = junit(full)
    assert value["tests"] > 1800 and value["failures"] == value["errors"] == 0
    report["testSuites"][name] = value
    save()

check_source = r'''
import datetime, hashlib, importlib, importlib.metadata, json, os, sys, zoneinfo
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
import pendulum
import pendulum._pendulum as acceleration
import tzdata
from pendulum.tz.data.windows import windows_timezones

source = Path(sys.argv[1]).resolve()
assert Path(pendulum.__file__).resolve().is_relative_to(source / "src")
assert importlib.metadata.version("pendulum") == "3.3.0"
assert tzdata.__version__ == "2026.5" and tzdata.IANA_VERSION == "2026e"
assert zoneinfo.TZPATH == ()
xml = Path(sys.argv[2]).read_text(encoding="utf-8")
blob = hashlib.sha1(b"blob " + str(len(xml.encode())).encode() + b"\0" + xml.encode()).hexdigest()
assert blob == "2dc8c386cb3aed1a8c21b4ba7bb7b8d4f3535507"
cldr = {r.attrib["other"]: r.attrib["type"] for r in ET.fromstring(xml).findall(".//mapZone")
        if r.attrib["territory"] == "001"}
assert set(cldr) == set(windows_timezones) and len(cldr) == 142
aliases = {"FLE Standard Time": ("Europe/Kyiv", "Europe/Kiev"), "UTC": ("Etc/GMT", "Etc/UTC")}
dates = [(2026, 1, 15), (2026, 7, 15), (2026, 10, 31), (2026, 11, 2), (2027, 1, 15)]
offsets = {}
for name, expected in cldr.items():
    actual = windows_timezones[name]
    assert actual == expected or (actual, expected) == aliases.get(name), (name, actual, expected)
    observed = []
    for year, month, day in dates:
        instant = datetime.datetime(year, month, day, 12, tzinfo=datetime.timezone.utc)
        got = instant.astimezone(pendulum.timezone(actual))
        want = instant.astimezone(zoneinfo.ZoneInfo(expected))
        assert got.utcoffset() == want.utcoffset(), (name, instant, got, want)
        observed.append(int(got.utcoffset().total_seconds()))
    offsets[name] = observed

canada = {"British Columbia Standard Time": -7*3600, "Alberta Standard Time": -6*3600,
          "Manitoba Standard Time": -5*3600}
for name, offset in canada.items():
    assert offsets[name][3] == offsets[name][4] == offset
result = {"pendulum": importlib.metadata.version("pendulum"),
          "pendulumPath": pendulum.__file__, "extensionPath": acceleration.__file__,
          "extensionSha256": hashlib.sha256(Path(acceleration.__file__).read_bytes()).hexdigest(),
          "tzdata": tzdata.__version__, "ianaVersion": tzdata.IANA_VERSION, "tzpath": list(zoneinfo.TZPATH),
          "cldrBlob": blob, "mappingCount": len(cldr), "dates": dates,
          "offsetComparisons": len(cldr)*len(dates), "offsets": offsets,
          "nativeWindowsRegistryChecks": None}
if sys.platform == "win32":
    import winreg
    local = importlib.import_module("pendulum.tz.local_timezone")
    original_enum = winreg.EnumValue
    local._local_timezone = None
    local._mock_local_timezone = None
    real = pendulum.now()
    assert real.timezone is not None
    native = {"actualRegistryLookupTimezone": real.timezone.name, "injectedNames": [],
              "suffixFallbacks": [], "unknownNameRejected": False,
              "registryWritesPerformed": False}

    def with_name(supplied):
        def enum_value(key, index):
            value = original_enum(key, index)
            if value[0] == "TimeZoneKeyName":
                return value[0], supplied, value[2]
            return value
        local._local_timezone = None
        local._mock_local_timezone = None
        with patch.object(winreg, "EnumValue", enum_value):
            return pendulum.now()

    for name, expected in windows_timezones.items():
        for suffix in ["", "\0ignored-tail"]:
            value = with_name(name + suffix)
            assert value.timezone.name == expected, (name, value)
            native["injectedNames"].append({"windowsName": name, "nulPadded": bool(suffix),
                                           "timezone": value.timezone.name})
    changed = ["British Columbia Standard Time", "Alberta Standard Time", "Manitoba Standard Time",
               "Yukon Standard Time", "Qyzylorda Standard Time", "South Sudan Standard Time",
               "Volgograd Standard Time", "Central Asia Standard Time"]
    for name in changed:
        short = name.removesuffix(" Standard Time")
        value = with_name(short)
        assert value.timezone.name == windows_timezones[name]
        native["suffixFallbacks"].append({"input": short, "timezone": value.timezone.name})
    try:
        with_name("__unknown_timezone_for_review__")
    except LookupError as error:
        assert "__unknown_timezone_for_review__" in str(error)
        native["unknownNameRejected"] = True
    else:
        raise AssertionError("Unknown Windows name unexpectedly accepted")
    local._local_timezone = None
    assert pendulum.now().timezone.name == real.timezone.name
    result["nativeWindowsRegistryChecks"] = native
print(json.dumps(result))
'''
checker = WORK / "independent_checks.py"
checker.write_text(check_source, encoding="utf-8")
value = json.loads(run("independent-checks", [str(python), "-B", str(checker), str(SRC),
                                            str(ROOT / "validation/pendulum1059/windowsZones.xml")], cwd=SRC))
report["independentChecks"] = value
save()

mapping_file = SRC / "src/pendulum/tz/data/windows.py"
original = mapping_file.read_bytes()
base_mapping = run("base-mapping", ["git", "-C", str(SRC), "show", BASE + ":src/pendulum/tz/data/windows.py"])
try:
    mapping_file.write_bytes(base_mapping)
    baseline = OUT / "baseline-regressions.xml"
    run("baseline-regressions", [str(python), "-B", "-m", "pytest", "tests/tz/test_local_timezone.py",
                                 "-q", "-k", "windows_timezone_names", "--junitxml=" + str(baseline)],
        cwd=SRC, expected=(1,))
    report["baselineRegressions"] = junit(baseline)
    assert report["baselineRegressions"] == {"tests": 10, "failures": 9, "errors": 0, "skipped": 0}
finally:
    mapping_file.write_bytes(original)

restored = OUT / "restored-regressions.xml"
run("restored-regressions", [str(python), "-B", "-m", "pytest", "tests/tz/test_local_timezone.py",
                             "-q", "-k", "windows_timezone_names", "--junitxml=" + str(restored)], cwd=SRC)
report["restoredRegressions"] = junit(restored)
assert report["restoredRegressions"] == {"tests": 10, "failures": 0, "errors": 0, "skipped": 0}
run("source-unchanged", ["git", "-C", str(SRC), "diff", "--exit-code"])
report["mappingSha256"] = hashlib.sha256(original).hexdigest()
report["allChecksPassed"] = True
report["scope"] = (
    "Exact PR head, editable build including Rust extension; all tests in pure-Python and extension modes, "
    "with benchmark timing disabled. "
    "Native Windows checks read real registry handles and substitute EnumValue results only in process; "
    "no registry writes or OS timezone changes. Pinned tzdata 2026.5 and CLDR blob."
)
save()
print(json.dumps({"allChecksPassed": True, "tests": report["testSuites"],
                  "offsetComparisons": value["offsetComparisons"], "windowsRegistryChecks": sys.platform == "win32"}))
