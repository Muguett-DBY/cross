"""Verify the exact numbered-menu example with real Rich input validation."""

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

assert os.environ.get("GITHUB_ACTIONS") == "true", "Run only on disposable Actions runners."
ROOT = Path.cwd()
WORK = ROOT / "work"
OUT = ROOT / "reports"
WORK.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", NO_COLOR="1",
           PIP_CACHE_DIR=str(WORK / "pip-cache"))
example = (ROOT / "validation/rich3694/example.py").read_text(encoding="utf-8")
report = {"python": sys.version, "platform": platform.platform(), "commands": {}, "versions": {},
          "exampleSha256": hashlib.sha256(example.encode()).hexdigest(),
          "scope": "Real subprocess stdin, no terminal emulator or keyboard navigation."}


def save() -> None:
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run(name: str, args: list[str], *, input_text: str | None = None) -> str:
    result = subprocess.run(args, cwd=WORK, env=ENV, input=input_text.encode() if input_text is not None else None,
                            capture_output=True, timeout=180)
    text = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = "".join(c for c in text if c in "\n\r\t" or ord(c) >= 32)
    (OUT / (name + ".log")).write_text(text, encoding="utf-8")
    report["commands"][name] = {"args": args, "returncode": result.returncode}
    save()
    assert result.returncode == 0, (name, result.returncode, text[-3000:])
    return text


choices = ["Lorem ipsum", "Dolor sit amet", "consectetur adipiscing elit"]
cases = [
    ("first", "1\n", 1, 0, 0),
    ("middle", "2\n", 2, 0, 0),
    ("last", "3\n", 3, 0, 0),
    ("surrounding-space", " 2 \n", 2, 0, 0),
    ("out-of-range", "0\n4\n2\n", 2, 2, 0),
    ("non-integer", "word\n2\n", 2, 0, 1),
    ("blank", "\n3\n", 3, 0, 1),
    ("leading-zero", "02\n2\n", 2, 1, 0),
    ("signed-choice", "+2\n2\n", 2, 1, 0),
    ("decimal", "2.0\n2\n", 2, 0, 1),
]
runner = WORK / "example.py"
runner.write_text(example + '\nimport json\nprint("RICH_MENU_RESULT=" + json.dumps({"number": number, "selected": selected}))\n',
                  encoding="utf-8")
(OUT / "example.py").write_text(example, encoding="utf-8")
for version in ("14.1.0", "15.0.0"):
    folder = WORK / ("rich-" + version)
    venv.EnvBuilder(with_pip=True).create(folder)
    python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run(version + "-install", [str(python), "-m", "pip", "install", "--no-cache-dir", "rich==" + version])
    provenance = run(version + "-provenance", [str(python), "-c", '''import hashlib,importlib.metadata,inspect,json
from pathlib import Path
import rich.prompt
path=Path(inspect.getsourcefile(rich.prompt))
print(json.dumps({"rich":importlib.metadata.version("rich"), "promptPath":str(path),
                  "promptSha256":hashlib.sha256(path.read_bytes()).hexdigest()}))
'''])
    info = json.loads(provenance)
    assert info["rich"] == version
    report["versions"][version] = {"provenance": info, "cases": {}}
    for name, input_text, expected_number, invalid_choice, invalid_integer in cases:
        log = run(version + "-" + name, [str(python), "-B", str(runner)], input_text=input_text)
        match = re.search(r"RICH_MENU_RESULT=(\{[^\n]+\})", log)
        assert match, log
        result = json.loads(match[1])
        assert result == {"number": expected_number, "selected": choices[expected_number - 1]}, result
        assert log.count("Please select one of the available options") == invalid_choice, log
        assert log.count("Please enter a valid integer number") == invalid_integer, log
        assert "[1/2/3]" not in log
        for index, label in enumerate(choices, 1):
            assert f"{index}. {label}" in log
        report["versions"][version]["cases"][name] = {
            "input": input_text, "result": result, "invalidChoiceMessages": invalid_choice,
            "invalidIntegerMessages": invalid_integer, "visibleChoiceSuffix": False,
            "numberedLabelsPrinted": True,
        }
        save()
report["allChecksPassed"] = True
report["casesPerVersion"] = len(cases)
save()
print(json.dumps({"allChecksPassed": True, "versions": list(report["versions"]), "casesPerVersion": len(cases)}))
