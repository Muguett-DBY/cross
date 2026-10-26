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
package_path=SRC/"package.json"
package=json.loads(package_path.read_text())
direct_dependencies={key:dict(package[key]) for key in ["dependencies","devDependencies"]}
package.setdefault("overrides",{})["miniflare"]={"undici":"^7.30.1"}
package_path.write_text(json.dumps(package,indent=2)+"\n",encoding="utf-8")
run("lock-security-fix",["npm","audit","fix","--package-lock-only","--ignore-scripts"])
run("install-repaired-lock",["npm","ci"])
package=json.loads(package_path.read_text())
assert all(package[key]==values for key,values in direct_dependencies.items())
lock=json.loads((SRC/"package-lock.json").read_text())
report["repairedTransitiveVersions"]={name:lock["packages"]["node_modules/"+name]["version"] for name in ["undici","fast-uri","serialize-javascript"]}
assert report["repairedTransitiveVersions"]["undici"].startswith("7.")
report["auditAfterRepair"]=json.loads(run("audit-after-repair",["npm","audit","--json"]))
assert report["auditAfterRepair"]["metadata"]["vulnerabilities"]["total"]==0
if KIND=="tooling":
    before=run("types-before",["npm","run","cf:types:check"],expected=(1,))
    assert b"out of date" in before or b"out of date" in (OUT/"types-before.log").read_bytes()
    run("types-generate",["npm","run","cf:types"])
    run("types-after",["npm","run","cf:types:check"])
    patch_paths=["worker-configuration.d.ts","package.json","package-lock.json"]
else:
    run("types-unchanged",["npm","run","cf:types:check"])
    run("baseline-build",["npm","run","build:full"],expected=(1,))
    assert b"Route budget exceeded for src/pages/ProjectsPage.tsx" in (OUT/"baseline-build.log").read_bytes()
    run("baseline-bundle",["npm","run","bundle:analyze"])
    manifest=SRC/"dist/.vite/manifest.json"
    (OUT/"baseline-manifest.json").write_bytes(manifest.read_bytes())
    base_manifest=json.loads(manifest.read_text())
    (OUT/"baseline-main-entry.js").write_bytes((SRC/"dist"/base_manifest["index.html"]["file"]).read_bytes())
    patch_paths=["package.json","package-lock.json"]
config=SRC/"vite.config.ts"; text=config.read_text()
anchor='          groups: [\n'
assert text.count(anchor)==1
navigation = r"""            {
              name: "shared-navigation",
              test: /src[\\/](?:routing[\\/]RoutePreloadProvider\.tsx|components[\\/]shared[\\/]PrefetchLink\.tsx|lib[\\/]view-transition\.ts)$/,
              priority: 110,
            },
"""
config.write_text(text.replace(anchor,anchor+navigation,1),encoding="utf-8")
report["changeRationale"]="Keep the shared prefetch link, context and view-transition state in one small independent chunk instead of folding them into the application entry. Preserve all existing vendor groups and performance limits."
patch_paths.append("vite.config.ts")
report["budgetScriptSha256"]=hashlib.sha256((SRC/"scripts/check-route-budgets.mjs").read_bytes()).hexdigest()
for path in patch_paths:
    output=OUT/"source"/path;output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes((SRC/path).read_bytes())
run("migrations",["npm","run","db:migrate:check"])
run("types-final",["npm","run","cf:types:check"])
run("lint",["npm","run","lint"])
run("unit",["npm","test"])
run("content",["npm","run","law:scan"])
run("build",["npm","run","build:full"])
if KIND=="runtime":
    (OUT/"patched-manifest.json").write_bytes((SRC/"dist/.vite/manifest.json").read_bytes())
built_manifest=json.loads((SRC/"dist/.vite/manifest.json").read_text())
report["navigationChunkFiles"]=[x["file"] for x in built_manifest.values() if "shared-navigation" in x.get("file","")]
assert len(report["navigationChunkFiles"])==1
report["projectsDirectImports"]=built_manifest["src/pages/ProjectsPage.tsx"]["imports"]
(OUT/"final-manifest.json").write_bytes((SRC/"dist/.vite/manifest.json").read_bytes())
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
