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


def failure_names(path: Path) -> list[str]:
    return [c.get("name", "") for c in ET.parse(path).iter("testcase")
            if c.find("failure") is not None or c.find("error") is not None]


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
                "pytest==9.0.3", "pytest-benchmark==4.0.0", "tzdata==2026.5",
                "python-dateutil==3.0.0"], timeout=600)
ENV["PYTHONPATH"] = str(SRC / "src")
run("installed-versions", [str(python), "-m", "pip", "freeze"])

def select_mode(label: str, flag: str) -> dict:
    ENV["PENDULUM_EXTENSIONS"] = flag
    mode_check = (
        "import json,os,pendulum,pendulum.helpers as h; "
        "import pendulum._helpers as pure,pendulum._pendulum as native; "
        "expected=pure if os.environ['PENDULUM_EXTENSIONS']=='0' else native; "
        "assert h.precise_diff is expected.precise_diff; "
        "print(json.dumps({'flag':os.environ['PENDULUM_EXTENSIONS'],"
        "'selectedModule':expected.__name__,'packagePath':pendulum.__file__}))"
    )
    return json.loads(run(label + "-mode", [str(python), "-B", "-c", mode_check], cwd=SRC))


behavior_code = (
    "from datetime import datetime; import json,importlib.metadata,pendulum; from dateutil import tz; "
    "named=tz.gettz('Europe/Paris'); generic=tz.tzoffset('custom',7200); "
    "named_result=pendulum.instance(datetime(2016,8,7,12,34,56,tzinfo=named)); "
    "generic_result=pendulum.instance(datetime(2016,8,7,12,34,56,tzinfo=generic)); "
    "assert not hasattr(generic,'key') and not hasattr(generic,'localize'); "
    "assert generic_result.timezone_name=='+02:00' and generic_result.offset==7200; "
    "assert named_result.offset==7200; "
    "print(json.dumps({'dateutil':importlib.metadata.version('python-dateutil'),"
    "'namedType':type(named).__module__+'.'+type(named).__qualname__,"
    "'namedKey':getattr(named,'key',None),'namedResult':named_result.timezone_name,"
    "'genericType':type(generic).__module__+'.'+type(generic).__qualname__,"
    "'genericResult':generic_result.timezone_name}))"
)
report["dateutil3Behavior"] = json.loads(run(
    "dateutil3-behavior", [str(python), "-B", "-c", behavior_code], cwd=SRC))
assert report["dateutil3Behavior"]["namedKey"] == "Europe/Paris"
assert report["dateutil3Behavior"]["namedResult"] == "Europe/Paris"

changed_files = run("pr-changed-files", ["git", "-C", str(SRC), "diff", "--name-only", BASE, HEAD]).decode().splitlines()
assert set(changed_files) == {"src/pendulum/tz/data/windows.py", "tests/tz/test_local_timezone.py"}
run("checkout-master-baseline", ["git", "-C", str(SRC), "checkout", "--detach", BASE])
report["unmodifiedMasterDateutil3"] = {}
for name, flag in [("pure-python", "0"), ("rust-extension", "1")]:
    select_mode("master-dateutil3-" + name, flag)
    result_path = OUT / ("master-dateutil3-" + name + ".xml")
    run("master-dateutil3-" + name, [str(python), "-B", "-m", "pytest", "-q",
        "tests/test_main.py::test_instance_with_aware_datetime_any_tzinfo",
        "--junitxml=" + str(result_path)], cwd=SRC, expected=(1,))
    value = junit(result_path)
    assert value == {"tests": 1, "failures": 1, "errors": 0, "skipped": 0}
    assert failure_names(result_path) == ["test_instance_with_aware_datetime_any_tzinfo"]
    report["unmodifiedMasterDateutil3"][name] = value
    save()
run("restore-pr-head", ["git", "-C", str(SRC), "checkout", "--detach", HEAD])
run("install-locked-dateutil", [str(python), "-m", "pip", "install", "--no-cache-dir",
                                "python-dateutil==2.9.0.post0"])
report["testSuites"] = {}
report["executionModes"] = {}
for name, flag in [("pure-python", "0"), ("rust-extension", "1")]:
    report["executionModes"][name] = select_mode("review-" + name, flag)
    full = OUT / ("review-" + name + "-tests.xml")
    run("review-" + name + "-tests", [str(python), "-B", "-m", "pytest", "tests", "-q",
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

run("checkout-master-for-fixture-fix", ["git", "-C", str(SRC), "checkout", "--detach", BASE])
test_file = SRC / "tests/test_main.py"
original_test = test_file.read_text(encoding="utf-8")
old = 'tzinfo=tz.gettz("Europe/Paris")'
new = 'tzinfo=tz.tzoffset("custom", 7200)'
assert original_test.count(old) == 1
patched_test = original_test.replace(old, new)
test_file.write_text(patched_test, encoding="utf-8")
patch_text = run("fixture-fix-diff", ["git", "-C", str(SRC), "diff", "--", "tests/test_main.py"]).decode()
numstat = run("fixture-fix-numstat", ["git", "-C", str(SRC), "diff", "--numstat"]).decode().strip()
assert numstat == "1\t1\ttests/test_main.py", numstat
report["fixtureFix"] = {"base": BASE, "path": "tests/test_main.py", "diff": patch_text,
                        "oldSha256": hashlib.sha256(original_test.encode()).hexdigest(),
                        "newSha256": hashlib.sha256(patched_test.encode()).hexdigest(),
                        "sourceOrDependencyConstraintsChanged": False, "versions": {}}
for version in ["2.9.0.post0", "3.0.0"]:
    run("fixture-install-dateutil-" + version, [str(python), "-m", "pip", "install", "--no-cache-dir",
                                               "python-dateutil==" + version])
    behavior = json.loads(run("fixture-behavior-" + version,
                             [str(python), "-B", "-c", behavior_code], cwd=SRC))
    assert behavior["dateutil"] == version
    if version == "3.0.0":
        assert behavior["namedKey"] == behavior["namedResult"] == "Europe/Paris"
    else:
        assert behavior["namedKey"] is None and behavior["namedResult"] == "+02:00"
    checks = {"behavior": behavior, "testSuites": {}, "executionModes": {}}
    for name, flag in [("pure-python", "0"), ("rust-extension", "1")]:
        label = "fixture-" + version + "-" + name
        checks["executionModes"][name] = select_mode(label, flag)
        full = OUT / (label + ".xml")
        run(label, [str(python), "-B", "-m", "pytest", "tests", "-q", "--benchmark-disable",
                    "--junitxml=" + str(full)], cwd=SRC, timeout=360)
        value = junit(full)
        assert value["tests"] > 1800 and value["failures"] == value["errors"] == 0
        checks["testSuites"][name] = value
    report["fixtureFix"]["versions"][version] = checks
    save()
run("restore-master-test", ["git", "-C", str(SRC), "checkout", "--", "tests/test_main.py"])
run("restore-final-pr-head", ["git", "-C", str(SRC), "checkout", "--detach", HEAD])
run("final-source-unchanged", ["git", "-C", str(SRC), "diff", "--exit-code"])
report["allChecksPassed"] = True
report["scope"] = (
    "Exact PR head, editable build including Rust extension; full tests in both execution modes with "
    "the lockfile's dateutil 2.9.0.post0. Separate generic-tzinfo fixture fix on unchanged master "
    "validated against dateutil 2.9.0.post0 and 3.0.0 in both modes. The original master test's "
    "dateutil 3 failure is an asserted negative control. Benchmark timing disabled. "
    "Native Windows checks read real registry handles and substitute EnumValue results only in process; "
    "no registry writes or OS timezone changes. Pinned tzdata 2026.5 and CLDR blob."
)
save()
print(json.dumps({"allChecksPassed": True, "tests": report["testSuites"],
                  "offsetComparisons": value["offsetComparisons"], "windowsRegistryChecks": sys.platform == "win32"}))
