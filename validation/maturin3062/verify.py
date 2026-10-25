import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

triple = sys.argv[1]
expected_arch = "i686" if triple.startswith("i686") else "x86_64"
results = {}
for directory in ("old-find", "new-find"):
    wheels = sorted(Path(directory).glob("*.whl"))
    assert wheels, directory
    entries = []
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            wheel_info = next(n for n in archive.namelist() if n.endswith(".dist-info/WHEEL"))
            tags = [line[5:] for line in archive.read(wheel_info).decode().splitlines() if line.startswith("Tag: ")]
        assert tags and all(tag.endswith(expected_arch) for tag in tags), tags
        free_threaded = any("-cp314t-" in tag for tag in tags)
        interpreter = "/opt/python/cp314-cp314t/bin/python" if free_threaded else "/opt/python/cp314-cp314/bin/python"
        with tempfile.TemporaryDirectory() as installation:
            subprocess.run([interpreter, "-m", "pip", "install", "--no-deps", "--no-cache-dir", "--target", installation, str(wheel)], check=True)
            code = "import sys;sys.path.insert(0, " + repr(installation) + ");import maturin3062_probe;assert maturin3062_probe.answer()==42;assert sys._is_gil_enabled() is " + str(not free_threaded)
            subprocess.run([interpreter, "-c", code], check=True)
        entries.append({"filename":wheel.name,"tags":tags,"freeThreaded":free_threaded,"importAndCallPassed":True,"gilRemainedDisabled":free_threaded})
    results[directory] = entries
# Record observed results rather than assuming that the original failure reproduces.
report={"target":triple,"pyo3":"0.28.2","stableAbiEnabled":False,"requiresPython":">=3.14,<3.15","oldVersion":"1.12.6","newVersion":"1.15.0","bothInterpretersOnPath":True,"wheels":results,"cargoLockSha256":hashlib.sha256(Path("Cargo.lock").read_bytes()).hexdigest()}
Path("probe-result.json").write_text(json.dumps(report,indent=2)+"\n")
print("MATURIN3062_RESULT="+json.dumps(report,sort_keys=True))
