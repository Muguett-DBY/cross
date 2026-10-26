import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

const [source, fixture, work, referenceFile, output, expected, mode, binding] = process.argv
  .slice(2)
  .map((value, index) => (index < 5 ? path.resolve(value) : value));
assert(process.platform === 'linux', 'Disposable Linux runner only');
assert(['old', 'fixed'].includes(expected));
assert(['allowlisted', 'safe'].includes(mode));
assert(['exact', 'latest'].includes(binding));
assert(path.dirname(work) === path.dirname(fixture));
await fs.mkdir(work);
const load = (name) => import(pathToFileURL(path.join(source, name)).href);
const { GlobalConfig } = await load('lib/config/global.ts');
const git = await load('lib/util/git/index.ts');
const { updateArtifacts } = await load('lib/modules/manager/mise/artifacts.ts');
const { updateLockedDependency } = await load('lib/modules/manager/mise/update-locked.ts');
const { Toml } = await load('lib/util/schema-utils/index.ts');
const digest = (value) => createHash('sha256').update(value).digest('hex');

GlobalConfig.set({
  localDir: work,
  cacheDir: path.join(path.dirname(work), 'renovate-cache'),
  binarySource: 'global',
  platform: 'github',
  allowedUnsafeExecutions: mode === 'safe' ? [] : ['mise'],
  exposeAllEnv: true,
  executionTimeout: 5,
});
await git.initRepo({ url: fixture, defaultBranch: 'main', currentBranch: 'main' });
await git.syncGit();
const config = await fs.readFile(path.join(work, 'mise.toml'), 'utf8');
const originalLock = await fs.readFile(path.join(work, 'mise.lock'), 'utf8');
const original = Toml.parse(originalLock);
const reference = Toml.parse(await fs.readFile(referenceFile, 'utf8'));
const expectedEntries = structuredClone(reference.tools.hk);
assert.equal(expectedEntries.length, 1);
expectedEntries[0].specifiers = ['latest'];
assert.equal(original.tools.hk[0].version, '2.5.0');
assert.equal(original.tools.hk[0].backend, 'aqua:jdx/hk');
assert.equal(reference.tools.hk[0].version, '2.6.0');

const edit = updateLockedDependency({
  packageFile: 'mise.toml',
  lockFile: 'mise.lock',
  lockFileContent: originalLock,
  packageFileContent: config,
  depName: 'hk',
  currentVersion: '2.5.0',
  newVersion: '2.6.0',
});
assert.equal(edit.status, 'updated');
assert(edit.files['mise.lock'].includes('/download/v2.5.0/'));
assert.equal(Toml.parse(edit.files['mise.lock']).tools.hk[0].version, '2.6.0');
const result = await updateArtifacts({
  packageFileName: 'mise.toml',
  updatedDeps: [{
    depName: 'hk',
    datasource: 'github-releases',
    currentVersion: '2.5.0',
    newVersion: '2.6.0',
    newValue: 'latest',
  }],
  newPackageFileContent: config,
  newLockFileContent: edit.files['mise.lock'],
  config: {},
});
const actualBytes = await fs.readFile(path.join(work, 'mise.lock'), 'utf8');
const actual = Toml.parse(actualBytes);
const artifacts = result?.filter((item) => item.artifactError) ?? [];
let matchesReference = false;
try {
  assert.deepEqual(actual.tools.hk, expectedEntries);
  matchesReference = true;
} catch (error) {
  assert.equal(error.code, 'ERR_ASSERTION');
}
const hk = actual.tools.hk[0];
const platforms = hk.platforms ?? Object.fromEntries(
  Object.entries(hk)
    .filter(([key]) => key.startsWith('platforms.'))
    .map(([key, value]) => [key.slice('platforms.'.length), value]),
);
const receipt = {
  checkedAtUtc: new Date().toISOString(),
  expected,
  mode,
  fixtureBinding: binding,
  node: process.version,
  platform: process.platform,
  productionSourceSha256: digest(await fs.readFile(path.join(source, 'lib/modules/manager/mise/artifacts.ts'))),
  lockedVersion: hk.version,
  platformEntries: platforms,
  platformCount: Object.keys(platforms).length,
  matchesIndependentExactVersionReference: matchesReference,
  referenceBindingNormalizedToConfiguredSelector: true,
  artifactErrors: artifacts,
  resultFiles: result?.filter((item) => item.file).map(({ file }) => ({
    type: file.type, path: file.path, sha256: file.contents ? digest(file.contents) : null,
  })),
  configUnchanged: config === await fs.readFile(path.join(work, 'mise.toml'), 'utf8'),
  unrelatedToolUnchanged: JSON.stringify(original.tools.jq) === JSON.stringify(actual.tools.jq),
};
await fs.writeFile(output, JSON.stringify(receipt, null, 2) + '\n');
assert(receipt.configUnchanged);
assert(receipt.unrelatedToolUnchanged);
if (expected === 'fixed') {
  assert.equal(artifacts.length, 0, 'Artifact generation must succeed');
  assert(matchesReference, 'Every platform field must match the independently regenerated target');
  assert.equal(receipt.platformCount, 6);
  assert(result.some((item) => item.file?.path === 'mise.lock' && item.file.contents === actualBytes));
  for (const args of [['install', '--locked', 'hk'], ['exec', '--locked', '--', 'hk', '--version'], ['lock', 'hk']]) {
    const check = spawnSync('mise', args, {
      cwd: work, env: { ...process.env, MISE_SAFE: '1' }, encoding: 'utf8', timeout: 180000,
    });
    await fs.appendFile(output + '.commands.log', `${JSON.stringify(args)}\n${check.stdout}\n${check.stderr}\n`);
    assert.equal(check.status, 0, `Post-update ${args.join(' ')} failed: ${check.stderr}`);
    if (args[0] === 'exec') {
      assert.match(check.stdout, /\b2\.6\.0\b/);
      receipt.installedVersionOutput = check.stdout.trim();
    }
  }
  const stable = Toml.parse(await fs.readFile(path.join(work, 'mise.lock'), 'utf8'));
  assert.equal(stable.tools.hk.length, 1);
  assert.equal(stable.tools.hk[0].version, '2.6.0');
  assert(stable.tools.hk[0].specifiers.includes('latest'));
  assert.deepEqual(stable.tools.jq, original.tools.jq);
  assert.equal(config, await fs.readFile(path.join(work, 'mise.toml'), 'utf8'));
  receipt.lockedInstallVerified = true;
  receipt.subsequentPlainRefreshStable = true;
  await fs.writeFile(output, JSON.stringify(receipt, null, 2) + '\n');
} else {
  assert(!matchesReference || artifacts.length > 0, 'The old draft did not reproduce the reported failure');
}
console.log(JSON.stringify(receipt));
