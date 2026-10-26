"""Disposable-runner checks of the exact proposed support-answer class."""
import ast
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import subprocess
import sys

assert os.environ.get("GITHUB_ACTIONS") == "true", "Run on a disposable GitHub runner only"
ROOT = Path(__file__).resolve().parent
REPORTS = Path("reports").resolve()
REPORTS.mkdir(exist_ok=True)
ANSWER = (ROOT / "answer.txt").read_text(encoding="utf-8")
CLASS_SOURCE = ANSWER.split("```python\n", 1)[1].split("```", 1)[0]
FAILURE_SOURCE = ANSWER.split("```python\n", 2)[2].split("```", 1)[0]


def check_version():
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.table import Column
    from rich.text import Text

    namespace = {}
    exec(compile(ast.parse(CLASS_SOURCE), "answer-example", "exec"), namespace)
    outcome = namespace["OutcomeSpinnerColumn"]
    cases = []
    captures = {}

    for terminal in (False, True):
        mode = "terminal" if terminal else "plain"
        stream = io.StringIO()
        clock = [100.0]
        console = Console(file=stream, force_terminal=terminal, color_system="standard", width=100)
        finished_text = "[green]✓" if terminal else "-"
        spinner = outcome(finished_text=finished_text)
        progress = Progress(
            spinner,
            TextColumn("{task.description}", table_column=Column(ratio=1)),
            TextColumn("{task.fields[command]}", table_column=Column(ratio=2)),
            console=console,
            get_time=lambda: clock[0],
            auto_refresh=False,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        partial = progress.add_task("partial-failure", total=10, completed=4, command="first command")
        finished = progress.add_task("finished-failure", total=1, command="second command")
        success = progress.add_task("success", total=1, command="third command")
        active = progress.add_task("active", total=None, command="fourth command")
        progress.update(finished, completed=1)
        progress.update(success, completed=1)
        tasks = {task.id: task for task in progress.tasks}
        before_success = spinner.render(tasks[success]).plain
        before_active = spinner.render(tasks[active]).plain
        assert before_success == ("✓" if terminal else "-")
        assert before_active != "x"
        clock[0] += 2.0
        for task_id in (partial, finished):
            exec(compile(FAILURE_SOURCE, "answer-failure-example", "exec"), {"progress": progress, "task_id": task_id})
        console.print(progress)
        first_capture = stream.getvalue()
        captures[mode + "-mixed"] = first_capture

        # Inspect the composed table, not only the overridden method's return.
        plain_stream = io.StringIO()
        plain_console = Console(file=plain_stream, width=100, color_system=None)
        plain_console.print(progress)
        lines = plain_stream.getvalue().splitlines()
        assert len(lines) == 4, lines
        expected = {"partial-failure": "x", "finished-failure": "x", "success": before_success}
        for name, marker in expected.items():
            line = next(line for line in lines if name in line)
            assert line.split()[0] == marker, line
        assert next(line for line in lines if "active" in line).split()[0] != "x"
        assert all(command in plain_stream.getvalue() for command in ("first command", "second command", "third command", "fourth command"))
        cases.append(mode + ":mixed rows keep independent outcomes")

        assert tasks[partial].completed == 4 and tasks[partial].total == 10
        assert not tasks[partial].finished and not progress.finished
        frozen_elapsed = tasks[partial].elapsed
        active_elapsed = tasks[active].elapsed
        clock[0] += 5.0
        assert tasks[partial].elapsed == frozen_elapsed
        assert tasks[active].elapsed == active_elapsed + 5.0
        cases.append(mode + ":stop freezes only failed elapsed time without completing it")

        assert spinner.render(tasks[success]).plain == before_success
        assert tasks[finished].finished and spinner.render(tasks[finished]).plain == "x"
        assert spinner.finished_text.plain == before_success
        cases.append(mode + ":failed marker takes precedence after normal completion")

        segments = list(console.render(spinner.render(tasks[partial])))
        marked = [segment for segment in segments if "x" in segment.text]
        assert marked and all(segment.style.color.name == "red" for segment in marked)
        if terminal:
            assert "\x1b[31mx\x1b[0m" in first_capture, repr(first_capture)
        else:
            assert "\x1b" not in first_capture
        cases.append(mode + ":red marker in color mode and readable plain output")

        # Negative controls: an ordinary column ignores the per-task field, and
        # mutating the shared column changes the unrelated successful row too.
        ordinary = SpinnerColumn(finished_text=finished_text)
        assert ordinary.render(tasks[partial]).plain != "x"
        assert ordinary.render(tasks[finished]).plain == before_success
        ordinary.finished_text = Text("x", style="red")
        assert ordinary.render(tasks[success]).plain == "x"
        cases.append(mode + ":ordinary/global-column controls distinguish the solution")

        # Exercise the actual Live context, without a background refresh thread.
        stream.seek(0)
        stream.truncate()
        with progress:
            progress.update(active, description="still-active", refresh=True)
            assert progress.live.is_started
        assert not progress.live.is_started
        final_capture = stream.getvalue()
        assert "partial-failure" in final_capture and "still-active" in final_capture
        assert "x" in final_capture
        assert spinner.render(tasks[success]).plain == before_success
        captures[mode + "-live"] = final_capture
        cases.append(mode + ":Live final rendering retains independent failed/success rows")

    result = {
        "version": importlib.metadata.version("rich"),
        "python": sys.version,
        "platform": sys.platform,
        "answerSha256": hashlib.sha256(ANSWER.encode()).hexdigest(),
        "classSourceSha256": hashlib.sha256(CLASS_SOURCE.encode()).hexdigest(),
        "failureSourceSha256": hashlib.sha256(FAILURE_SOURCE.encode()).hexdigest(),
        "cases": cases,
        "caseCount": len(cases),
        "captures": captures,
        "terminalEmulatorTested": False,
        "dependencies": {name: importlib.metadata.version(name) for name in ("rich", "pygments", "markdown-it-py", "mdurl")},
    }
    output = REPORTS / ("rich-" + result["version"] + ".json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"version": result["version"], "platform": result["platform"], "casesPassed": len(cases)}))


if sys.argv[-1] == "--child":
    check_version()
else:
    for version in ("13.9.4", "15.0.0"):
        environment = Path("runner-environments") / version
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        with (REPORTS / ("install-" + version + ".log")).open("w", encoding="utf-8") as log:
            subprocess.run([str(python), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", "rich==" + version, "pygments==2.19.2", "markdown-it-py==4.0.0", "mdurl==0.1.2"], stdout=log, stderr=subprocess.STDOUT, timeout=300, check=True)
        subprocess.run([str(python), "-B", "-X", "utf8", str(Path(__file__).resolve()), "--child"], timeout=120, check=True)
