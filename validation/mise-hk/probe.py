"""Validate one real reported backend case on a disposable Linux runner only."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import datetime, timezone

assert sys.platform == 'linux', 'Never install validation dependencies on the user computer'
assert os.environ.get('GITHUB_ACTIONS') == 'true'
workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
source = workspace / 'source'
payload = workspace / 'validation/mise-hk'
reports = workspace / 'reports'
reports.mkdir()
scratch = Path(os.environ['RUNNER_TEMP']).resolve() / 'mise-hk-validation'
scratch.mkdir()
env = dict(os.environ, HUSKY='0', FORCE_COLOR='0', CI='true')
paths = ['lib/modules/manager/mise/' + name for name in [
    'artifacts.ts', 'artifacts.spec.ts', 'readme.md', 'update-locked.ts', 'update-locked.spec.ts', 'schema.ts']]
original_production = (source / paths[0]).read_bytes()
original_files = {relative: (source/relative).read_bytes() for relative in paths}
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip() == 'dafa4dcf670599c9573abf850ef46570e99dcabe'


def run(args, label, cwd=source, timeout=600):
    start = time.monotonic()
    log = reports / (label + '.log')
    with log.open('w', encoding='utf-8') as handle:
        try:
            process = subprocess.run(args, cwd=cwd, env=env, stdout=handle,
                                     stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            print(json.dumps({'phase': label, 'timedOut': True}), flush=True)
            raise
    print(json.dumps({'phase': label, 'exitCode': process.returncode,
                      'durationSeconds': round(time.monotonic()-start, 2)}), flush=True)
    if process.returncode:
        print(log.read_text(encoding='utf-8', errors='replace')[-10000:], flush=True)
    return process.returncode


assert run(['pnpm', 'install', '--frozen-lockfile'], 'dependency-install', timeout=900) == 0
archive = scratch / 'mise.tar.gz'
url = 'https://github.com/jdx/mise/releases/download/v2026.10.18/mise-v2026.10.18-linux-x64.tar.gz'
with urllib.request.urlopen(url, timeout=120) as response, archive.open('wb') as output:
    shutil.copyfileobj(response, output)
assert hashlib.sha256(archive.read_bytes()).hexdigest() == '0d41b60745a6273a80089c86f80d8729042d2f57f87fda70aecc7d1c3ffbd3be'
bin_dir = scratch / 'bin'
bin_dir.mkdir()
mise = bin_dir / 'mise'
with tarfile.open(archive, 'r:gz') as bundle:
    members = [m for m in bundle.getmembers() if m.name.lstrip('./') == 'mise/bin/mise' and m.isfile()]
    assert len(members) == 1
    mise.write_bytes(bundle.extractfile(members[0]).read())
mise.chmod(0o700)
env['PATH'] = str(bin_dir) + os.pathsep + env['PATH']
for key in ['MISE_DATA_DIR', 'MISE_CACHE_DIR', 'MISE_CONFIG_DIR', 'MISE_STATE_DIR']:
    env[key] = str(scratch / key.lower())
env.update(MISE_GLOBAL_CONFIG_FILE=str(scratch / 'empty-global.toml'),
           MISE_BACKENDS_HK='aqua:jdx/hk', MISE_BACKENDS_JQ='aqua:jqlang/jq',
           MISE_YES='1', MISE_COLOR='0', MISE_SAFE='1')
assert run(['mise', 'version'], 'mise-version') == 0

fixture = scratch / 'fixture'
fixture.mkdir()
config = '[tools]\nhk = "latest"\njq = "1.8.1"\n'
(fixture / 'mise.toml').write_text(config)
platforms = 'linux-x64,linux-x64-musl,linux-arm64,linux-arm64-musl,macos-arm64,windows-x64'
assert run(['mise', 'lock', '--platform', platforms, '--bump', 'hk@2.5.0', 'jq@1.8.1'], 'seed-lock', fixture, 300) == 0
assert (fixture / 'mise.toml').read_text() == config
for args in [['git', 'init', '-b', 'main'], ['git', 'add', 'mise.toml', 'mise.lock'],
             ['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
              'commit', '-m', 'Seed reported hk metadata case']]:
    assert run(args, 'seed-git-' + args[1], fixture) == 0
shutil.copyfile(fixture/'mise.toml', reports/'fixture-mise.toml')
shutil.copyfile(fixture/'mise.lock', reports/'fixture-mise.lock')
reference = scratch / 'reference'
reference.mkdir()
for name in ['mise.toml', 'mise.lock']:
    shutil.copyfile(fixture / name, reference / name)
assert run(['mise', 'lock', '--bump', 'hk@2.6.0'], 'reference-lock', reference, 300) == 0
assert (reference / 'mise.toml').read_text() == config
shutil.copyfile(reference/'mise.lock', reports/'reference-mise.lock')
latest_fixture = scratch / 'fixture-latest-binding'
latest_fixture.mkdir()
latest_lock = (fixture/'mise.lock').read_text()
assert latest_lock.count('specifiers = ["2.5.0"]') == 1
latest_lock = latest_lock.replace('specifiers = ["2.5.0"]', 'specifiers = ["latest"]', 1)
(latest_fixture/'mise.toml').write_text(config)
(latest_fixture/'mise.lock').write_text(latest_lock)
for args in [['git', 'init', '-b', 'main'], ['git', 'add', 'mise.toml', 'mise.lock'],
             ['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
              'commit', '-m', 'Model the reported latest request binding without changing platform metadata']]:
    assert run(args, 'latest-seed-git-' + args[1], latest_fixture) == 0
shutil.copyfile(latest_fixture/'mise.lock', reports/'fixture-latest-binding-mise.lock')
env.pop('MISE_SAFE', None)
probe = payload / 'probe.mjs'
errors = []
# Check the complete candidate first; keep independent runtime observations even
# if a formatting/test problem needs repair, rather than stopping at one signal.
for relative in paths:
    shutil.copyfile(payload/'source'/relative, source/relative)
fixed_check = run(['pnpm', 'check', '--all', 'lib/modules/manager/mise', '--coverage-dir', str(reports/'fixed-coverage')],
                  'fixed-scoped-check', timeout=600)
if fixed_check:
    errors.append('fixed scoped check failed')
for relative in paths:
    target = reports/'formatted-source'/relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source/relative, target)
cases = []
for variant in ['old', 'fixed']:
    for relative in paths:
        target = source / relative
        if variant == 'fixed':
            shutil.copyfile(reports/'formatted-source'/relative, target)
        else:
            target.write_bytes(original_files[relative])
    for binding, seed in [('exact', fixture), ('latest', latest_fixture)]:
        for mode in ['allowlisted', 'safe']:
            label = variant + '-' + binding + '-' + mode
            out = reports / (label + '.json')
            status = run(['node', str(probe), str(source), str(seed), str(scratch/label),
                          str(reference/'mise.lock'), str(out), variant, mode, binding], label, timeout=300)
            if status:
                errors.append(label+' failed')
            if out.exists():
                cases.append(json.loads(out.read_text()))
fixed_production = (source / paths[0]).read_bytes()
try:
    (source / paths[0]).write_bytes(original_production)
    status = run(['pnpm', 'check', 'lib/modules/manager/mise', '--coverage-dir', str(reports/'old-coverage')],
                 'old-scoped-countercontrol', timeout=600)
    text = re.sub(r'\x1b\[[0-9;]*[mK]', '', (reports/'old-scoped-countercontrol.log').read_text())
    expected_control = status != 0 and '7 failed' in text and \
        'regenerates aqua platform metadata after a version-only edit' in text and \
        'regenerates all selected tools in a mixed local-environment update' in text
    if not expected_control:
        errors.append('unexpected old artifact-module countercontrol result')
finally:
    (source / paths[0]).write_bytes(fixed_production)

changed = subprocess.check_output(['git', 'diff', '--name-only'], cwd=source, text=True).splitlines()
if sorted(changed) != sorted(paths):
    errors.append('unexpected modified paths: '+str(changed))
result = {'checkedAtUtc': datetime.now(timezone.utc).isoformat(),
          'sourceBase': 'dafa4dcf670599c9573abf850ef46570e99dcabe',
          'mise': '2026.10.18', 'node': subprocess.check_output(['node','--version'],text=True).strip(),
          'hostPlatform': sys.platform, 'metadataPlatforms': platforms.split(','),
          'fixtureBindingVariants': ['Mise-generated exact request', 'controlled latest binding, real generated platform metadata unchanged'],
          'latestBindingBaselineIsObservational': True,
          'actualManagerCases': cases, 'requiredScopedCheckPassed': fixed_check == 0,
          'oldArtifactModuleCountercontrolPassed': expected_control,
          'oldDraftRegressionFailures': 7 if expected_control else None,
          'verifiedSourceSha256': {p: hashlib.sha256((source/p).read_bytes()).hexdigest() for p in paths},
          'fullRenovateSuiteExecuted': False, 'foreignPlatformBinariesExecuted': False,
          'userComputerPackagesInstalled': False}
(reports/'verified.json').write_text(json.dumps(result, indent=2)+'\n')
public_status = run(['python3', '-B', str(payload/'public-hk/reproduce.py'), '--renovate', str(source),
                     '--output', str(scratch/'public-reproducer'), '--binding', 'exact', '--safe'],
                    'public-reproducer', timeout=300)
result['publicReproducerPassed'] = public_status == 0
if public_status:
    errors.append('public reproducer failed')
result['errors'] = errors
(reports/'verified.json').write_text(json.dumps(result, indent=2)+'\n')
assert not errors, errors
for relative in paths:
    target = reports/'verified-source'/relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source/relative, target)
print(json.dumps(result), flush=True)
