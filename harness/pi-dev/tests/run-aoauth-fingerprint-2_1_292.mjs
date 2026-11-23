// cspell:ignore oneline worktrees
import assert from 'node:assert/strict';
import { readFile, realpath } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const prefix = resolve(process.env.AOAUTH_RUNTIME_NODE_MODULES ??
  '/home/coder/.local/pi-dev/node_modules');
const codingDir = join(prefix, '@earendil-works/pi-coding-agent');
const require = createRequire(join(codingDir, 'package.json'));
const corePackage = require.resolve('@earendil-works/pi-agent-core/package.json');
assert.equal(await realpath(dirname(corePackage)), await realpath(
  join(codingDir, 'node_modules/@earendil-works/pi-agent-core'),
));
const aiDir = join(codingDir, 'node_modules/@earendil-works/pi-ai');
const { createJiti } = require('jiti');
const jiti = createJiti(import.meta.url, {
  fsCache: false,
  moduleCache: false,
  alias: {
    '@earendil-works/pi-coding-agent': join(codingDir, 'dist/index.js'),
    '@earendil-works/pi-ai': join(aiDir, 'dist/compat.js'),
  },
});
const directory = dirname(fileURLToPath(import.meta.url));
const fingerprint = await jiti.import(join(
  directory, '../extensions/aoauth-fingerprint-2_1_292.ts'
));
const moveFactory = await jiti.import(join(
  directory, '../extensions/move-reminders-2_1_292.ts'
), { default: true });
const expectedBetas = {
  'claude-sonnet-5': [
    'claude-code-20250219',
    'oauth-2025-04-20',
    'interleaved-thinking-2025-05-14',
    'thinking-token-count-2026-05-13',
    'context-management-2025-06-27',
    'prompt-caching-scope-2026-01-05',
    'mid-conversation-system-2026-04-07',
    'effort-2025-11-24',
    'dangerous-tool-use-2026-09-03',
    'thinking-display-updates-2026-08-18',
    'afk-mode-2026-01-31',
    'extended-cache-ttl-2025-04-11',
  ],
  'claude-opus-5-5': [
    'claude-code-20250219',
    'oauth-2025-04-20',
    'interleaved-thinking-2025-05-14',
    'thinking-token-count-2026-05-13',
    'context-management-2025-06-27',
    'prompt-caching-scope-2026-01-05',
    'mid-conversation-system-2026-04-07',
    'per-turn-control-2026-07-01',
    'mid-conversation-tool-changes-2026-07-01',
    'effort-2025-11-24',
    'dangerous-tool-use-2026-09-03',
    'thinking-display-updates-2026-08-18',
    'afk-mode-2026-01-31',
    'extended-cache-ttl-2025-04-11',
  ],
  'claude-opus-5': [
    'claude-code-20250219',
    'oauth-2025-04-20',
    'interleaved-thinking-2025-05-14',
    'thinking-token-count-2026-05-13',
    'context-management-2025-06-27',
    'prompt-caching-scope-2026-01-05',
    'mid-conversation-system-2026-04-07',
    'mid-conversation-tool-changes-2026-07-01',
    'effort-2025-11-24',
    'dangerous-tool-use-2026-09-03',
    'thinking-display-updates-2026-08-18',
    'afk-mode-2026-01-31',
    'extended-cache-ttl-2025-04-11',
  ],
  'claude-opus-4-8': [
    'claude-code-20250219',
    'oauth-2025-04-20',
    'interleaved-thinking-2025-05-14',
    'thinking-token-count-2026-05-13',
    'context-management-2025-06-27',
    'prompt-caching-scope-2026-01-05',
    'mid-conversation-system-2026-04-07',
    'mid-conversation-tool-changes-2026-07-01',
    'effort-2025-11-24',
    'dangerous-tool-use-2026-09-03',
    'thinking-display-updates-2026-08-18',
    'afk-mode-2026-01-31',
    'extended-cache-ttl-2025-04-11',
  ],
  'claude-opus-4-7': [
    'claude-code-20250219',
    'oauth-2025-04-20',
    'interleaved-thinking-2025-05-14',
    'thinking-token-count-2026-05-13',
    'context-management-2025-06-27',
    'prompt-caching-scope-2026-01-05',
    'effort-2025-11-24',
    'dangerous-tool-use-2026-09-03',
    'thinking-display-updates-2026-08-18',
    'afk-mode-2026-01-31',
    'extended-cache-ttl-2025-04-11',
  ],
};
const handlers = {};
moveFactory({
  on(name, handler) {
    handlers[name] = handler;
  },
  async exec(_command, args) {
    const key = args.join(' ');
    if (key === 'rev-parse --is-inside-work-tree') {
      return { code: 0, stdout: 'true\n' };
    }
    if (key === 'rev-parse --show-toplevel') {
      return { code: 0, stdout: '/workspace/project\n' };
    }
    if (key === 'rev-parse --git-dir --git-common-dir') {
      return { code: 0, stdout: '/repo/.git/worktrees/test\n/repo/.git\n' };
    }
    if (key === 'symbolic-ref --quiet --short HEAD') {
      return { code: 0, stdout: 'feature\n' };
    }
    if (key === 'show-ref --verify --quiet refs/heads/master') {
      return { code: 0, stdout: '' };
    }
    if (key === 'config user.name') return { code: 0, stdout: 'Tester\n' };
    if (key === '--no-optional-locks status --short') {
      return { code: 0, stdout: '' };
    }
    if (key === '--no-pager log -5 --oneline') {
      return { code: 0, stdout: '1234567 test\n' };
    }
    return { code: 1, stdout: '' };
  },
});
const cwd = resolve(directory, '../../..');
const context = (model) => ({
  cwd,
  model: { id: model, provider: 'anthropic', name: model.replace('claude-', '') },
  modelRegistry: { getProviderDisplayName: () => 'Anthropic' },
});
await handlers.session_start({}, context('claude-sonnet-5'));
handlers.before_agent_start({ systemPromptOptions: {} });
for (const [model, betas] of Object.entries(expectedBetas)) {
  const moved = await handlers.before_provider_request({
    payload: {
      messages: [
        { role: 'user', content: [{ type: 'text', text: 'Reply with one word: hello' }] },
        {
          role: 'system',
          content: [],
          output_config: { effort: 'medium' },
        },
      ],
      thinking: {
        type: 'adaptive',
        display: 'summarized',
        block_binding: { prefix_mismatch_behavior: 'drop_block' },
      },
    },
  }, context(model));
  const output = fingerprint.applyFingerprintPayload(moved, model);
  assert.deepEqual(output.betas, betas, `${model} betas`);
  assert.deepEqual(output.thinking, { type: 'adaptive', display: 'updates' });
  assert.equal(output.system[0].text,
    'x-anthropic-billing-header: cc_version=2.1.292.544; cc_entrypoint=cli;');
  assert.equal(output.system.length, 3);
  assert.deepEqual(output.system[1].cache_control, { type: 'ephemeral', ttl: '1h' });
  assert.match(output.system.at(-1).text, /claude-sonnet-5-5/u);
  const user = output.messages[0];
  assert.equal(user.role, 'user');
  assert.equal(user.content.at(-1).text, 'Reply with one word: hello');
  assert.equal(user.content.some((block) => (
    /Codebase and user instructions/u.test(block.text)
  )), true);
  const tail = output.messages.at(-1);
  if (model === 'claude-opus-4-7') {
    assert.equal(output.messages.length, 1);
    assert.match(user.content[0].text, /# Environment/u);
  } else {
    assert.equal(tail.role, 'system');
    assert.match(tail.content[0].text, /Downloaded files and extracted archives/u);
    assert.match(tail.content[0].text, /This is a git worktree/u);
    assert.equal('output_config' in tail, model === 'claude-opus-5-5');
  }
}
assert.equal(fingerprint.buildHashFor(
  '2.1.292', 'Reply with one word: hello'
), '544');
const headers = { 'X-CLIENT-REQUEST-ID': 'stale' };
fingerprint.applyFingerprintHeaders(headers, 'test-session');
assert.equal(headers['user-agent'], 'claude-cli/2.1.292 (external, cli)');
assert.equal(headers['X-Stainless-Package-Version'], '0.128.0');
assert.equal(headers['X-Stainless-Runtime-Version'], 'v26.3.0');
assert.equal(headers['X-Claude-Code-Session-Id'], 'test-session');
assert.equal(Object.keys(headers).some((name) => (
  name.toLowerCase() === 'x-client-request-id'
)), false);
const staticPrompt = await readFile(join(
  directory, '../reminders/cc-2_1_292-p1/90sys-opus.md'
), 'utf8');
assert.match(staticPrompt, /Sonnet 5\.5: 'claude-sonnet-5-5'/u);
console.log('PASS: Claude Code 2.1.292 headers, betas, payloads, and prompt routes');
