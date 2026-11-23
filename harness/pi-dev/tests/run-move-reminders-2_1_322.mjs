import assert from 'node:assert/strict'
import { readFile, realpath } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
const prefix = resolve(process.env.MOVE_TEST_RUNTIME_NODE_MODULES ??
  '/home/coder/.local/pi-dev/node_modules')
const codingDir = join(prefix, '@earendil-works/pi-coding-agent')
const codingPackage = join(codingDir, 'package.json')
const require = createRequire(codingPackage)
const corePackage = require.resolve('@earendil-works/pi-agent-core/package.json')
assert.equal(await realpath(dirname(corePackage)), await realpath(
  join(codingDir, 'node_modules/@earendil-works/pi-agent-core'),
), 'coding-agent must resolve its nested agent-core, not root 1.1.0')
for (const path of [codingPackage, corePackage]) {
  assert.equal(JSON.parse(await readFile(path, 'utf8')).version, '0.85.1')
}
const aiDir = join(codingDir, 'node_modules/@earendil-works/pi-ai')
assert.equal(JSON.parse(await readFile(join(aiDir, 'package.json'), 'utf8')).version,
  '0.85.1')
assert.equal(process.env.PI_OFFLINE, '1', 'Run with the documented offline command')
globalThis.fetch = async () => { throw new Error('Unmocked fetch is forbidden') }
const { createJiti } = require('jiti')
const jiti = createJiti(import.meta.url, {
  fsCache: false, moduleCache: false,
  alias: {
    '@earendil-works/pi-coding-agent': join(codingDir, 'dist/index.js')
  }
})
const testsPath = join(
  dirname(fileURLToPath(import.meta.url)), 'move-reminders-2_1_322.test.ts'
)
await jiti.import(pathToFileURL(testsPath).href)
console.log('PASS: loaded move-reminders model parity tests with nested runtime 0.85.1')
