"""Check rendered progress-column allocation using the exact proposed example."""

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
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", NO_COLOR="1",
           PIP_CACHE_DIR=str(WORK / "pip-cache"))
example = (ROOT / "validation/rich3665/example.py").read_text(encoding="utf-8")
report = {"python": sys.version, "platform": platform.platform(), "commands": {}, "versions": {},
          "exampleSha256": hashlib.sha256(example.encode()).hexdigest()}


def save() -> None:
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run(name: str, args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=WORK, env=ENV, capture_output=True, timeout=180)
    (OUT / (name + ".log")).write_bytes(result.stdout + result.stderr)
    report["commands"][name] = {"args": args, "returncode": result.returncode}
    save()
    assert result.returncode == 0, (name, result.returncode, result.stderr.decode(errors="replace")[-3000:])
    return result


check_source = r'''
import hashlib, importlib.metadata, io, json, re, sys
from pathlib import Path
from rich.cells import cell_len
from rich.console import Console
from rich.progress import TextColumn

example = Path(sys.argv[1]).read_text(encoding="utf-8")
def make(mode="fixed"):
    namespace = {}
    exec(compile(example, "example.py", "exec"), namespace)
    progress = namespace["progress"]
    if mode == "missing-ratio":
        progress.columns = (*progress.columns[:-1], TextColumn("{task.fields[detail]}"))
    elif mode == "missing-expand":
        progress.expand = False
    task = progress.add_task("Downloading", total=100, completed=25, detail="DETAIL:ok")
    return progress, task

def render(progress, width, *, color=True, single_line=True):
    stream = io.StringIO()
    console = Console(file=stream, width=width, force_terminal=color,
                      color_system="standard" if color else None, no_color=not color)
    console.print(progress.make_tasks_table(progress.tasks))
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", stream.getvalue())
    lines = text.splitlines()
    if single_line:
        assert len(lines) == 1, (width, text)
    assert all(cell_len(line) <= width for line in lines)
    return lines[0] if single_line else text

def intact(line):
    return all(marker in line for marker in ["Downloading", "25/100", "25%"])

result = {"rich": importlib.metadata.version("rich"), "cases": [], "controls": {}, "resize": []}
for width in [80, 96, 120]:
    prefixes = []
    for name, detail in [("short", "DETAIL:ok"), ("long-word", "DETAIL:" + "x"*240),
                         ("long-spaced", "DETAIL:" + "file "*60)]:
        progress, task = make()
        progress.update(task, detail=detail)
        line = render(progress, width)
        assert intact(line), (width, name, line)
        prefix, displayed = line.split("DETAIL:", 1)
        prefixes.append(prefix)
        bar = prefix.split("Downloading", 1)[1].split("25/100", 1)[0].strip()
        assert cell_len(bar) == 40, (width, name, bar, line)
        if name == "short":
            assert displayed.rstrip() == "ok", line
        else:
            assert line.rstrip().endswith("…"), line
        plain = render(progress, width, color=False)
        for marker in ["Downloading", "25/100", "25%", "DETAIL:"]:
            assert plain.index(marker) == line.index(marker), (marker, line, plain)
        result["cases"].append({"width": width, "detail": name, "line": line, "prefix": prefix,
                                "plainLine": plain, "barCells": cell_len(bar),
                                "allFourLeadingColumnsIntact": True})
        if width == 80 and name == "long-word":
            fixed_prefix = prefix
    assert prefixes[0] == prefixes[1] == prefixes[2], (width, prefixes)

progress, task = make()
progress.update(task, detail="DETAIL:" + "x"*240)
for width in [120, 80, 96, 120]:
    line = render(progress, width)
    assert intact(line) and line.rstrip().endswith("…"), line
    result["resize"].append({"width": width, "line": line})
assert result["resize"][0]["line"] == result["resize"][-1]["line"]
for mode in ["missing-ratio", "missing-expand"]:
    progress, task = make(mode)
    progress.update(task, detail="DETAIL:" + "x"*240)
    text = render(progress, 80, single_line=False)
    preserved = text.startswith(fixed_prefix)
    assert not preserved, (mode, text)
    result["controls"][mode] = {"text": text, "leadingColumnsIntact": intact(text),
                                "sameLeadingColumnLayout": preserved}
result["exampleSha256"] = hashlib.sha256(example.encode()).hexdigest()
print(json.dumps(result))
'''
checker = WORK / "render_checks.py"
checker.write_text(check_source, encoding="utf-8")
example_file = WORK / "example.py"
example_file.write_text(example, encoding="utf-8")
(OUT / "example.py").write_text(example, encoding="utf-8")
for version in ("14.1.0", "15.0.0"):
    folder = WORK / ("rich-" + version)
    venv.EnvBuilder(with_pip=True).create(folder)
    python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run(version + "-install", [str(python), "-m", "pip", "install", "--no-cache-dir", "rich==" + version])
    checked = run(version + "-render", [str(python), "-B", str(checker), str(example_file)])
    value = json.loads(checked.stdout)
    assert value["rich"] == version and value["exampleSha256"] == report["exampleSha256"]
    report["versions"][version] = value
    save()
report["allChecksPassed"] = True
report["scope"] = "Static Rich text rendering at explicit Console widths, including re-rendering the same Progress instance; no terminal emulator."
save()
print(json.dumps({"allChecksPassed": True, "versions": list(report["versions"])}))
