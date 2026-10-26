"""Prepare one consistent lockfile for the two existing dependency PRs, on CI only."""
import hashlib,json,os,subprocess,time
from pathlib import Path
assert os.environ.get("GITHUB_ACTIONS")=="true"
ROOT=Path.cwd();SRC=ROOT/"work/photography";OUT=ROOT/"reports"
SRC.parent.mkdir(parents=True,exist_ok=True);OUT.mkdir(exist_ok=True)
TOOLING="f44185e69b865653d20f5b497023fd9d633495c0"
RUNTIME="f99c824629b824f9d43c431018724d7e81c387cc"
ENV=dict(os.environ,CI="true",WRANGLER_SEND_METRICS="false",NPM_CONFIG_CACHE=str(ROOT/"work/npm-cache"))
report={"toolingHead":TOOLING,"runtimeHead":RUNTIME,"commands":{},"allGenerationChecksPassed":False,"noFullBrowserSuiteClaimed":True}
def save():(OUT/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
def run(name,args,cwd=SRC,timeout=600):
    log=OUT/(name+".log")
    try:
        with log.open("wb") as stream:
            p=subprocess.run(args,cwd=cwd,env=ENV,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
    except subprocess.TimeoutExpired:
        report["commands"][name]={"args":args,"exitCode":124,"timedOut":True};save();raise
    output=log.read_bytes();report["commands"][name]={"args":args,"exitCode":p.returncode};save();print(name,p.returncode,flush=True)
    assert p.returncode==0,(name,output.decode(errors="replace")[-3000:])
    return output
run("clone",["git","clone","--depth=1","https://github.com/Muguett-DBY/naihuangbao-photography.git",str(SRC)],cwd=ROOT)
for key,sha in [("tooling",TOOLING),("runtime",RUNTIME)]:run("fetch-"+key,["git","fetch","--depth=1","origin",sha])
run("checkout",["git","checkout","--detach",TOOLING])
assert run("head-readback",["git","rev-parse","HEAD"]).decode().strip()==TOOLING
package=json.loads((SRC/"package.json").read_text())
runtime=json.loads(run("runtime-manifest",["git","show",RUNTIME+":package.json"]))
runtime_lock=json.loads(run("runtime-lockfile",["git","show",RUNTIME+":package-lock.json"]))
runtime_versions={name:runtime_lock["packages"]["node_modules/"+name]["version"] for name in ["@remotion/player","remotion","framer-motion","i18next","lucide-react","react-i18next"]}
changes={name:version for name,version in runtime["dependencies"].items() if package["dependencies"].get(name)!=version}
assert set(changes)=={"@remotion/player","framer-motion","i18next","lucide-react","react-i18next"},changes
package["dependencies"].update(changes)
package.setdefault("overrides",{})["miniflare"]={"undici":"^7.30.1"}
(SRC/"package.json").write_text(json.dumps(package,indent=2)+"\n",encoding="utf-8")
report["productionManifestUpdates"]=changes
run("lockfile-security-fix",["npm","audit","fix","--package-lock-only","--ignore-scripts"])
run("preserve-runtime-batch",["npm","install","--package-lock-only","--ignore-scripts","--save-prefix=^",*[name+"@"+version for name,version in runtime_versions.items()]])
report["runtimeBatchVersionsPreserved"]=runtime_versions
run("clean-install",["npm","ci"])
report["audit"]=json.loads(run("audit",["npm","audit","--json"]))
assert report["audit"]["metadata"]["vulnerabilities"]["total"]==0
lock=json.loads((SRC/"package-lock.json").read_text())
report["resolvedVersions"]={name:lock["packages"]["node_modules/"+name]["version"] for name in ["wrangler","undici","fast-uri","serialize-javascript","remotion","@remotion/player","framer-motion","i18next","lucide-react","react-i18next"]}
assert all(report["resolvedVersions"][name]==version for name,version in runtime_versions.items())
assert report["resolvedVersions"]["wrangler"]=="4.152.0"
assert report["resolvedVersions"]["undici"].startswith("7.")
assert report["resolvedVersions"]["remotion"]==report["resolvedVersions"]["@remotion/player"]
config=ROOT/"validation/photography-dependencies/vite.config.ts"
(SRC/"vite.config.ts").write_bytes(config.read_bytes())
run("generate-worker-types",["npm","run","cf:types"])
run("check-worker-types",["npm","run","cf:types:check"])
run("lint",["npm","run","lint"])
run("build",["npm","run","build:full"])
run("local-migrations",["npm","run","db:migrate:check"])
run("browser-install",["npx","--no-install","playwright","install","--with-deps","chromium"])
run("project-package-browser-smoke",["npx","--no-install","playwright","test","-c","e2e/playwright.config.ts","e2e/visual-os-v8.spec.ts","--project=default","--grep","同 ID"],timeout=240)
run("diff-check",["git","diff","--check","--","package.json","package-lock.json","worker-configuration.d.ts","vite.config.ts"])
paths=["package.json","package-lock.json","worker-configuration.d.ts","vite.config.ts"]
report["sourceSha256"]={}
for name in paths:
    target=OUT/"source"/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((SRC/name).read_bytes());report["sourceSha256"][name]=hashlib.sha256(target.read_bytes()).hexdigest()
report["diff"]=run("repair-diff",["git","diff","--",*paths]).decode()
report["allGenerationChecksPassed"]=True;save();print(json.dumps({"allGenerationChecksPassed":True,"resolvedVersions":report["resolvedVersions"]}))
