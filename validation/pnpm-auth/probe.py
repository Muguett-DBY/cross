"""Run the requested pnpm regressions only on a disposable hosted Linux runner."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

assert sys.platform == 'linux' and os.environ.get('GITHUB_ACTIONS') == 'true'
workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
source = workspace / 'source'
payload = workspace / 'validation/pnpm-auth'
reports = workspace / 'reports'
reports.mkdir()
base = '3c91a90df917386441a3d373d6f3e52836d25849'
paths = ['lib/modules/manager/npm/post-update/pnpm.ts',
         'lib/modules/manager/npm/post-update/pnpm.spec.ts']
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip() == base
original_production = (source / paths[0]).read_bytes()
env = dict(os.environ, HUSKY='0', FORCE_COLOR='0', CI='true')


def run(args, label, timeout=900):
    started = time.monotonic()
    with (reports / (label + '.log')).open('w', encoding='utf-8') as log:
        result = subprocess.run(args, cwd=source, env=env, stdout=log,
                                stderr=subprocess.STDOUT, timeout=timeout)
    print(json.dumps({'phase': label, 'exitCode': result.returncode,
                      'durationSeconds': round(time.monotonic() - started, 2)}), flush=True)
    return result.returncode


assert run(['pnpm', 'install', '--frozen-lockfile'], 'dependency-install') == 0
for path in paths:
    shutil.copyfile(payload / path, source / path)
fixed_status = run(['pnpm', 'check', '--all', *paths, '--coverage-dir',
                    str(reports / 'fixed-coverage')], 'fixed-scoped-check')
formatted = {path: (source / path).read_bytes() for path in paths}
for path, content in formatted.items():
    target = reports / 'verified-source' / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
fixed_log = (reports / 'fixed-scoped-check.log').read_text(encoding='utf-8')
strip_ansi = lambda text: re.sub(r'\x1b\[[0-9;]*[mK]', '', text)
fixed_text = strip_ansi(fixed_log)
passed = re.search(r'Tests\s+(\d+) passed', fixed_text)

try:
    (source / paths[0]).write_bytes(original_production)
    baseline_status = run(['pnpm', 'check', *paths, '--coverage-dir',
                           str(reports / 'baseline-coverage')], 'baseline-countercontrol')
    baseline_text = strip_ansi((reports / 'baseline-countercontrol.log').read_text(encoding='utf-8'))
finally:
    (source / paths[0]).write_bytes(formatted[paths[0]])

failures = re.search(r'Tests\s+(\d+) failed', baseline_text)
expected_failures = ['passes Git authentication for api.github.com to pnpm',
                     'passes Git authentication for https://github.com/org/private.git to pnpm',
                     'appends authentication without replacing approved Git configuration',
                     'passes Git authentication into the pnpm Docker container']
causal = baseline_status != 0 and failures is not None and int(failures[1]) == 4
causal = causal and all(name in baseline_text for name in expected_failures)
coverage_file = reports / 'fixed-coverage/coverage-final.json'
coverage = None
if coverage_file.exists():
    all_coverage = json.loads(coverage_file.read_text())
    module_coverage = next(value for key, value in all_coverage.items() if key.endswith('/' + paths[0]))
    coverage = {}
    for kind in ['s', 'f', 'b']:
        values = list(module_coverage[kind].values())
        hits = [hit for value in values for hit in (value if isinstance(value, list) else [value])]
        coverage[kind] = {'covered': sum(hit > 0 for hit in hits), 'total': len(hits)}

changed = subprocess.check_output(['git', 'diff', '--name-only'], cwd=source, text=True).splitlines()
errors = []
if fixed_status or not passed:
    errors.append('patched scoped checks did not pass')
if not causal:
    errors.append('baseline did not produce exactly the four expected regression failures')
if sorted(changed) != sorted(paths):
    errors.append('unexpected changed source paths: ' + repr(changed))
result = {
    'checkedAtUtc': datetime.now(timezone.utc).isoformat(), 'sourceBase': base,
    'node': subprocess.check_output(['node', '--version'], text=True).strip(),
    'pnpm': subprocess.check_output(['pnpm', '--version'], text=True).strip(),
    'requiredScopedCheckPassed': fixed_status == 0,
    'scopedTestsPassed': int(passed[1]) if passed else None,
    'baselineCountercontrolPassed': causal,
    'baselineExpectedRegressionFailures': 4 if causal else None,
    'moduleCoverage': coverage,
    'verifiedSourceSha256': {path: hashlib.sha256(content).hexdigest() for path, content in formatted.items()},
    'sourceChangedOnlyExpectedPaths': sorted(changed) == sorted(paths),
    'subprocessAndDockerExecutionMocked': True,
    'privateRepositoryUsed': False, 'fullProjectTypeCheckRun': False,
    'fullRenovateSuiteRun': False, 'userComputerPackagesInstalled': False,
    'errors': errors,
}
(reports / 'verified.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result), flush=True)
if errors:
    for label in ['fixed-scoped-check', 'baseline-countercontrol']:
        print((reports / (label + '.log')).read_text(encoding='utf-8')[-13000:], flush=True)
assert not errors, errors
