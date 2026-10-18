import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
const prefix = resolve(process.env.AOAUTH_RUNTIME_NODE_MODULES ??
  '/home/coder/.local/pi-dev/node_modules');
const codingDir = join(prefix, '@earendil-works/pi-coding-agent');
const codingPackage = join(codingDir, 'package.json');
const require = createRequire(codingPackage);
const corePackage = require.resolve('@earendil-works/pi-agent-core/package.json');
assert.equal(await realpath(dirname(corePackage)), await realpath(
  join(codingDir, 'node_modules/@earendil-works/pi-agent-core'),
), 'coding-agent must resolve its nested agent-core, not root 1.1.0');
for (const path of [codingPackage, corePackage]) {
  assert.equal(JSON.parse(await readFile(path, 'utf8')).version, '0.85.1');
}
const aiDir = join(codingDir, 'node_modules/@earendil-works/pi-ai');
assert.equal(JSON.parse(await readFile(join(aiDir, 'package.json'), 'utf8')).version,
  '0.85.1');
assert.equal(process.env.PI_OFFLINE, '1', 'Run with the documented offline command');
const temporary = await mkdtemp(join(tmpdir(), 'aoauth-summary-test-'));
try {
  for (const key of Object.keys(process.env)) delete process.env[key];
  Object.assign(process.env, {
    HOME: temporary, PI_CODING_AGENT_DIR: join(temporary, 'agent'),
    PI_OFFLINE: '1', PI_TELEMETRY: '0', PI_SKIP_VERSION_CHECK: '1',
  });
  globalThis.fetch = async () => { throw new Error('Unmocked fetch is forbidden'); };
  const sdkURL = pathToFileURL(join(codingDir, 'dist/index.js')).href;
  const facadePath = join(temporary, 'sdk-facade.mjs');
  await writeFile(facadePath, [
    `import * as native from ${JSON.stringify(sdkURL)};`,
    `export * from ${JSON.stringify(sdkURL)};`,
    'export const overrides = {};',
    'export const compact = (...args) =>',
    '  (overrides.compact ?? native.compact)(...args);',
    'export const generateBranchSummary = (...args) =>',
    '  (overrides.generateBranchSummary ?? native.generateBranchSummary)(...args);',
    'export { native };',
    '',
  ].join('\n'));
  const facade = await import(pathToFileURL(facadePath).href);
  const { createJiti } = require('jiti');
  const jiti = createJiti(import.meta.url, {
    fsCache: false, moduleCache: false,
    alias: {
      '@earendil-works/pi-coding-agent': facadePath,
      '@earendil-works/pi-ai': join(aiDir, 'dist/compat.js'),
      '@earendil-works/pi-ai/providers/anthropic': join(aiDir, 'dist/providers/anthropic.js'),
    },
  });
  const directory = dirname(fileURLToPath(import.meta.url));
  const moveDirectory = join(temporary, 'move/extensions');
  const configDirectory = join(temporary, 'move/reminders/cc-2_1_281-p1');
  await mkdir(moveDirectory, { recursive: true });
  await mkdir(configDirectory, { recursive: true });
  const movePath = join(moveDirectory, 'move-reminders-2_1_281.ts');
  await writeFile(movePath, await readFile(
    join(directory, '../extensions/move-reminders-2_1_281.ts'), 'utf8',
  ));
  await writeFile(join(configDirectory, 'move.json'), JSON.stringify({
    reminders: ['Offline reminder'], system: ['Offline agent system'],
  }));
  const moveFactory = await jiti.import(movePath, { default: true });
  const tests = await jiti.import(join(directory, 'aoauth-fingerprint-2_1_281.test.ts'));
  console.log('PASS: installed coding-agent/ai/nested agent-core graph = 0.85.1');
  const { prepareCompaction } = await import(pathToFileURL(
    join(codingDir, 'dist/core/compaction/compaction.js'),
  ).href);
  await tests.runTests(facade.overrides, facade.native, moveFactory, prepareCompaction);
} finally {
  await rm(temporary, { recursive: true, force: true });
}
