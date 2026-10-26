"""Validate the current-base synchronization on disposable runners only."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

assert os.environ.get("GITHUB_ACTIONS") == "true", "Use a disposable runner"
ROOT = Path.cwd().resolve()
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)
SOURCE = ROOT / "source"
CONFIG = json.loads(Path(__file__).with_name("candidate.json").read_text(encoding="utf-8"))
ENV = dict(os.environ)
ENV.update(
    BUN_INSTALL_CACHE_DIR=str(ROOT / "runner-cache" / "bun"),
    BUN_RUNTIME_TRANSPILER_CACHE_PATH=str(ROOT / "runner-cache" / "transpiler"),
    TURBO_CACHE_DIR=str(ROOT / "runner-cache" / "turbo"),
    DO_NOT_TRACK="1",
    TURBO_TELEMETRY_DISABLED="1",
)
RESULT = {"startedAtUtc": datetime.now(timezone.utc).isoformat(), "platform": sys.platform,
          "candidate": CONFIG, "commands": [], "terminalEmulatorTested": False,
          "serverBrowserReproductionClaimed": False}


def command(name, args, cwd=ROOT, timeout=1200, allow_failure=False):
    logfile = REPORTS / (name + ".log")
    started = time.monotonic()
    print("START", name, flush=True)
    with logfile.open("w", encoding="utf-8") as output:
        process = subprocess.run(args, cwd=cwd, env=ENV, stdout=output,
                                 stderr=subprocess.STDOUT, timeout=timeout)
    text = logfile.read_text(encoding="utf-8", errors="replace")
    record = {"name": name, "args": args, "cwd": str(cwd), "exitCode": process.returncode,
              "seconds": round(time.monotonic() - started, 3)}
    RESULT["commands"].append(record)
    print("END", name, process.returncode, "\n".join(text.splitlines()[-8:]), flush=True)
    assert allow_failure or process.returncode == 0, (name, process.returncode, text[-3000:])
    return process.returncode


def git(*args):
    return subprocess.check_output(["git", *args], cwd=SOURCE, env=ENV).decode("utf-8").strip()


def junit(name):
    root = ET.parse(REPORTS / name).getroot()
    cases = list(root.iter("testcase"))
    return {"total": len(cases), "passed": sum(not any(c.tag in ("failure", "error", "skipped") for c in x) for x in cases),
            "failures": sum(any(c.tag in ("failure", "error") for c in x) for x in cases),
            "skipped": sum(any(c.tag == "skipped" for c in x) for x in cases),
            "failedNames": [x.attrib.get("name") for x in cases if any(c.tag in ("failure", "error") for c in x)]}


try:
    command("clone", ["git", "clone", "--filter=blob:none", "--no-checkout", "https://github.com/Muguett-DBY/opencode.git", str(SOURCE)])
    command("checkout", ["git", "-c", "core.autocrlf=false", "checkout", "--detach", CONFIG["candidateHead"]], SOURCE)
    assert git("rev-parse", "HEAD") == CONFIG["candidateHead"]
    assert git("diff", "--name-only", CONFIG["upstreamHead"], "HEAD").splitlines() == sorted(CONFIG["paths"])
    before = {path: hashlib.sha256((SOURCE / path).read_bytes()).hexdigest() for path in CONFIG["paths"]}
    assert before == CONFIG["paths"]
    RESULT["bunVersion"] = subprocess.check_output(["bun", "--version"], env=ENV).decode().strip()
    assert RESULT["bunVersion"] == "1.4.4"
    command("install", ["bun", "install", "--frozen-lockfile", "--ignore-scripts"], SOURCE)
    client = SOURCE / "packages/client"

    # Repository guidance requests a randomized rerun while the full suite runs.
    parallel = [
        ("client-suite", ["bun", "test", "--timeout", "5000", "--reporter=junit", "--reporter-outfile", str(REPORTS / "client-suite.xml")]),
        ("solid-data-repeat", ["bun", "test", "--timeout", "5000", "test/solid-data.test.ts", "--rerun-each", "10", "--randomize", "--reporter=junit", "--reporter-outfile", str(REPORTS / "solid-data-repeat.xml")]),
    ]
    children = []
    for name, args in parallel:
        output = (REPORTS / (name + ".log")).open("w", encoding="utf-8")
        children.append((name, args, output, subprocess.Popen(args, cwd=client, env=ENV, stdout=output, stderr=subprocess.STDOUT), time.monotonic()))
    exits = []
    for name, args, output, process, started in children:
        try:
            code = process.wait(timeout=600)
        finally:
            output.close()
        text = (REPORTS / (name + ".log")).read_text(encoding="utf-8", errors="replace")
        RESULT["commands"].append({"name": name, "args": args, "cwd": str(client), "exitCode": code,
                                   "seconds": round(time.monotonic() - started, 3), "ranConcurrently": True})
        print("END", name, code, "\n".join(text.splitlines()[-8:]), flush=True)
        exits.append(code)
    assert exits == [0, 0], exits
    RESULT["clientSuite"] = junit("client-suite.xml")
    RESULT["solidDataRepeated"] = junit("solid-data-repeat.xml")
    assert RESULT["clientSuite"]["failures"] == RESULT["solidDataRepeated"]["failures"] == 0
    command("client-typecheck", ["bun", "run", "typecheck"], client)
    command("client-typecheck-tsc", ["bun", "run", "typecheck:tsc"], client)

    # This is a causal control of the actual entry point, not an extracted copy.
    production = SOURCE / "packages/client/src/solid/data.ts"
    fixed = production.read_bytes()
    upstream = subprocess.check_output(["git", "show", CONFIG["upstreamHead"] + ":packages/client/src/solid/data.ts"], cwd=SOURCE, env=ENV)
    pattern = "does not load unread MCP catalogs|keeps teardown|refreshes rebuilt MCP catalogs|discards queued MCP refreshes"
    try:
        production.write_bytes(upstream)
        code = command("upstream-causal-control", ["bun", "test", "--timeout", "5000", "test/solid-data.test.ts", "--test-name-pattern", pattern,
                       "--reporter=junit", "--reporter-outfile", str(REPORTS / "upstream-control.xml")], client, allow_failure=True)
        control = junit("upstream-control.xml")
        assert code != 0 and control["failures"] >= 3, control
        assert any("keeps teardown" in name for name in control["failedNames"])
        assert any("discards queued" in name for name in control["failedNames"])
        RESULT["upstreamControl"] = control
    finally:
        production.write_bytes(fixed)
    command("restored-regressions", ["bun", "test", "--timeout", "5000", "test/solid-data.test.ts", "--test-name-pattern", pattern,
            "--reporter=junit", "--reporter-outfile", str(REPORTS / "restored.xml")], client)
    RESULT["restored"] = junit("restored.xml")
    assert RESULT["restored"]["passed"] == 9 and RESULT["restored"]["failures"] == 0
    if sys.platform == "linux":
        command("repository-check", ["bun", "run", "check"], SOURCE)
    RESULT["finalFileHashes"] = {path: hashlib.sha256((SOURCE / path).read_bytes()).hexdigest() for path in CONFIG["paths"]}
    assert RESULT["finalFileHashes"] == CONFIG["paths"]
    RESULT["trackedStatus"] = git("status", "--porcelain", "--untracked-files=no")
    assert RESULT["trackedStatus"] == ""
    RESULT["success"] = True
finally:
    RESULT["finishedAtUtc"] = datetime.now(timezone.utc).isoformat()
    (REPORTS / "summary.json").write_text(json.dumps(RESULT, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"success": True, "platform": sys.platform, "clientSuite": RESULT["clientSuite"],
                  "repeated": RESULT["solidDataRepeated"], "restored": RESULT["restored"]}), flush=True)
