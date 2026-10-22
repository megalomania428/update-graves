import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
const prefix = resolve(process.env.COMPACT_CONTEXT_RUNTIME_NODE_MODULES ??
  '/home/coder/.local/pi-dev/node_modules');
const codingDir = join(prefix, '@earendil-works/pi-coding-agent');
const codingPackage = join(codingDir, 'package.json');
assert.equal(JSON.parse(await readFile(codingPackage, 'utf8')).version, '0.85.1');
assert.equal(process.env.PI_OFFLINE, '1', 'Run with the documented offline command');
const require = createRequire(codingPackage);
const { createJiti } = require('jiti');
const jiti = createJiti(import.meta.url, {
  fsCache: false, moduleCache: false,
  alias: { typebox: require.resolve('typebox') },
});
const directory = dirname(fileURLToPath(import.meta.url));
const tests = await jiti.import(join(directory, 'compact-context.test.ts'));
await tests.runTests();
