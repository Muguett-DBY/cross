"""Run the repository checks on disposable runners only; export the minimal repairs."""
import hashlib, json, os, subprocess, sys, time
from pathlib import Path
assert os.environ.get("GITHUB_ACTIONS") == "true"
KIND=sys.argv[1]
HEADS={"runtime":"f99c824629b824f9d43c431018724d7e81c387cc","tooling":"f44185e69b865653d20f5b497023fd9d633495c0"}
assert KIND in HEADS
ROOT=Path.cwd(); SRC=ROOT/"work/photography"; OUT=ROOT/"reports"
SRC.parent.mkdir(parents=True,exist_ok=True); OUT.mkdir(exist_ok=True)
ENV=dict(os.environ,CI="true",WRANGLER_SEND_METRICS="false",NPM_CONFIG_CACHE=str(ROOT/"work/npm-cache"))
report={"kind":KIND,"baseHead":HEADS[KIND],"commands":{},"localDependenciesInstalled":False,"allChecksPassed":False}
def save(): (OUT/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
def run(name,args,cwd=SRC,expected=(0,),timeout=600):
    started=time.monotonic()
    p=subprocess.run(args,cwd=cwd,env=ENV,capture_output=True,timeout=timeout)
    (OUT/(name+".log")).write_bytes(p.stdout+p.stderr)
    report["commands"][name]={"args":args,"exitCode":p.returncode,"expected":list(expected),"seconds":round(time.monotonic()-started,3)}
    save()
    print(name+": "+str(p.returncode),flush=True)
    assert p.returncode in expected,(name,p.returncode,(p.stdout+p.stderr).decode(errors="replace")[-3000:])
    return p.stdout
run("clone",["git","clone","--depth=1","https://github.com/Muguett-DBY/naihuangbao-photography.git",str(SRC)],cwd=ROOT)
run("fetch-base",["git","fetch","--depth=1","origin",HEADS[KIND]])
run("checkout-base",["git","checkout","--detach",HEADS[KIND]])
assert run("head-readback",["git","rev-parse","HEAD"]).decode().strip()==HEADS[KIND]
run("npm-version",["npm","--version"])
run("node-version",["node","--version"])
run("install",["npm","ci"])
if KIND=="tooling":
    before=run("types-before",["npm","run","cf:types:check"],expected=(1,))
    assert b"out of date" in before or b"out of date" in (OUT/"types-before.log").read_bytes()
    run("types-generate",["npm","run","cf:types"])
    run("types-after",["npm","run","cf:types:check"])
    patch_paths=["worker-configuration.d.ts"]
else:
    run("types-unchanged",["npm","run","cf:types:check"])
    run("baseline-build",["npm","run","build:full"],expected=(1,))
    assert b"Route budget exceeded for src/pages/ProjectsPage.tsx" in (OUT/"baseline-build.log").read_bytes()
    run("baseline-bundle",["npm","run","bundle:analyze"])
    manifest=SRC/"dist/.vite/manifest.json"
    (OUT/"baseline-manifest.json").write_bytes(manifest.read_bytes())
    config=SRC/"vite.config.ts"; text=config.read_text()
    start=text.index('            {\n              name: "icon-vendor",')
    end=text.index('            {\n              name: "immersive-vendor",',start)
    block=text[start:end]; assert block.count('name:')==1 and 'lucide-react' in block
    config.write_text(text[:start]+text[end:],encoding="utf-8")
    report["changeRationale"]="Let the bundler share and split Lucide modules by usage instead of loading every site's used icons through one global vendor chunk. Page budgets are unchanged."
    patch_paths=["vite.config.ts"]
run("migrations",["npm","run","db:migrate:check"])
run("types-final",["npm","run","cf:types:check"])
run("lint",["npm","run","lint"])
run("unit",["npm","test"])
run("content",["npm","run","law:scan"])
run("build",["npm","run","build:full"])
if KIND=="runtime":
    (OUT/"patched-manifest.json").write_bytes((SRC/"dist/.vite/manifest.json").read_bytes())
run("audit",["npm","run","deps:audit"])
run("browser-install",["npx","--no-install","playwright","install","--with-deps","chromium"])
run("browser",["npm","run","test:e2e"],timeout=1200)
run("diff-check",["git","diff","--check","--",*patch_paths])
report["patch"]=run("repair-diff",["git","diff","--",*patch_paths]).decode()
report["sourceSha256"]={}
for path in patch_paths:
    output=OUT/"source"/path;output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes((SRC/path).read_bytes())
    report["sourceSha256"][path]=hashlib.sha256(output.read_bytes()).hexdigest()
report["allChecksPassed"]=True;save()
print(json.dumps({"kind":KIND,"allChecksPassed":True,"patchedFiles":patch_paths}))
