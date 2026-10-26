"""Run the hk selected-version case with existing tools on a disposable Linux host."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--renovate', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--node', default='node')
parser.add_argument('--binding', choices=['exact', 'latest'], default='exact')
parser.add_argument('--safe', action='store_true')
args = parser.parse_args()
if sys.platform != 'linux':
    parser.error('This hk reproduction has been validated on Linux only.')
root = Path(__file__).resolve().parent
output = args.output.resolve()
if output.exists():
    parser.error('Use a fresh output directory; existing files are never reset.')
output.mkdir(parents=True)
fixture = output/'fixture'
fixture.mkdir()
shutil.copyfile(root/'mise.toml', fixture/'mise.toml')
shutil.copyfile(root/(args.binding+'-mise.lock'), fixture/'mise.lock')
for command in [
    ['git', 'init', '-b', 'main'],
    ['git', 'add', 'mise.toml', 'mise.lock'],
    ['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
     'commit', '-m', 'Initialize the hk fixture'],
]:
    subprocess.run(command, cwd=fixture, check=True, capture_output=True)

allowed = {'PATH', 'LANG', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'GITHUB_TOKEN'}
env = {key:value for key,value in os.environ.items() if key.upper() in allowed}
for key in ['MISE_DATA_DIR', 'MISE_CACHE_DIR', 'MISE_CONFIG_DIR', 'MISE_STATE_DIR']:
    env[key] = str(output/key.lower())
empty = output/'empty-global.toml'
empty.write_text('', encoding='utf-8')
env.update(MISE_GLOBAL_CONFIG_FILE=str(empty), MISE_BACKENDS_HK='aqua:jdx/hk',
           MISE_BACKENDS_JQ='aqua:jqlang/jq', MISE_YES='1', MISE_COLOR='0')
command = [args.node, str(root/'verify.mjs'), str(args.renovate.resolve()),
           str(fixture), str(output/'checkout'), str(root/'reference-mise.lock'),
           str(output/'result.json'), 'fixed', 'safe' if args.safe else 'allowlisted', args.binding]
result = subprocess.run(command, env=env)
print('Checkout, isolated tool state and evidence retained in:', output)
sys.exit(result.returncode)
