// cspell:ignore worktrees oneline convo
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { afterEach, test } from 'node:test'
import type { ExtensionAPI, ExtensionContext } from '@earendil-works/pi-coding-agent'
import expected from './fixtures/move-322.json'

interface HookEvent {
  payload?: unknown
  systemPromptOptions?: { appendSystemPrompt?: string }
}
type Hook = (event: HookEvent, ctx: ExtensionContext) => unknown
type ExtensionFactory = (pi: ExtensionAPI) => void
interface Message {
  role: string
  content: unknown
  [key: string]: unknown
}
interface PromptBlock {
  type: string
  text?: string
  data?: string
  cache_control?: { type: string, ttl?: string } | null
}
interface Payload {
  messages: Message[]
  system: PromptBlock[]
  tools: unknown
  [key: string]: unknown
}
interface ModelFixture {
  displayName: string
  systemPromptHash: string
  nativeSystemHash: string
}
const modelFixtures = expected.models as Record<string, ModelFixture>
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const movePath = join(root, 'extensions/move-reminders-2_1_322.ts')
const promptDirectory = join(root, 'reminders/cc-2_1_322-p1')
const runtimeRequire = createRequire(
  '/home/coder/.local/pi-dev/node_modules/@earendil-works/pi-coding-agent/package.json'
)
const projectRoot = resolve(root, '../..')
const environmentPrefix = '# Environment\n' +
  'You have been invoked in the following environment:'
const initialArgv = [...process.argv]
const initialHome = process.env.HOME
const initialAgentDir = process.env.PI_CODING_AGENT_DIR
afterEach(() => {
  process.argv = [...initialArgv]
  if (initialHome === undefined) delete process.env.HOME
  else process.env.HOME = initialHome
  if (initialAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR
  else process.env.PI_CODING_AGENT_DIR = initialAgentDir
})
function sha256 (text: string): string {
  return createHash('sha256').update(text).digest('hex')
}
function isRecord (value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
async function createHarness (options: {
  modelId: string
  cwd?: string
  agentDir?: string
  happierPrompt?: string
  appendSystemPrompt?: string
  worktree?: boolean
  billing?: string
  effortHistory?: boolean
  initial?: boolean
  fingerprintOrder?: 'before' | 'after'
  isRepo?: boolean
  globalInstructions?: string
  localInstructions?: string
  payload?: Partial<Payload>
}): Promise<{
    output: Payload
    userBlocks: PromptBlock[]
  }> {
  const oldArgv = [...process.argv]
  const oldHome = process.env.HOME
  const oldAgentDir = process.env.PI_CODING_AGENT_DIR
  const temp = await mkdtemp(join(tmpdir(), 'move-reminders-322-'))
  const home = join(temp, 'home')
  const agentDir = options.agentDir ?? join(temp, 'agent')
  await mkdir(home, { recursive: true })
  if (options.globalInstructions !== undefined) {
    await writeFile(join(home, '.AGENTS.md'), options.globalInstructions)
  }
  const localCwd = join(temp, 'project')
  if (options.localInstructions !== undefined) {
    await mkdir(localCwd, { recursive: true })
    await writeFile(join(localCwd, 'AGENTS.md'), options.localInstructions)
  }
  process.env.HOME = home
  process.env.PI_CODING_AGENT_DIR = agentDir
  const happyPath = join(temp, 'happy.json')
  if (options.happierPrompt !== undefined) {
    await writeFile(happyPath, JSON.stringify({ promptAddition: options.happierPrompt }))
    process.argv.push('--happy-tools-config', happyPath)
  }
  const model = {
    id: options.modelId,
    provider: 'anthropic',
    api: 'anthropic-messages',
    name: `Claude ${modelFixtures[options.modelId]?.displayName ?? 'Unknown'}`,
    baseUrl: 'https://offline.invalid',
    reasoning: true,
    input: ['text', 'image'],
    contextWindow: 200000,
    maxTokens: 8192,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }
  }
  const hooks = new Map<string, Hook[]>()
  const fakePi = {
    on (name: string, handler: Hook) {
      const registered = hooks.get(name) ?? []
      registered.push(handler)
      hooks.set(name, registered)
    },
    async exec (_command: string, args: string[]) {
      const joined = args.join(' ')
      if (joined === 'rev-parse --is-inside-work-tree') {
        return options.isRepo === false
          ? { code: 1, stdout: '' }
          : { code: 0, stdout: 'true\n' }
      }
      if (joined === 'rev-parse --git-dir') {
        return {
          code: 0,
          stdout: options.worktree === false
            ? '/offline/project/.git\n'
            : '/offline/project/.git/worktrees/feature\n'
        }
      }
      if (joined === 'rev-parse --git-common-dir') {
        return { code: 0, stdout: '/offline/project/.git\n' }
      }
      if (joined === 'rev-parse --show-toplevel') {
        return { code: 0, stdout: '/offline/project\n' }
      }
      if (joined === 'symbolic-ref --quiet --short HEAD') {
        return { code: 0, stdout: 'feature\n' }
      }
      if (joined === 'symbolic-ref --quiet --short refs/remotes/origin/HEAD') {
        return { code: 0, stdout: 'origin/master\n' }
      }
      if (joined.startsWith('show-ref --verify')) return { code: 1, stdout: '' }
      if (joined === 'config user.name') return { code: 0, stdout: 'Offline User\n' }
      if (joined === '--no-optional-locks status --short') {
        return { code: 0, stdout: ' M move-reminders.ts\n' }
      }
      if (joined === '--no-pager log -5 --oneline') {
        return { code: 0, stdout: 'abc123 offline fixture\n' }
      }
      return { code: 1, stdout: '' }
    }
  } as unknown as ExtensionAPI
  const { createJiti } = runtimeRequire('jiti') as {
    createJiti: (id: string, options: object) => {
      import: (path: string, options?: object) => Promise<unknown>
    }
  }
  const jiti = createJiti(import.meta.url, {
    fsCache: false,
    moduleCache: false,
    alias: {
      '@earendil-works/pi-coding-agent': process.env.MOVE_TEST_SDK ??
        join(dirname(runtimeRequire.resolve('./package.json')), 'dist/index.js')
    }
  })
  const factory = await jiti.import(movePath, { default: true }) as ExtensionFactory
  factory(fakePi)
  const cwd = options.cwd ?? (options.localInstructions === undefined
    ? projectRoot
    : localCwd)
  const ctx = {
    cwd,
    model,
    modelRegistry: { getProviderDisplayName: () => 'Anthropic' }
  } as unknown as ExtensionContext
  for (const handler of hooks.get('session_start') ?? []) handler({}, ctx)
  const startupEvent = {
    systemPromptOptions: { appendSystemPrompt: options.appendSystemPrompt }
  }
  for (const handler of hooks.get('before_agent_start') ?? []) handler(startupEvent, ctx)
  const originalImage = { type: 'image', data: 'offline-image', mimeType: 'image/png' }
  const originalTools = [{ name: 'offline-tool', input_schema: { type: 'object' } }]
  const payload: Payload = {
    model: options.modelId,
    system: options.billing === undefined
      ? [{ type: 'text', text: 'SDK prompt', cache_control: { type: 'ephemeral' } }]
      : [{ type: 'text', text: options.billing },
          { type: 'text', text: 'SDK prompt', cache_control: { type: 'ephemeral' } }],
    messages: [
      {
        role: 'user',
        content: [
          { type: 'text', text: 'Real user request' }, originalImage
        ]
      },
      ...(options.initial === true
        ? []
        : [
            { role: 'assistant', content: [{ type: 'text', text: 'History assistant' }] },
            {
              role: 'toolResult',
              content: [{ type: 'text', text: 'History tool result' }]
            }
          ])
    ],
    tools: originalTools,
    ...options.payload
  }
  if (options.effortHistory === true) {
    payload.output_config = { effort: 'high', format: 'caller-format' }
    payload.messages.splice(1, 0, {
      role: 'system', content: [], output_config: { effort: 'low' }
    })
    payload.messages.push(
      { role: 'user', content: 'A later request' },
      { role: 'system', content: [], output_config: { effort: 'medium' } }
    )
  }
  const fingerprint = await jiti.import(join(root,
    'extensions/aoauth-fingerprint-2_1_322.ts')) as {
    applyFingerprintPayload: (payload: Payload, modelId: string) => Payload
  }
  const originalPayload = structuredClone(payload)
  const input = options.fingerprintOrder === 'before'
    ? fingerprint.applyFingerprintPayload(payload, options.modelId)
    : payload
  const hook = hooks.get('before_provider_request')?.[0]
  assert.ok(hook)
  const moved = await hook({ payload: input }, ctx)
  assert.ok(isRecord(moved))
  const result = options.fingerprintOrder === 'after'
    ? fingerprint.applyFingerprintPayload(moved as Payload, options.modelId)
    : moved
  assert.ok(isRecord(result))
  assert.deepEqual(payload, originalPayload)
  const output = result as Payload
  const user = output.messages.find((message) => message.role === 'user')
  assert.ok((user != null) && Array.isArray(user.content))
  const userBlocks = user.content as PromptBlock[]
  if (options.payload === undefined) {
    assert.deepEqual(output.messages.slice(2).map(({ role }) => role),
      options.effortHistory === true
        ? ['assistant', 'toolResult', 'user', 'system']
        : options.initial === true ? [] : ['assistant', 'toolResult'])
    assert.strictEqual(output.tools, originalTools)
  }
  await rm(temp, { recursive: true, force: true })
  process.argv = oldArgv
  if (oldHome === undefined) delete process.env.HOME
  else process.env.HOME = oldHome
  if (oldAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR
  else process.env.PI_CODING_AGENT_DIR = oldAgentDir
  return { output, userBlocks }
}
function systemText (message: Message | undefined): string {
  if ((message != null) && typeof message.content === 'string') return message.content
  assert.ok((message != null) && Array.isArray(message.content))
  const first = message.content[0]
  assert.ok(isRecord(first) && typeof first.text === 'string')
  return first.text
}

void test('capture-backed prompts and native placement ' +
  'cover all five models', async () => {
  for (const [id, fixture] of Object.entries(modelFixtures)) {
    const { output, userBlocks } = await createHarness({ modelId: id, initial: true })
    assert.equal(output.model, id)
    assert.equal(output.system.length, 2)
    assert.equal(sha256(output.system[0]?.text ?? ''), expected.identityHash)
    assert.deepEqual(output.system[0]?.cache_control, {
      type: 'ephemeral', ttl: '1h'
    })
    const modelPrompt = output.system[1]
    assert.ok(modelPrompt?.text)
    assert.deepEqual(modelPrompt.cache_control, { type: 'ephemeral', ttl: '1h' })
    const normalizedPrompt = modelPrompt.text.replace(
      /(?<=persistent file-based memory at `)[^`]+(?=`)/u,
      '{{crs_memory_dir}}'
    )
    assert.equal(sha256(normalizedPrompt), fixture.systemPromptHash)
    assert.deepEqual(output.messages.map(({ role }) => role), ['user', 'system'])
    const nativeSystem = output.messages[1]
    assert.deepEqual((nativeSystem?.content as PromptBlock[])[0]?.cache_control, {
      type: 'ephemeral', ttl: '1h'
    })
    const nativeText = systemText(nativeSystem)
    const normalizedNativeText = nativeText
      .replaceAll(projectRoot, '{{crs_cwd}}')
      .replace(/ - This is a git worktree —[^\n]*\n - The git stash stack is[^\n]*\n/u,
        '{{crs_worktree_notice}}\n')
      .replace(/^ - Is a git repository: true$/mu,
        ' - Is a git repository: {{crs_is_repo}}')
      .replace(/^ - Platform: [^\n]+/mu, ' - Platform: {{crs_platform}}')
      .replace(/^ - Shell: [^\n]+/mu, ' - Shell: {{crs_shell}}')
      .replace(/^ - OS Version: [^\n]+/mu, ' - OS Version: {{crs_os_ver}}')
      .replace(/Today's date is \d{4}-\d{2}-\d{2}/gu, 'Today\'s date is {{crs_today}}')
      .replace(/(?<=persistent file-based memory at `)[^`]+(?=`)/u,
        '{{crs_memory_dir}}')
    const nativeTemplatePath = join(promptDirectory,
      `80native-system-${id.slice('claude-'.length).replaceAll('-', '_')}.md`)
    const nativeTemplate = (await readFile(nativeTemplatePath, 'utf8'))
      .replace(/\n$/u, '')
    if (normalizedNativeText !== nativeTemplate) {
      let mismatchIndex = 0
      const length = Math.min(normalizedNativeText.length, nativeTemplate.length)
      while (mismatchIndex < length &&
        normalizedNativeText[mismatchIndex] === nativeTemplate[mismatchIndex]) {
        mismatchIndex += 1
      }
      assert.fail(`Native capture drift at ${mismatchIndex}: template ${JSON.stringify(
        nativeTemplate.slice(mismatchIndex - 50, mismatchIndex + 80)
      )}, output ${JSON.stringify(normalizedNativeText.slice(mismatchIndex - 50,
        mismatchIndex + 80))}`)
    }
    assert.equal(sha256(normalizedNativeText.replace('{{crs_is_repo}}', 'true')),
      fixture.nativeSystemHash)
    assert.match(nativeText, /This is a git worktree/u)
    assert.match(nativeText, /Today's date is/u)
    assert.match(nativeText, /Available agent types/u)
    assert.match(nativeText, new RegExp(`model named ${fixture.displayName}`))
    assert.equal(userBlocks.at(-1)?.type, 'image')
    assert.equal(userBlocks.at(-1)?.data, 'offline-image')
    const userText = userBlocks.map((block) => block.text ?? '').join('\n')
    assert.ok(userText.includes(`Contents of ${projectRoot}/AGENTS.md`))
    assert.ok(userText.includes(' M move-reminders.ts'))
    assert.ok(userText.includes(expected.gitFooter))
    assert.equal(userBlocks[2]?.text?.endsWith('\n'), expected.attributionEndsWithNewline)
    assert.ok(userText.includes(`Co-Authored-By: Claude ${fixture.displayName}`))
    assert.ok(!userText.includes('Claude Claude'))
    assert.ok(!userText.includes('<!--'))
    assert.ok(userBlocks.every((block) => block.cache_control === undefined))
    assert.ok(userText.includes('Real user request'))
    assert.ok(!userText.includes('Contents of (none)'))
  }
})

void test('move preserves fingerprint-owned billing for every model', async () => {
  const billing = 'x-anthropic-billing-header: cc_version=2.1.322.dynamic; ' +
    'cc_entrypoint=cli;'
  for (const modelId of Object.keys(modelFixtures)) {
    const { output } = await createHarness({ modelId, billing })
    assert.equal(output.system.length, 3)
    assert.deepEqual(output.system[0], { type: 'text', text: billing })
    assert.ok(output.system.slice(1).every((block) =>
      block.text?.startsWith('x-anthropic-billing-header:') !== true))
  }
})

void test('both hook orders preserve historical and current effort', async () => {
  for (const modelId of Object.keys(modelFixtures)) {
    for (const fingerprintOrder of ['before', 'after'] as const) {
      const { output } = await createHarness({
        modelId, effortHistory: true, fingerprintOrder
      })
      assert.deepEqual(output.output_config, {
        effort: 'medium', format: 'caller-format'
      })
      assert.deepEqual(output.messages[1]?.output_config, { effort: 'low' })
      assert.deepEqual(output.messages.map(({ role }) => role), [
        'user', 'system', 'assistant', 'toolResult', 'user', 'system'
      ])
      assert.deepEqual(output.messages.at(-1)?.output_config, { effort: 'medium' })
      const billing = output.system.filter((block) =>
        block.text?.startsWith('x-anthropic-billing-header:'))
      assert.equal(billing.length, 1)
      assert.ok(billing[0]?.text?.includes('2.1.322.91f;') !== true)
    }
  }
})

void test('worktree notice is omitted for a regular repository', async () => {
  const { output } = await createHarness({
    modelId: 'claude-sonnet-5-5', worktree: false
  })
  assert.ok(!systemText(output.messages[1]).includes('This is a git worktree'))
  assert.match(systemText(output.messages[1]), /Is a git repository: true/u)
})

void test('Happier and appendSystemPrompt follow ' +
  'the cached native model prompt', async () => {
  const { output } = await createHarness({
    modelId: 'claude-opus-5-5',
    happierPrompt: 'Happier append prompt',
    appendSystemPrompt: 'CLI append prompt'
  })
  assert.equal(output.system[2]?.text, 'Happier append prompt')
  assert.equal(output.system[3]?.text, 'CLI append prompt')
  assert.equal(output.system[2]?.cache_control, undefined)
  assert.equal(output.system[3]?.cache_control, undefined)
})

void test('unknown models retain the legacy reminder message behavior', async () => {
  const { output } = await createHarness({ modelId: 'unknown-claude-model' })
  assert.deepEqual(output.messages.map(({ role }) => role), [
    'user', 'user', 'assistant', 'toolResult'
  ])
  const originalUserContent = output.messages[1]?.content
  assert.ok(Array.isArray(originalUserContent))
  const finalOriginalBlock = originalUserContent.at(-1)
  assert.ok(isRecord(finalOriginalBlock))
  assert.equal(finalOriginalBlock.type, 'image')
  assert.ok(Array.isArray(output.messages[0]?.content))
  assert.equal(output.messages[0]?.content.length, 7)
  assert.equal(output.system[0]?.cache_control, undefined)
})

void test('the configured Opus native environment reference ' +
  'is capture-backed', async () => {
  const configPath = join(promptDirectory, 'move.json')
  const config = JSON.parse(await readFile(configPath, 'utf8')) as {
    models: Record<string, { reminders: Array<string | { $ref: string }> }>
  }
  const ref = config.models['claude-opus-5-5']?.reminders[0]
  assert.ok(typeof ref === 'object' && ref !== null)
  const text = await readFile(join(promptDirectory, ref.$ref), 'utf8')
  assert.match(text, /\{\{crs_cwd\}\}/u)
  assert.match(text, /\{\{crs_os_ver\}\}/u)
  assert.match(text, /\{\{crs_today\}\}/u)
  assert.match(text, /\{\{crs_worktree_notice\}\}/u)
})

interface Cache { type: 'ephemeral', ttl?: '1h' | '5m' }
const defaultCache: Cache = { type: 'ephemeral' }
const longCache: Cache = { type: 'ephemeral', ttl: '1h' }
function assertCacheMarker (marker: unknown): asserts marker is Cache {
  assert.ok(isRecord(marker))
  assert.equal(marker.type, 'ephemeral')
  assert.ok(marker.ttl === undefined || marker.ttl === '5m' || marker.ttl === '1h')
}
function cacheMarkers (payload: Payload): Cache[] {
  const markers: Cache[] = []
  const visit = (value: unknown): void => {
    if (Array.isArray(value)) {
      value.forEach(visit)
      return
    }
    if (!isRecord(value)) return
    if (value.cache_control != null) {
      assertCacheMarker(value.cache_control)
      markers.push(value.cache_control)
    }
    if (value.type === 'tool_addition' && isRecord(value.tool) &&
      value.tool.type === 'tool_definition' && isRecord(value.tool.definition) &&
      value.tool.definition.cache_control != null) {
      const marker = value.tool.definition.cache_control
      assertCacheMarker(marker)
      markers.push(marker)
    }
    if (Array.isArray(value.content)) visit(value.content)
  }
  visit(payload.tools)
  visit(payload.system)
  visit(payload.messages)
  if (payload.cache_control != null) {
    assertCacheMarker(payload.cache_control)
    markers.push(payload.cache_control)
  }
  return markers
}
function assertCacheValid (payload: Payload): void {
  const markers = cacheMarkers(payload)
  if (isRecord(payload.cache_control)) {
    let last: Record<string, unknown> | undefined
    for (const tool of Array.isArray(payload.tools) ? payload.tools : []) {
      if (isRecord(tool)) last = tool
    }
    for (const block of payload.system) if (isRecord(block)) last = block
    for (const message of payload.messages) {
      // Turn-scoped system text is not cache-eligible, even before it expires.
      if (message.clear_at === 'next_user_message') continue
      if (typeof message.content === 'string') last = undefined
      if (Array.isArray(message.content)) {
        for (const block of message.content) {
          if (!isRecord(block)) continue
          last = block
          if (block.type === 'tool_addition' && isRecord(block.tool) &&
            block.tool.type === 'tool_definition' && isRecord(block.tool.definition)) {
            last = block.tool.definition
          }
          if (block.type === 'tool_result' && Array.isArray(block.content)) {
            for (const nested of block.content) if (isRecord(nested)) last = nested
          }
        }
      }
    }
    if (isRecord(last?.cache_control)) {
      assert.equal(payload.cache_control.ttl === '1h', last.cache_control.ttl === '1h',
        'automatic and last explicit breakpoint TTLs differ')
      markers.pop()
    }
  }
  assert.ok(markers.length <= 4, `too many cache breakpoints: ${markers.length}`)
  let short = false
  for (const marker of markers) {
    if (marker.ttl !== '1h') short = true
    else assert.equal(short, false, '1h breakpoint after a 5m breakpoint')
  }
}
function isEmptySystem (message: Message): boolean {
  return message.role === 'system' && Array.isArray(message.content) &&
    message.content.length === 0
}
function assertSystemPlacement (messages: Message[]): void {
  const meaningful = messages.filter((message) =>
    !(isEmptySystem(message) && isRecord(message.output_config)))
  assert.equal(meaningful[0]?.role, 'user', 'first conversational message must be user')
  for (let index = 0; index < meaningful.length; index++) {
    if (meaningful[index]?.role !== 'system') continue
    const previous = meaningful[index - 1]
    while (meaningful[index + 1]?.role === 'system') index++
    const next = meaningful[index + 1]
    const serverTool = previous?.role === 'assistant' &&
      Array.isArray(previous.content) && isRecord(previous.content.at(-1)) &&
      previous.content.at(-1)?.type === 'server_tool_use'
    assert.ok(previous?.role === 'user' || serverTool, 'system must follow a user')
    assert.ok(next === undefined || next.role === 'assistant',
      'system must be last or followed by an assistant')
  }
}
function sdkBody (
  mode: 'none' | 'default' | 'long', followup: boolean
): Partial<Payload> {
  const marker = mode === 'none' ? undefined : mode === 'long' ? longCache : defaultCache
  const tool = {
    name: 'sdk-tool',
    input_schema: { type: 'object' },
    ...(marker === undefined ? {} : { cache_control: marker })
  }
  const userBlock = {
    type: 'text',
    text: followup ? 'Followup' : 'First',
    ...(marker === undefined ? {} : { cache_control: marker })
  }
  return {
    system: [{
      type: 'text',
      text: 'SDK prompt',
      ...(marker === undefined ? {} : { cache_control: marker })
    }],
    tools: [tool],
    output_config: { effort: 'high' },
    messages: followup
      ? [
          { role: 'user', content: 'First' },
          { role: 'system', content: [], output_config: { effort: 'medium' } },
          { role: 'assistant', content: [{ type: 'text', text: 'Answer' }] },
          { role: 'user', content: [userBlock] },
          { role: 'system', content: [], output_config: { effort: 'high' } }
        ]
      : [
          { role: 'user', content: [userBlock] },
          { role: 'system', content: [], output_config: { effort: 'high' } }
        ]
  }
}

void test('captured cache topology is valid for every model', async () => {
  for (const modelId of Object.keys(modelFixtures)) {
    for (const followup of [false, true]) {
      const { output } = await createHarness({
        modelId, payload: sdkBody('default', followup)
      })
      assertCacheValid(output)
      assertSystemPlacement(output.messages)
      assert.deepEqual(cacheMarkers(output), [longCache, longCache, longCache])
      assert.equal((output.tools as Array<{ cache_control?: unknown }>)[0]?.cache_control,
        undefined)
      const environment = output.messages.find((message) => message.role === 'system' &&
        !isEmptySystem(message))
      assert.ok(environment)
      if (followup) {
        assert.equal(typeof environment.content, 'string')
        const lastUser = output.messages.filter(
          (message) => message.role === 'user'
        ).at(-1)
        assert.deepEqual((lastUser?.content as PromptBlock[]).at(-1)?.cache_control,
          longCache)
      } else {
        assert.deepEqual((environment.content as PromptBlock[])[0]?.cache_control,
          longCache)
      }
    }
  }
})

void test('none, default and long SDK caches fit the full request budget', async () => {
  for (const mode of ['none', 'default', 'long'] as const) {
    for (const followup of [false, true]) {
      const { output } = await createHarness({
        modelId: 'claude-opus-5-5', payload: sdkBody(mode, followup)
      })
      assertCacheValid(output)
      if (mode === 'none') assert.deepEqual(cacheMarkers(output), [])
      if (mode === 'long') {
        const firstTool = (output.tools as Array<{ cache_control?: unknown }>)[0]
        assert.deepEqual(firstTool?.cache_control, longCache)
      }
    }
  }
})

void test('automatic cache does not conflict with ' +
  'an explicit last-block TTL', async () => {
  for (const followup of [false, true]) {
    const { output } = await createHarness({
      modelId: 'claude-opus-5-5',
      payload: {
        ...sdkBody('default', followup), cache_control: defaultCache
      }
    })
    assertCacheValid(output)
    assert.deepEqual(output.cache_control, defaultCache)
  }
})

void test('caller text, image, TTL and auto-cache markers survive', async () => {
  const image = {
    type: 'image',
    source: {
      type: 'base64',
      media_type: 'image/png',
      data: 'aW1hZ2U='
    },
    cache_control: longCache
  }
  const cases: Array<Partial<Payload>> = [
    {
      system: [],
      tools: [],
      messages: [{
        role: 'user',
        content: [
          { type: 'text', text: 'custom', cache_control: longCache }, image
        ]
      }]
    },
    {
      system: [],
      tools: [{ name: 'custom', cache_control: longCache }],
      cache_control: longCache,
      messages: [{
        role: 'user',
        content: [
          { type: 'text', text: 'custom', cache_control: longCache }, image
        ]
      }]
    },
    {
      system: [],
      tools: [{ name: 'caller-short', cache_control: defaultCache },
        { name: 'plain' }],
      messages: [{
        role: 'user',
        content: [
          {
            type: 'text',
            text: 'custom',
            cache_control: { type: 'ephemeral', ttl: '5m' }
          },
          { ...image, cache_control: defaultCache }
        ]
      }]
    }
  ]
  for (const payload of cases) {
    const { output } = await createHarness({ modelId: 'claude-opus-5-5', payload })
    assertCacheValid(output)
    const blocks = output.messages.find((message) => message.role === 'user')?.content
    assert.ok(Array.isArray(blocks))
    const original = (payload.messages?.[0]?.content as PromptBlock[])
    assert.deepEqual(blocks.slice(-original.length), original)
    assert.deepEqual(output.tools, payload.tools)
    const cachePayload: Payload = {
      ...payload, system: [], tools: payload.tools ?? [], messages: payload.messages ?? []
    }
    assert.equal(cacheMarkers(output).length, cacheMarkers(cachePayload).length)
    if (payload.cache_control !== undefined) {
      assert.deepEqual(output.cache_control, payload.cache_control)
      assert.equal(cacheMarkers(output).length, 4)
    }
  }
})

void test('SDK cache policy respects custom markers and the automatic slot', async () => {
  const sdkSystem = [{ type: 'text', text: 'SDK prompt', cache_control: defaultCache }]
  const image = {
    type: 'image',
    source: {
      type: 'base64',
      media_type: 'image/png',
      data: 'aW1hZ2U='
    },
    cache_control: longCache
  }
  const cases: Array<Partial<Payload>> = [
    {
      system: sdkSystem,
      tools: [{ name: 'custom', cache_control: longCache }],
      cache_control: longCache,
      messages: [{
        role: 'user',
        content: [
          { type: 'text', text: 'custom', cache_control: longCache }, image
        ]
      }]
    },
    {
      system: sdkSystem,
      tools: [{ name: 'short', cache_control: defaultCache },
        { name: 'plain' }],
      messages: [{
        role: 'user',
        content: [
          {
            type: 'text',
            text: 'custom',
            cache_control: { type: 'ephemeral', ttl: '5m' }
          },
          { ...image, cache_control: defaultCache }
        ]
      }]
    }
  ]
  for (const payload of cases) {
    const { output } = await createHarness({ modelId: 'claude-opus-5-5', payload })
    assertCacheValid(output)
    assert.deepEqual(output.tools, payload.tools)
    const blocks = output.messages[0]?.content
    assert.ok(Array.isArray(blocks))
    assert.deepEqual(blocks.slice(-2), payload.messages?.[0]?.content)
    if (payload.cache_control === undefined) {
      assert.ok(output.system.every((block) => block.cache_control === undefined))
      assert.equal(cacheMarkers(output).length, 3)
    } else {
      assert.deepEqual(cacheMarkers(output), [longCache, longCache, longCache,
        longCache, longCache])
      assert.equal(output.system.filter((block) =>
        block.text?.startsWith(environmentPrefix)).length, 1)
      assert.ok(!output.messages.some((message) => message.role === 'system' &&
        !isEmptySystem(message)))
    }
  }
})

void test('compact user groups, reminders, images and ' +
  'tool results keep order', async () => {
  const image = {
    type: 'image',
    source: {
      type: 'base64',
      media_type: 'image/png',
      data: 'aW1hZ2U='
    }
  }
  const payload: Partial<Payload> = {
    system: [],
    tools: [],
    messages: [
      { role: 'user', content: [{ type: 'text', text: 'Summary' }, image] },
      { role: 'user', content: [{ type: 'text', text: 'Retained' }] },
      { role: 'system', content: [], output_config: { effort: 'low' } },
      {
        role: 'assistant',
        content: [
          { type: 'tool_use', id: 'call', name: 'read', input: {} }
        ]
      },
      {
        role: 'user',
        content: [
          { type: 'tool_result', tool_use_id: 'call', content: 'result' }
        ]
      }
    ]
  }
  const { output } = await createHarness({ modelId: 'claude-opus-5-5', payload })
  assertSystemPlacement(output.messages)
  const firstAssistant = output.messages.findIndex(
    (message) => message.role === 'assistant'
  )
  const beforeAssistant = output.messages.slice(0, firstAssistant)
  const envIndex = beforeAssistant.findIndex((message) => message.role === 'system' &&
    !isEmptySystem(message))
  const retainedIndex = beforeAssistant.findIndex((message) => message.role === 'user' &&
    JSON.stringify(message.content).includes('Retained'))
  assert.ok(envIndex > retainedIndex)
  assert.ok(JSON.stringify(beforeAssistant).indexOf('Summary') <
    JSON.stringify(beforeAssistant).indexOf('Retained'))
  assert.ok(JSON.stringify(beforeAssistant).includes('aW1hZ2U='))
  const resultMessage = output.messages.at(-1)
  assert.ok(Array.isArray(resultMessage?.content))
  assert.equal(resultMessage.content[0]?.type, 'tool_result')
})

void test('system reminders and empty or tool-use-only histories ' +
  'stay valid', async () => {
  const cases: Message[][] = [
    [{ role: 'user', content: 'First' },
      { role: 'system', content: 'Existing reminder', custom_field: 'keep' },
      { role: 'assistant', content: [{ type: 'text', text: 'Answer' }] }],
    [{
      role: 'assistant',
      content: [
        { type: 'tool_use', id: 'call', name: 'read', input: {} }
      ]
    },
    {
      role: 'user',
      content: [
        { type: 'tool_result', tool_use_id: 'call', content: 'result' },
        { type: 'text', text: 'After result' }
      ]
    }],
    [{
      role: 'assistant',
      content: [
        { type: 'tool_use', id: 'call', name: 'read', input: {} }
      ]
    }],
    []
  ]
  for (const messages of cases) {
    const { output } = await createHarness({
      modelId: 'claude-opus-5-5',
      payload: { system: [], tools: [], messages }
    })
    assertSystemPlacement(output.messages)
    assert.ok(output.messages.some((message) => message.role === 'system' &&
      JSON.stringify(message.content).includes('# Environment')) ||
      output.system.some((block) => block.text?.startsWith(environmentPrefix)))
    if (JSON.stringify(messages).includes('Existing reminder')) {
      assert.deepEqual(output.messages[1], messages[1])
      assert.equal(output.system.filter((block) =>
        block.text?.startsWith(environmentPrefix)).length, 1)
    }
    if (JSON.stringify(messages).includes('tool_result')) {
      const resultUser = output.messages.find((message) => message.role === 'user' &&
        JSON.stringify(message.content).includes('tool_result'))
      assert.ok(Array.isArray(resultUser?.content))
      assert.equal(resultUser.content[0]?.type, 'tool_result')
    }
  }
})

void test('assistant tool-use prefix gains a user ' +
  'without disturbing its result', async () => {
  const assistant = {
    role: 'assistant',
    content: [
      { type: 'tool_use', id: 'call', name: 'read', input: {} }
    ]
  }
  const result = {
    role: 'user',
    content: [
      { type: 'tool_result', tool_use_id: 'call', content: 'result' },
      { type: 'text', text: 'After result' }
    ]
  }
  const { output } = await createHarness({
    modelId: 'claude-opus-5-5',
    payload: { system: [], tools: [], messages: [assistant, result] }
  })
  assertSystemPlacement(output.messages)
  assert.deepEqual(output.messages.slice(-2), [assistant, result])
})

void test('turn-scoped existing system reminders do not ' +
  'acquire a cache marker', async () => {
  const reminder = {
    role: 'system',
    content: 'One-turn reminder',
    clear_at: 'next_user_message'
  }
  const { output } = await createHarness({
    modelId: 'claude-opus-5-5',
    payload: {
      system: [{ type: 'text', text: 'SDK prompt', cache_control: defaultCache }],
      tools: [],
      messages: [{ role: 'user', content: 'First' }, reminder]
    }
  })
  assertSystemPlacement(output.messages)
  assertCacheValid(output)
  const scoped = output.messages.find(
    (message) => message.clear_at === 'next_user_message'
  )
  assert.deepEqual(scoped, reminder)
  assert.ok(output.system.some((block) => block.text?.startsWith(environmentPrefix)))
})

void test('unknown historical effort is not guessed from the current tail', async () => {
  for (const fingerprintOrder of ['before', 'after'] as const) {
    const { output } = await createHarness({
      modelId: 'claude-opus-5-5',
      fingerprintOrder,
      payload: {
        ...sdkBody('default', true),
        messages: [
          { role: 'user', content: 'First' },
          { role: 'assistant', content: [{ type: 'text', text: 'Answer' }] },
          { role: 'user', content: 'Second' },
          { role: 'system', content: [], output_config: { effort: 'high' } }
        ]
      }
    })
    const environment = output.messages.find((message) => message.role === 'system' &&
      !isEmptySystem(message))
    assert.ok(environment)
    assert.equal(environment.output_config, undefined)
    assert.deepEqual(output.messages.at(-1)?.output_config, { effort: 'high' })
  }
})

void test('both hook orders retain extra per-message controls', async () => {
  for (const fingerprintOrder of ['before', 'after'] as const) {
    const initial = {
      role: 'system',
      content: [],
      custom_field: 'initial',
      output_config: { effort: 'low', format: { type: 'json_schema', schema: {} } }
    }
    const tail = {
      role: 'system',
      content: [],
      custom_field: 'tail',
      output_config: { effort: 'high', format: { type: 'tail-format' } }
    }
    const { output } = await createHarness({
      modelId: 'claude-opus-5-5',
      fingerprintOrder,
      payload: {
        system: [],
        tools: [],
        output_config: { effort: 'medium', format: 'top' },
        messages: [
          { role: 'user', content: 'First' }, initial,
          { role: 'assistant', content: [{ type: 'text', text: 'Answer' }] },
          { role: 'user', content: 'Second' }, tail]
      }
    })
    assert.deepEqual(output.output_config, { effort: 'high', format: 'top' })
    assert.ok(output.messages.some((message) =>
      JSON.stringify(message) === JSON.stringify(initial)))
    assert.deepEqual(output.messages.at(-1), tail)
  }
})

void test('historical controls and malformed or extended tails survive', async () => {
  for (const modelId of Object.keys(modelFixtures)) {
    for (const fingerprintOrder of ['before', 'after'] as const) {
      for (const tail of [
        { role: 'system', content: [], output_config: { effort: 'high' }, extra: 'keep' },
        {
          role: 'system',
          content: [],
          output_config: { effort: 'invalid', custom: true }
        },
        { role: 'system', content: [], output_config: { custom: 'keep' } }
      ]) {
        const initial = {
          role: 'system',
          content: [],
          output_config: { effort: 'low' },
          extra: 'history'
        }
        const { output } = await createHarness({
          modelId,
          fingerprintOrder,
          payload: {
            system: [],
            tools: [],
            output_config: { effort: 'medium', format: 'top' },
            messages: [{ role: 'user', content: 'First' }, initial, tail]
          }
        })
        assert.ok(output.messages.some((message) => JSON.stringify(message) ===
          JSON.stringify(initial)))
        assert.deepEqual(output.messages.at(-1), tail)
        assert.deepEqual(output.output_config, {
          effort: tail.output_config.effort === 'high' ? 'high' : 'medium', format: 'top'
        })
      }
    }
  }
})

void test('controls before the first user keep the current effort tail', async () => {
  for (const modelId of Object.keys(modelFixtures)) {
    for (const fingerprintOrder of ['before', 'after'] as const) {
      const historical = {
        role: 'system',
        content: [],
        output_config: { effort: 'low' },
        custom_field: 'keep'
      }
      const tail = { role: 'system', content: [], output_config: { effort: 'high' } }
      const { output } = await createHarness({
        modelId,
        fingerprintOrder,
        payload: {
          system: [],
          tools: [],
          output_config: { effort: 'medium', format: 'top' },
          messages: [historical, { role: 'user', content: 'First' }, tail]
        }
      })
      assert.deepEqual(output.messages[0], historical)
      assert.deepEqual(output.messages.at(-1), tail)
      const environment = output.messages.find((message) => message.role === 'system' &&
        !isEmptySystem(message))
      assert.equal(environment?.output_config, undefined)
      assert.deepEqual(output.output_config, { effort: 'high', format: 'top' })
    }
  }
})

void test('native environments report a non-repository ' +
  'for every captured model', async () => {
  for (const modelId of Object.keys(modelFixtures)) {
    const { output } = await createHarness({ modelId, isRepo: false, initial: true })
    assert.match(systemText(output.messages[1]), /Is a git repository: false/u)
    assert.ok(!systemText(output.messages[1]).includes('This is a git worktree'))
  }
})

async function recordR2 (payload: Payload, name: string): Promise<void> {
  const directory = process.env.MOVE_R2_CAPTURE_DIR
  if (directory !== undefined) {
    await writeFile(join(directory, `${name}.json`), JSON.stringify(payload))
  }
}
function assertRequestShape (payload: Payload): void {
  assertSystemPlacement(payload.messages)
  assertCacheValid(payload)
  const pending = new Set<string>()
  for (const message of payload.messages) {
    if (message.role === 'assistant') {
      assert.equal(pending.size, 0)
      if (!Array.isArray(message.content)) continue
      for (const block of message.content) {
        if (isRecord(block) && block.type === 'tool_use') {
          assert.equal(typeof block.id, 'string')
          pending.add(block.id as string)
        }
      }
    } else if (message.role === 'user' && Array.isArray(message.content)) {
      let seenOther = false
      for (const block of message.content) {
        assert.ok(isRecord(block))
        if (block.type !== 'tool_result') {
          seenOther = true
        } else {
          assert.equal(seenOther, false, 'tool results must precede other content')
          assert.equal(typeof block.tool_use_id, 'string')
          assert.ok(pending.delete(block.tool_use_id as string))
        }
      }
    } else {
      assert.ok(['user', 'system'].includes(message.role))
    }
  }
  assert.equal(pending.size, 0)
}
function visibleContent (payload: Payload): string {
  const persistent: unknown[] = [...payload.system]
  let scoped: unknown[] = []
  for (const message of payload.messages) {
    if (message.role === 'user') scoped = []
    if (message.role !== 'system') continue
    if (message.clear_at === 'next_user_message') scoped.push(message.content)
    else persistent.push(message.content)
  }
  return JSON.stringify([...persistent, ...scoped])
}
function toolLoop (
  toolCache: Cache | undefined, resultCache: Cache | undefined
): Partial<Payload> {
  const cache = toolCache === undefined ? {} : { cache_control: toolCache }
  const result = resultCache === undefined ? {} : { cache_control: resultCache }
  return {
    system: [{ type: 'text', text: 'SDK prompt', ...cache }],
    tools: [
      { name: 'read', input_schema: { type: 'object', properties: {} } },
      { name: 'search', input_schema: { type: 'object', properties: {} }, ...cache },
      {
        name: 'lazy-one',
        input_schema: { type: 'object', properties: { x: {} } },
        defer_loading: true
      },
      {
        name: 'lazy-two',
        input_schema: { type: 'object', properties: { y: {} } },
        defer_loading: true
      }
    ],
    output_config: { effort: 'medium', format: 'keep-top' },
    messages: [
      { role: 'user', content: 'First' },
      { role: 'system', content: [], output_config: { effort: 'low' } },
      {
        role: 'assistant',
        content: [
          { type: 'tool_use', id: 'one', name: 'read', input: { path: 'one' } },
          { type: 'tool_use', id: 'two', name: 'search', input: { query: 'two' } }
        ]
      },
      {
        role: 'user',
        content: [
          { type: 'tool_result', tool_use_id: 'one', content: 'first-result' },
          { type: 'tool_result', tool_use_id: 'two', content: 'second-result', ...result }
        ]
      },
      { role: 'system', content: [], output_config: { effort: 'high' } }
    ]
  }
}
async function realSdkToolLoop (modelId: string): Promise<Partial<Payload>> {
  const codingRoot = dirname(runtimeRequire.resolve('./package.json'))
  const adapterPath = join(codingRoot,
    'node_modules/@earendil-works/pi-ai/dist/api/anthropic-messages.js')
  const adapter = await import(pathToFileURL(adapterPath).href) as {
    stream: (model: unknown, context: unknown, options: unknown) => {
      result: () => Promise<{ stopReason: string, errorMessage?: string }>
    }
  }
  const model = {
    id: modelId,
    provider: 'anthropic',
    api: 'anthropic-messages',
    name: modelId,
    baseUrl: 'https://offline.invalid',
    input: ['text', 'image'],
    reasoning: true,
    maxTokens: 8192,
    contextWindow: 200000,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    compat: { supportsToolReferences: true, supportsMidConvoEffort: true }
  }
  const tool = (name: string): Record<string, unknown> => ({
    name,
    description: name,
    parameters: { type: 'object', properties: {} }
  })
  const assistant = (
    id: string, name: string, level: string
  ): Record<string, unknown> => ({
    role: 'assistant',
    provider: 'anthropic',
    api: 'anthropic-messages',
    model: modelId,
    content: [{ type: 'toolCall', id, name, arguments: {} }],
    providerThinkingLevel: level,
    stopReason: 'toolUse',
    timestamp: 1
  })
  const context = {
    systemPrompt: 'SDK prompt',
    tools: [tool('read'), tool('search'),
      tool('lazy')],
    messages: [
      { role: 'user', content: 'First', timestamp: 1 },
      assistant('one', 'search', 'low'),
      {
        role: 'toolResult',
        toolCallId: 'one',
        toolName: 'search',
        content: [{ type: 'text', text: 'found lazy' }],
        isError: false,
        addedToolNames: ['lazy'],
        timestamp: 1
      },
      assistant('two', 'read', 'medium'),
      {
        role: 'toolResult',
        toolCallId: 'two',
        toolName: 'read',
        content: [{ type: 'text', text: 'read result' }],
        isError: false,
        timestamp: 1
      }
    ]
  }
  let captured: unknown
  const result = await adapter.stream(model, context, {
    apiKey: 'offline-only',
    onPayload: (payload: unknown) => {
      captured = structuredClone(payload)
      throw new Error('offline payload captured before transport')
    }
  }).result()
  assert.equal(result.stopReason, 'error')
  assert.equal(result.errorMessage, 'offline payload captured before transport')
  assert.ok(isRecord(captured))
  return captured as Partial<Payload>
}
for (const modelId of Object.keys(modelFixtures)) {
  for (const order of ['before', 'after'] as const) {
    void test(`R2W2 real SDK deferred tool loop ${modelId} ${order}`, async () => {
      const payload = await realSdkToolLoop(modelId)
      const tools = payload.tools as Array<Record<string, unknown>>
      assert.deepEqual(tools[1]?.cache_control, defaultCache)
      assert.equal(tools[2]?.defer_loading, true)
      const original = payload.messages?.findLast((message) => message.role === 'user')
      assert.ok(Array.isArray(original?.content))
      assert.equal(original.content.at(-1)?.type, 'tool_result')
      assert.deepEqual(original.content.at(-1)?.cache_control, defaultCache)
      const { output } = await createHarness({
        modelId,
        fingerprintOrder: order,
        payload
      })
      await recordR2(output, `sdk-real-${modelId}-${order}`)
      assertRequestShape(output)
      const resultTools = output.tools as Array<Record<string, unknown>>
      assert.equal(resultTools[1]?.cache_control, undefined)
      assert.deepEqual(resultTools.slice(2), tools.slice(2))
      assert.deepEqual(cacheMarkers(output), [longCache, longCache, longCache])
      const user = output.messages.findLast((message) => message.role === 'user')
      assert.ok(Array.isArray(user?.content))
      assert.deepEqual(user.content.at(-1), {
        ...original.content.at(-1),
        cache_control: longCache
      })
      assert.deepEqual(output.output_config, { effort: 'high' })
      const controls = output.messages.filter((message) => message.role === 'system' &&
        isRecord(message.output_config)).map((message) => message.output_config)
      assert.deepEqual(controls, [
        { effort: 'low' }, { effort: 'medium' }, { effort: 'high' }
      ])
    })
    for (const expired of [false, true]) {
      void test(`R2W3 full automatic budget expired=${String(expired)} ` +
        `${modelId} ${order}`,
      async () => {
        const scoped = {
          role: 'system',
          content: 'Scoped only',
          clear_at: 'next_user_message'
        }
        const latest = {
          role: 'user',
          content: [
            { type: 'text', text: 'Latest', cache_control: longCache }
          ]
        }
        const payload: Partial<Payload> = {
          system: [],
          cache_control: longCache,
          tools: ['one', 'two', 'three'].map((name) => ({
            name,
            input_schema: { type: 'object' },
            cache_control: longCache
          })),
          messages: expired
            ? [{ role: 'user', content: 'First' }, scoped,
                { role: 'assistant', content: 'Reply' }, latest]
            : [latest, scoped]
        }
        const { output } = await createHarness({
          modelId,
          fingerprintOrder: order,
          payload
        })
        await recordR2(output, `scope-full-${String(expired)}-${modelId}-${order}`)
        assertRequestShape(output)
        assert.deepEqual(output.messages.find((message) =>
          message.clear_at === 'next_user_message'), scoped)
        assert.deepEqual(output.tools, payload.tools)
        assert.ok(visibleContent(output).includes('You have been invoked in the ' +
            'following environment:'))
        assert.equal(visibleContent(output).includes('Scoped only'), !expired)
        assert.equal(cacheMarkers(output).length, 5)
      })
    }
    for (const ttl of ['1h', '5m'] as const) {
      void test(`R2W1 initial duplicate ${ttl} ${modelId} ${order}`, async () => {
        const last: Cache = ttl === '1h' ? longCache : { type: 'ephemeral', ttl: '5m' }
        const payload: Partial<Payload> = {
          system: [],
          cache_control: last,
          tools: ['one', 'two', 'three'].map((name) => ({
            name,
            input_schema: { type: 'object' },
            cache_control: longCache
          })),
          messages: [{
            role: 'user',
            content: [
              { type: 'text', text: 'First', cache_control: last }
            ]
          }]
        }
        const { output } = await createHarness({
          modelId,
          fingerprintOrder: order,
          payload
        })
        await recordR2(output, `initial-duplicate-${ttl}-${modelId}-${order}`)
        assertRequestShape(output)
        assert.deepEqual(output.tools, payload.tools)
        assert.equal(cacheMarkers(output).length, 5)
        assert.ok(visibleContent(output).includes('You have been invoked in the ' +
          'following environment:'))
        assert.ok(output.system.some((block) => block.text?.startsWith('# Environment')))
        const user = output.messages.find((message) => message.role === 'user')
        assert.ok(Array.isArray(user?.content))
        assert.deepEqual(user.content.at(-1), {
          type: 'text', text: 'First', cache_control: last
        })
      })
      for (const nested of [false, true]) {
        void test(`R2W1 duplicate ${ttl} nested=${String(nested)} ` +
          `${modelId} ${order}`, async () => {
          const last: Cache = ttl === '1h' ? longCache : { type: 'ephemeral', ttl: '5m' }
          const result = {
            type: 'tool_result',
            tool_use_id: 'call',
            content: nested
              ? [{ type: 'text', text: 'result', cache_control: last }]
              : 'result',
            ...(nested ? {} : { cache_control: last })
          }
          const assistant = {
            role: 'assistant',
            content: [{
              type: 'tool_use',
              id: 'call',
              name: 'read',
              input: {},
              cache_control: longCache
            }]
          }
          const tools = [{
            name: 'read',
            input_schema: { type: 'object' },
            cache_control: longCache
          }]
          const payload: Partial<Payload> = {
            system: [],
            tools,
            cache_control: ttl === '1h' ? longCache : defaultCache,
            messages: [
              {
                role: 'user',
                content: [{
                  type: 'text',
                  text: 'First',
                  cache_control: longCache
                }]
              }, assistant,
              { role: 'user', content: [result] }
            ]
          }
          const { output } = await createHarness({
            modelId,
            fingerprintOrder: order,
            payload
          })
          await recordR2(output, `duplicate-${ttl}-${String(nested)}-${modelId}-${order}`)
          assertRequestShape(output)
          assert.equal(cacheMarkers(output).length, 5)
          assert.deepEqual(output.cache_control, payload.cache_control)
          assert.deepEqual(output.tools, tools)
          const actualAssistant = output.messages.find((message) =>
            message.role === 'assistant')
          assert.deepEqual(actualAssistant, assistant)
          assert.deepEqual(output.messages.filter((message) => message.role === 'user')
            .at(-1)?.content, [result])
          const first = output.messages.find((message) => message.role === 'user')
          assert.ok(Array.isArray(first?.content))
          const originalFirst = payload.messages?.[0]?.content as unknown[]
          assert.deepEqual(first.content.at(-1), originalFirst[0])
          assert.ok(output.system.some((block) => sha256(block.text ?? '') ===
            expected.identityHash))
          assert.ok(visibleContent(output).includes('You have been invoked in the ' +
            'following environment:'))
        })
      }
    }
    void test(`R2W1 automatic distinct slot ${modelId} ${order}`, async () => {
      const payload: Partial<Payload> = {
        system: [],
        cache_control: defaultCache,
        tools: [{
          name: 'read',
          input_schema: { type: 'object' },
          cache_control: longCache
        }],
        messages: [{
          role: 'user',
          content: [{
            type: 'text',
            text: 'First',
            cache_control: longCache
          }]
        },
        {
          role: 'assistant',
          content: [{
            type: 'tool_use',
            id: 'call',
            name: 'read',
            input: {},
            cache_control: longCache
          }]
        },
        {
          role: 'user',
          content: [{
            type: 'tool_result',
            tool_use_id: 'call',
            content: 'result'
          }]
        }]
      }
      const { output } = await createHarness({
        modelId,
        fingerprintOrder: order,
        payload
      })
      await recordR2(output, `automatic-distinct-${modelId}-${order}`)
      assertRequestShape(output)
      assert.equal(cacheMarkers(output).length, 4)
      assert.deepEqual(output.cache_control, defaultCache)
      assert.deepEqual(output.tools, payload.tools)
    })
    void test(`R2W1 mismatched automatic TTL ${modelId} ${order}`, async () => {
      const payload: Partial<Payload> = {
        system: [],
        tools: [],
        cache_control: defaultCache,
        messages: [{ role: 'user', content: 'First' },
          {
            role: 'assistant',
            content: [{
              type: 'tool_use',
              id: 'call',
              name: 'read',
              input: {}
            }]
          },
          {
            role: 'user',
            content: [{
              type: 'tool_result',
              tool_use_id: 'call',
              content: 'result',
              cache_control: longCache
            }]
          }]
      }
      await assert.rejects(createHarness({ modelId, fingerprintOrder: order, payload }),
        /Cannot preserve invalid caller cache markers/u)
    })
    void test('R2W1 automatic fifth slot is not repaired ' +
      `${modelId} ${order}`, async () => {
      const payload: Partial<Payload> = {
        system: [],
        cache_control: longCache,
        tools: [{
          name: 'read',
          input_schema: { type: 'object' },
          cache_control: longCache
        }],
        messages: [{
          role: 'user',
          content: [{
            type: 'text',
            text: 'First',
            cache_control: longCache
          }]
        },
        {
          role: 'assistant',
          content: [{
            type: 'tool_use',
            id: 'call',
            name: 'read',
            input: {},
            cache_control: longCache
          }]
        },
        {
          role: 'user',
          content: [{
            type: 'tool_result',
            tool_use_id: 'call',
            content: [{ type: 'text', text: 'nested result' }],
            cache_control: longCache
          }]
        }]
      }
      await assert.rejects(createHarness({ modelId, fingerprintOrder: order, payload }),
        /Cannot preserve invalid caller cache markers/u)
    })
    void test(`R2W2 deferred immediate SDK marker ${modelId} ${order}`, async () => {
      const payload = toolLoop(defaultCache, defaultCache)
      const lastUser = payload.messages?.findLast((message) => message.role === 'user')
      assert.ok(lastUser !== undefined && Array.isArray(lastUser.content))
      lastUser.content.push({
        type: 'text',
        text: 'Followup',
        cache_control: defaultCache
      })
      delete lastUser.content[1].cache_control
      const { output } = await createHarness({
        modelId,
        fingerprintOrder: order,
        payload
      })
      await recordR2(output, `deferred-${modelId}-${order}`)
      assertRequestShape(output)
      const tools = output.tools as Array<Record<string, unknown>>
      const original = payload.tools as Array<Record<string, unknown>>
      assert.equal(tools[1]?.cache_control, undefined)
      assert.deepEqual(tools.slice(2), original.slice(2))
      assert.deepEqual(cacheMarkers(output), [longCache, longCache, longCache])
      const user = output.messages.findLast((message) => message.role === 'user')
      assert.ok(Array.isArray(user?.content))
      assert.deepEqual(user.content.slice(0, 2), lastUser.content.slice(0, 2))
      assert.deepEqual(user.content.at(-1), {
        type: 'text',
        text: 'Followup',
        cache_control: longCache
      })
      assert.deepEqual(output.messages.at(-1)?.output_config, { effort: 'high' })
      assert.deepEqual(output.output_config, { effort: 'high', format: 'keep-top' })
    })
    void test(`R2W2 SDK tool result marker ${modelId} ${order}`, async () => {
      const payload = toolLoop(defaultCache, defaultCache)
      payload.tools = []
      const { output } = await createHarness({
        modelId,
        fingerprintOrder: order,
        payload
      })
      await recordR2(output, `result-${modelId}-${order}`)
      assertRequestShape(output)
      assert.deepEqual(cacheMarkers(output), [longCache, longCache, longCache])
      const user = output.messages.findLast((message) => message.role === 'user')
      const original = payload.messages?.findLast((message) => message.role === 'user')
      assert.ok(Array.isArray(user?.content) && Array.isArray(original?.content))
      assert.deepEqual(user.content[0], original.content[0])
      assert.deepEqual(user.content[1], {
        ...original.content[1],
        cache_control: longCache
      })
      assert.deepEqual(output.messages.at(-1)?.output_config, { effort: 'high' })
      const environment = output.messages.find((message) => message.role === 'system' &&
        !isEmptySystem(message))
      assert.deepEqual(environment?.output_config, { effort: 'low' })
    })
    void test(`R2W2 deferred custom marker remains ${modelId} ${order}`, async () => {
      const payload = toolLoop(longCache, { type: 'ephemeral', ttl: '5m' })
      payload.system = [{ type: 'text', text: 'SDK prompt', cache_control: defaultCache }]
      const tools = payload.tools as Array<Record<string, unknown>>
      tools[3].cache_control = defaultCache
      const { output } = await createHarness({
        modelId,
        fingerprintOrder: order,
        payload
      })
      await recordR2(output, `deferred-custom-${modelId}-${order}`)
      assertRequestShape(output)
      assert.deepEqual(output.tools, tools)
      const user = output.messages.findLast((message) => message.role === 'user')
      assert.deepEqual(user?.content, payload.messages?.findLast((message) =>
        message.role === 'user')?.content)
    })
    void test(`R2W2 image marker remains ${modelId} ${order}`, async () => {
      const payload = toolLoop(defaultCache, undefined)
      const original = payload.messages?.findLast((message) => message.role === 'user')
      assert.ok(Array.isArray(original?.content))
      const image = {
        type: 'image',
        source: {
          type: 'base64',
          media_type: 'image/png',
          data: 'aW1hZ2U='
        },
        cache_control: defaultCache
      }
      original.content.push(image)
      const { output } = await createHarness({
        modelId,
        fingerprintOrder: order,
        payload
      })
      await recordR2(output, `image-${modelId}-${order}`)
      assertRequestShape(output)
      const user = output.messages.findLast((message) => message.role === 'user')
      assert.deepEqual(user?.content, original.content)
    })
    for (const mode of ['none', 'long', 'explicit-short'] as const) {
      void test(`R2W2 ${mode} caller markers remain ${modelId} ${order}`, async () => {
        const marker: Cache | undefined = mode === 'none'
          ? undefined
          : mode === 'long' ? longCache : { type: 'ephemeral', ttl: '5m' }
        const payload = toolLoop(marker, marker)
        if (mode === 'explicit-short') {
          payload.system = [{
            type: 'text',
            text: 'SDK prompt',
            cache_control: defaultCache
          }]
        }
        const { output } = await createHarness({
          modelId,
          fingerprintOrder: order,
          payload
        })
        await recordR2(output, `custom-${mode}-${modelId}-${order}`)
        assertRequestShape(output)
        assert.deepEqual(output.tools, payload.tools)
        const original = payload.messages?.findLast((message) => message.role === 'user')
        const user = output.messages.findLast((message) => message.role === 'user')
        assert.deepEqual(user?.content, original?.content)
        if (mode === 'none') assert.deepEqual(cacheMarkers(output), [])
        if (mode === 'explicit-short') {
          assert.deepEqual(cacheMarkers(output), [marker, marker])
        }
      })
    }
    for (const expired of [false, true]) {
      void test(`R2W3 scoped expired=${String(expired)} ` +
        `${modelId} ${order}`, async () => {
        const scoped = {
          role: 'system',
          content: 'One-turn caller reminder',
          clear_at: 'next_user_message',
          custom_field: 'keep-scope',
          output_config: { effort: 'medium', format: { type: 'caller-format' } }
        }
        const historical = {
          role: 'system',
          content: [],
          output_config: { effort: 'low' },
          custom_field: 'keep-history'
        }
        const tail = {
          role: 'system',
          content: [],
          custom_field: 'keep-tail',
          output_config: { effort: 'high', format: { type: 'tail-format' } }
        }
        const payload: Partial<Payload> = {
          system: [{ type: 'text', text: 'SDK prompt', cache_control: defaultCache }],
          tools: [],
          output_config: { effort: 'medium', format: 'keep-top' },
          messages: [{ role: 'user', content: 'First' }, historical, scoped,
            { role: 'assistant', content: [{ type: 'text', text: 'Answer' }] },
            ...(expired ? [{ role: 'user', content: 'Later user' }] : []), tail]
        }
        const { output } = await createHarness({
          modelId,
          fingerprintOrder: order,
          payload
        })
        await recordR2(output, `scoped-${String(expired)}-${modelId}-${order}`)
        assertRequestShape(output)
        assert.deepEqual(output.messages.find((message) =>
          message.clear_at === 'next_user_message'), scoped)
        assert.ok(output.messages.some((message) => JSON.stringify(message) ===
          JSON.stringify(historical)))
        assert.deepEqual(output.messages.at(-1), tail)
        assert.deepEqual(output.output_config, { effort: 'high', format: 'keep-top' })
        const visible = visibleContent(output)
        assert.ok(visible.includes('You have been invoked in the following environment:'),
          'native environment must remain visible')
        assert.equal(visible.includes('One-turn caller reminder'), !expired)
        const nativeBlocks = output.system.filter((block) =>
          block.text?.startsWith(environmentPrefix))
        assert.equal(nativeBlocks.length, 1)
        assert.deepEqual(cacheMarkers(output), [longCache, longCache])
      })
      void test(`R2W3 scoped cache expired=${String(expired)} ` +
        `${modelId} ${order}`, async () => {
        const scoped = {
          role: 'system',
          content: 'One-turn caller reminder',
          clear_at: 'next_user_message',
          custom_field: 'keep-scope',
          output_config: { effort: 'medium', format: { type: 'caller-format' } }
        }
        const historical = {
          role: 'system',
          content: [],
          output_config: { effort: 'low' },
          custom_field: 'keep-history'
        }
        const tail = {
          role: 'system',
          content: [],
          custom_field: 'keep-tail',
          output_config: { effort: 'high', format: { type: 'tail-format' } }
        }
        const short: Cache = { type: 'ephemeral', ttl: '5m' }
        const answer = {
          role: 'assistant',
          content: [{
            type: 'text',
            text: 'Answer',
            cache_control: longCache
          }]
        }
        const later = {
          role: 'user',
          content: [{
            type: 'text',
            text: 'Later user',
            cache_control: short
          }]
        }
        const payload: Partial<Payload> = {
          system: [{ type: 'text', text: 'SDK prompt', cache_control: defaultCache }],
          tools: [],
          cache_control: expired ? defaultCache : longCache,
          output_config: { effort: 'medium', format: 'keep-top' },
          messages: [{ role: 'user', content: 'First' }, historical, scoped, answer,
            ...(expired ? [later] : []), tail]
        }
        const { output } = await createHarness({
          modelId,
          fingerprintOrder: order,
          payload
        })
        await recordR2(output, `scoped-cache-${String(expired)}-${modelId}-${order}`)
        assertRequestShape(output)
        assert.deepEqual(output.messages.find((message) =>
          message.clear_at === 'next_user_message'), scoped)
        assert.ok(output.messages.some((message) => JSON.stringify(message) ===
          JSON.stringify(historical)))
        assert.deepEqual(output.messages.at(-1), tail)
        assert.deepEqual(output.output_config, { effort: 'high', format: 'keep-top' })
        assert.deepEqual(output.cache_control, payload.cache_control)
        assert.deepEqual(cacheMarkers(output), expired
          ? [longCache, longCache, longCache, short, defaultCache]
          : [longCache, longCache, longCache, longCache])
        const visible = visibleContent(output)
        assert.ok(visible.includes('You have been invoked in the following environment:'))
        assert.equal(visible.includes('One-turn caller reminder'), !expired)
      })
    }
  }
}
function withoutCacheControls (value: unknown): unknown {
  if (Array.isArray(value)) return value.map(withoutCacheControls)
  if (!isRecord(value)) return value
  return Object.fromEntries(Object.entries(value)
    .filter(([key]) => key !== 'cache_control')
    .map(([key, item]) => [key, withoutCacheControls(item)]))
}
function withoutNullCacheControls (value: unknown): unknown {
  if (Array.isArray(value)) return value.map(withoutNullCacheControls)
  if (!isRecord(value)) return value
  return Object.fromEntries(Object.entries(value)
    .filter(([key, item]) => key !== 'cache_control' || item !== null)
    .map(([key, item]) => [key, withoutNullCacheControls(item)]))
}
async function serializedHistory (
  modelId: string, order: 'before' | 'after' | 'none',
  retention: 'short' | 'long' | 'none', fullBudget: boolean, includeSection = false
): Promise<Payload[]> {
  const temp = await mkdtemp(join(tmpdir(), 'move-r3-history-'))
  try {
    const codingRoot = dirname(runtimeRequire.resolve('./package.json'))
    const adapterPath = join(codingRoot,
      'node_modules/@earendil-works/pi-ai/dist/api/anthropic-messages.js')
    const adapter = await import(pathToFileURL(adapterPath).href) as {
      stream: (model: unknown, context: unknown, options: unknown) => {
        result: () => Promise<{ stopReason: string }>
      }
    }
    const model = {
      id: modelId,
      provider: 'anthropic',
      api: 'anthropic-messages',
      name: modelId,
      baseUrl: 'https://offline.invalid',
      input: ['text', 'image'],
      reasoning: true,
      maxTokens: 8192,
      contextWindow: 200000,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      compat: {
        supportsMidConvoEffort: true,
        supportsCacheControlOnTools: true,
        supportsLongCacheRetention: true
      }
    }
    const tools = ['one', 'two', 'three'].map((name) => ({
      name,
      description: name,
      parameters: { type: 'object', properties: {} }
    }))
    const user = (text: string): Record<string, unknown> => ({
      role: 'user',
      timestamp: 1,
      content: [{ type: 'text', text }]
    })
    const assistant = (redacted: boolean, level: string): Record<string, unknown> => ({
      role: 'assistant',
      provider: 'anthropic',
      api: 'anthropic-messages',
      model: modelId,
      providerThinkingLevel: level,
      stopReason: 'stop',
      timestamp: 1,
      content: [{
        type: 'thinking',
        thinking: redacted
          ? '[Reasoning redacted]'
          : 'Signed reasoning',
        thinkingSignature: redacted
          ? 'opaque-redacted-2'
          : 'opaque-signature-1',
        ...(redacted ? { redacted: true } : {})
      },
      { type: 'text', text: redacted ? 'Second answer' : 'First answer' }]
    })
    const histories = [
      [user('First')],
      [user('First'), assistant(false, 'low'), user('Second')],
      [user('First'), assistant(false, 'low'), user('Second'),
        assistant(true, 'medium'), user('Third')]
    ]
    const bodies: Payload[] = []
    for (let turn = 0; turn < histories.length; turn++) {
      let wire: string | undefined
      const result = await adapter.stream(model, {
        systemPrompt: 'SDK prompt',
        tools,
        messages: histories[turn]
      }, {
        apiKey: 'offline-only',
        cacheRetention: retention,
        effort: ['low', 'medium', 'high'][turn],
        maxRetries: 0,
        onPayload: async (raw: unknown) => {
          assert.ok(isRecord(raw))
          const payload = structuredClone(raw) as unknown as Payload
          if (!includeSection) {
            payload.cache_control = fullBudget || retention === 'long'
              ? longCache
              : defaultCache
          } else {
            payload.messages.splice(1, 0, {
              role: 'system',
              content: 'Persistent caller',
              clear_at: 'never'
            }, {
              role: 'system',
              content: [],
              output_config: { effort: 'low' }
            }, {
              role: 'system',
              content: [{ type: 'text', text: 'Scoped caller' }],
              clear_at: 'next_user_message'
            })
          }
          if (fullBudget) {
            payload.system = payload.system.map(({ cache_control: cache, ...block }) => {
              void cache
              return block
            })
            assert.ok(Array.isArray(payload.tools))
            for (const tool of payload.tools) {
              assert.ok(isRecord(tool))
              tool.cache_control = longCache
            }
            const lastUser = payload.messages.findLast((message) =>
              message.role === 'user')
            assert.ok(Array.isArray(lastUser?.content))
            const lastBlock = lastUser.content.at(-1)
            assert.ok(isRecord(lastBlock))
            lastBlock.cache_control = longCache
          }
          const { output } = await createHarness({
            modelId,
            fingerprintOrder: order === 'none' ? undefined : order,
            agentDir: join(temp, 'agent'),
            payload
          })
          return output
        },
        fetch: async (_input: unknown, init: { body?: unknown }) => {
          assert.ok(typeof init.body === 'string')
          wire = init.body
          throw new Error('offline serialized body captured')
        }
      }).result()
      assert.equal(result.stopReason, 'error')
      assert.ok(wire, 'SDK must serialize the request and invoke its transport')
      const body = JSON.parse(wire) as Payload
      await recordR2(body, `r3-wire-${retention}-${String(fullBudget)}-` +
        `${String(includeSection)}-${modelId}-${order}-${turn}`)
      assertRequestShape(body)
      assert.deepEqual(body.cache_control, includeSection
        ? undefined
        : fullBudget || retention === 'long' ? longCache : defaultCache)
      bodies.push(body)
    }
    return bodies
  } finally {
    await rm(temp, { recursive: true, force: true })
  }
}
function assertStableHistory (bodies: Payload[]): void {
  assert.equal(bodies.length, 3)
  for (let turn = 1; turn < bodies.length; turn++) {
    const previous = bodies[turn - 1]
    const current = bodies[turn]
    assert.equal(JSON.stringify(withoutCacheControls(current.tools)),
      JSON.stringify(withoutCacheControls(previous.tools)), 'tools prefix drift')
    assert.equal(JSON.stringify(withoutCacheControls(current.system)),
      JSON.stringify(withoutCacheControls(previous.system)), 'system prefix drift')
    const previousAssistants = previous.messages.filter((message) =>
      message.role === 'assistant').length
    const assistantIndexes = current.messages.flatMap((message, index) =>
      message.role === 'assistant' ? [index] : [])
    const nextAssistant = assistantIndexes[previousAssistants]
    assert.notEqual(nextAssistant, undefined)
    const prefix = current.messages.slice(0, nextAssistant)
    assert.equal(JSON.stringify(withoutCacheControls(prefix)),
      JSON.stringify(withoutCacheControls(previous.messages)), 'history prefix drift')
  }
  for (const body of bodies) {
    assert.equal(body.system.filter((block) =>
      block.text?.startsWith(environmentPrefix)).length, 1)
  }
  const third = JSON.stringify(bodies[2]?.messages)
  assert.ok(third.includes('opaque-signature-1'))
  assert.ok(third.includes('opaque-redacted-2'))
}
async function assertAutomaticHistory (
  modelId: string, order: 'before' | 'after' | 'none',
  retention: 'short' | 'long' | 'none', fullBudget: boolean
): Promise<void> {
  const bodies = await serializedHistory(modelId, order, retention, fullBudget)
  assertStableHistory(bodies)
  for (let turn = 0; turn < bodies.length; turn++) {
    const controls = bodies[turn].messages.filter((message) =>
      isEmptySystem(message)).map((message) => message.output_config)
    assert.deepEqual(controls, [{ effort: 'low' }, { effort: 'medium' },
      { effort: 'high' }].slice(0, turn + 1))
    if (fullBudget) {
      assert.equal(cacheMarkers(bodies[turn]).length, 5)
      const tools = bodies[turn].tools
      assert.ok(Array.isArray(tools))
      assert.equal(tools.length, 3)
      for (const tool of tools) {
        assert.ok(isRecord(tool))
        assert.deepEqual(tool.cache_control, longCache)
      }
      const users = bodies[turn].messages.filter((message) => message.role === 'user')
      const last = users.at(-1)
      assert.ok(Array.isArray(last?.content))
      assert.deepEqual(last.content.at(-1)?.cache_control, longCache)
      for (const previous of users.slice(0, -1)) {
        assert.ok(Array.isArray(previous.content))
        assert.ok(previous.content.every((block) =>
          isRecord(block) && block.cache_control === undefined))
      }
    }
  }
}
for (const modelId of Object.keys(modelFixtures)) {
  for (const retention of ['short', 'long', 'none'] as const) {
    for (const fullBudget of [false, true]) {
      void test(`R3WARN1 wire Move-only ${modelId} ${retention} ` +
        `full=${String(fullBudget)}`,
      async () => await assertAutomaticHistory(modelId, 'none', retention, fullBudget))
    }
  }
  void test(`R3WARN1 Move retains automatic effort controls ${modelId}`, async () => {
    for (const followup of [false, true]) {
      for (const callerSystem of [false, true]) {
        const payload = { ...sdkBody('none', followup), cache_control: defaultCache }
        if (callerSystem) {
          payload.messages?.splice(1, 0, { role: 'system', content: 'Caller' })
        }
        const original = structuredClone(payload)
        const { output } = await createHarness({ modelId, payload })
        assertRequestShape(output)
        assert.deepEqual(output.messages.slice(1), payload.messages?.slice(1))
        assert.deepEqual(payload, original)
        assert.equal(output.system.filter((block) =>
          block.text?.startsWith(environmentPrefix)).length, 1)
      }
    }
  })
  for (const order of ['before', 'after'] as const) {
    for (const retention of ['short', 'long', 'none'] as const) {
      for (const fullBudget of [false, true]) {
        void test(`R3WARN1 wire ${modelId} ${order} ${retention} ` +
          `full=${String(fullBudget)}`,
        async () => await assertAutomaticHistory(modelId, order, retention, fullBudget))
      }
      void test(`R3WARN2 wire sections ${modelId} ${order} ${retention}`, async () => {
        const bodies = await serializedHistory(modelId, order, retention, false, true)
        assertStableHistory(bodies)
        for (const body of bodies) {
          assert.deepEqual(body.messages.slice(1, 4), [
            { role: 'system', content: 'Persistent caller', clear_at: 'never' },
            { role: 'system', content: [], output_config: { effort: 'low' } },
            {
              role: 'system',
              content: [{ type: 'text', text: 'Scoped caller' }],
              clear_at: 'next_user_message'
            }
          ])
        }
      })
    }
    const persistentA = {
      role: 'system',
      content: 'Persistent A',
      clear_at: 'never',
      output_config: { effort: 'low' }
    }
    const persistentB = {
      role: 'system',
      content: [{ type: 'text', text: 'Persistent B' }],
      clear_at: 'never'
    }
    const persistentC = {
      role: 'system',
      content: 'Persistent C',
      output_config: { effort: 'high' }
    }
    const scoped = {
      role: 'system',
      content: [{ type: 'text', text: 'Scoped' }],
      clear_at: 'next_user_message'
    }
    const control = { role: 'system', content: [], output_config: { effort: 'medium' } }
    const toolAddition = {
      role: 'system',
      content: [{
        type: 'tool_addition',
        tool: { type: 'tool_reference', name: 'lazy' }
      }]
    }
    const sections = [[persistentA, persistentB], [persistentA, persistentB, persistentC],
      [persistentA, control, persistentB, control, persistentC],
      [scoped, control, persistentB], [persistentA, control, scoped],
      [persistentA, control, toolAddition],
      [persistentA, { ...persistentB, custom_field: 'keep B' }]]
    for (const [index, section] of sections.entries()) {
      for (const tail of ['end', 'assistant', 'later'] as const) {
        for (const automatic of [false, true]) {
          void test(`R3WARN2 section ${index} ${tail} auto=${String(automatic)} ` +
            `${modelId} ${order}`,
          async () => {
            const messages: Message[] = [{ role: 'user', content: 'First' }, ...section]
            if (tail !== 'end') messages.push({ role: 'assistant', content: 'Answer' })
            if (tail === 'later') {
              messages.push({ role: 'user', content: 'Later' },
                control, scoped, control, persistentC,
                { role: 'assistant', content: 'Later answer' })
            }
            const payload: Partial<Payload> = {
              system: [],
              tools: [{
                name: 'lazy',
                defer_loading: true,
                input_schema: { type: 'object', properties: {} }
              }],
              ...(automatic ? { cache_control: defaultCache } : {}),
              messages
            }
            const original = structuredClone(payload)
            const { output } = await createHarness({
              modelId,
              fingerprintOrder: order,
              payload
            })
            assertRequestShape(output)
            assert.deepEqual(output.messages.slice(1), messages.slice(1))
            assert.deepEqual(output.cache_control, payload.cache_control)
            assert.deepEqual(payload, original)
            assert.equal(output.system.filter((block) =>
              block.text?.startsWith(environmentPrefix)).length, 1)
            await recordR2(output, `r3-sections-${modelId}-${order}-${index}-${tail}-` +
                String(automatic))
          })
        }
      }
    }
    for (const [index, section] of [[persistentB], [persistentB, scoped],
      [scoped, persistentB]].entries()) {
      for (const automatic of [false, true]) {
        void test(`R3WARN2 initial effort ${index} auto=${String(automatic)} ` +
          `${modelId} ${order}`,
        async () => {
          const tail = {
            role: 'system',
            content: [],
            output_config: { effort: 'high' }
          }
          const messages: Message[] = [
            { role: 'user', content: 'First' }, ...section, tail
          ]
          const payload: Partial<Payload> = {
            system: [],
            tools: [],
            messages,
            ...(automatic ? { cache_control: defaultCache } : {})
          }
          const { output } = await createHarness({
            modelId,
            fingerprintOrder: order,
            payload
          })
          assertRequestShape(output)
          assert.deepEqual(output.messages.slice(1), messages.slice(1))
          assert.equal(output.system.filter((block) =>
            block.text?.startsWith(environmentPrefix)).length, 1)
        })
      }
    }
    void test(`R3WARN2 later server-tool section ${modelId} ${order}`, async () => {
      const messages: Message[] = [{ role: 'user', content: 'First' },
        {
          role: 'assistant',
          content: [
            { type: 'server_tool_use', id: 'server', name: 'search', input: {} }
          ]
        }, persistentA, control, persistentB,
        { role: 'assistant', content: 'Server answer' }]
      const { output } = await createHarness({
        modelId,
        fingerprintOrder: order,
        payload: { system: [], tools: [], messages }
      })
      assertRequestShape(output)
      assert.deepEqual(output.messages.slice(-5), messages.slice(-5))
    })
    for (const later of [false, true]) {
      void test(`R3WARN2 invalid section before user later=${String(later)} ` +
        `${modelId} ${order}`,
      async () => {
        const messages: Message[] = [{ role: 'user', content: 'First' },
          ...(later
            ? [{ role: 'assistant', content: 'Answer' },
                { role: 'user', content: 'Second' }]
            : []),
          { role: 'system', content: 'A' },
          { role: 'system', content: [], output_config: { effort: 'low' } },
          { role: 'system', content: 'B' }, { role: 'user', content: 'Invalid' }]
        const payload: Partial<Payload> = { system: [], tools: [], messages }
        const original = structuredClone(payload)
        assert.throws(() => assertSystemPlacement(messages),
          /followed by an assistant/u)
        const error = new RegExp('Cannot preserve (?:an invalid system reminder ' +
            'between users|consecutive nonempty system reminders)', 'u')
        await assert.rejects(
          createHarness({ modelId, fingerprintOrder: order, payload }), error
        )
        assert.deepEqual(payload, original)
      })
    }
  }
}
void test('R3WARN2 placement checker validates whole sections and server tools', () => {
  const control = { role: 'system', content: [], output_config: { effort: 'low' } }
  const section = [{ role: 'system', content: 'A' }, control,
    {
      role: 'system',
      content: [{
        type: 'tool_addition',
        tool: {
          type: 'tool_reference', name: 'lazy'
        }
      }]
    }]
  assert.doesNotThrow(() => assertSystemPlacement([
    { role: 'user', content: 'First' }, ...section, { role: 'assistant', content: 'A' }
  ]))
  assert.doesNotThrow(() => assertSystemPlacement([
    { role: 'user', content: 'First' }, {
      role: 'assistant',
      content: [
        { type: 'server_tool_use', id: 'server', name: 'search', input: {} }
      ]
    }, ...section
  ]))
  assert.throws(() => assertSystemPlacement([
    { role: 'user', content: 'First' }, ...section, { role: 'user', content: 'Bad' }
  ]), /followed by an assistant/u)
  assert.throws(() => assertSystemPlacement([
    { role: 'user', content: 'First' }, { role: 'assistant', content: 'A' }, ...section
  ]), /system must follow a user/u)
})
void test('instruction placeholders replace dollars and markers ' +
  'literally once', async () => {
  const globalText = "GLOBAL $$ $& $` $' {{crs_local_instructions}} {{crs_cwd}} END"
  const localText = "LOCAL $$ $& $` $' {{crs_global_instructions}} END"
  for (const modelId of ['claude-opus-5-5', 'unknown-claude-model']) {
    const { output } = await createHarness({
      modelId,
      globalInstructions: globalText,
      localInstructions: localText
    })
    const text = JSON.stringify(output.messages)
    assert.ok(text.includes(globalText))
    assert.ok(text.includes(localText))
  }
})
async function managedInitialWire (
  modelId: string, fingerprintOrder: 'before' | 'after' | undefined,
  retention: 'short' | 'long' | 'none'
): Promise<void> {
  const codingRoot = dirname(runtimeRequire.resolve('./package.json'))
  const adapterPath = join(codingRoot,
    'node_modules/@earendil-works/pi-ai/dist/api/anthropic-messages.js')
  const adapter = await import(pathToFileURL(adapterPath).href) as {
    stream: (model: unknown, context: unknown, options: unknown) => {
      result: () => Promise<{ stopReason: string }>
    }
  }
  const model = {
    id: modelId,
    provider: 'anthropic',
    api: 'anthropic-messages',
    name: modelId,
    baseUrl: 'https://offline.invalid',
    input: ['text'],
    reasoning: true,
    maxTokens: 8192,
    contextWindow: 200000,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    compat: { supportsMidConvoEffort: true, supportsLongCacheRetention: true }
  }
  let wire: string | undefined
  const result = await adapter.stream(model, {
    systemPrompt: 'SDK prompt',
    tools: [],
    messages: [{ role: 'user', content: 'First', timestamp: 1 }]
  }, {
    apiKey: 'offline-only',
    cacheRetention: retention,
    effort: 'medium',
    maxRetries: 0,
    onPayload: async (raw: unknown) => {
      assert.ok(isRecord(raw))
      const payload = structuredClone(raw) as unknown as Payload
      assert.deepEqual(payload.messages.at(-1), {
        role: 'system',
        content: [],
        output_config: { effort: 'medium' }
      })
      const { output } = await createHarness({ modelId, fingerprintOrder, payload })
      return output
    },
    fetch: async (_input: unknown, init: { body?: unknown }) => {
      assert.ok(typeof init.body === 'string')
      wire = init.body
      throw new Error('offline serialized body captured')
    }
  }).result()
  assert.equal(result.stopReason, 'error')
  assert.ok(wire, 'SDK must serialize the request and invoke its offline transport')
  const body = JSON.parse(wire) as Payload
  assertRequestShape(body)
  const environment = body.messages.find((message) => message.role === 'system' &&
    !isEmptySystem(message))
  assert.ok(environment)
  assert.deepEqual(environment.output_config, { effort: 'medium' })
  assert.deepEqual(body.output_config, { effort: 'medium' })
}
async function unmanagedWireHistory (
  modelId: string, order: 'before' | 'after' | 'none',
  retention: 'short' | 'long' | 'none', automatic: boolean
): Promise<Payload[]> {
  const codingRoot = dirname(runtimeRequire.resolve('./package.json'))
  const adapterPath = join(codingRoot,
    'node_modules/@earendil-works/pi-ai/dist/api/anthropic-messages.js')
  const adapter = await import(pathToFileURL(adapterPath).href) as {
    stream: (model: unknown, context: unknown, options: unknown) => {
      result: () => Promise<{ stopReason: string }>
    }
  }
  const model = {
    id: modelId,
    provider: 'anthropic',
    api: 'anthropic-messages',
    name: modelId,
    baseUrl: 'https://offline.invalid',
    input: ['text', 'image'],
    reasoning: true,
    maxTokens: 8192,
    contextWindow: 200000,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    compat: {
      forceAdaptiveThinking: true,
      supportsMidConvoEffort: false,
      supportsCacheControlOnTools: true,
      supportsLongCacheRetention: true
    }
  }
  const tools = [{
    name: 'read',
    description: 'read',
    parameters: { type: 'object', properties: {} }
  }]
  const user = (text: string): Record<string, unknown> => ({
    role: 'user',
    timestamp: 1,
    content: [{ type: 'text', text }]
  })
  const assistant = (redacted: boolean): Record<string, unknown> => ({
    role: 'assistant',
    provider: 'anthropic',
    api: 'anthropic-messages',
    model: modelId,
    stopReason: 'stop',
    timestamp: 1,
    content: [{
      type: 'thinking',
      thinking: redacted
        ? '[Reasoning redacted]'
        : 'Signed reasoning',
      thinkingSignature: redacted
        ? 'opaque-redacted-2'
        : 'opaque-signature-1',
      ...(redacted ? { redacted: true } : {})
    },
    { type: 'text', text: redacted ? 'Second answer' : 'First answer' }]
  })
  const histories = [[user('First')], [user('First'), assistant(false), user('Second')],
    [user('First'), assistant(false), user('Second'), assistant(true), user('Third')]]
  const efforts = ['low', 'medium', 'high']
  const bodies: Payload[] = []
  for (let turn = 0; turn < histories.length; turn++) {
    let wire: string | undefined
    const result = await adapter.stream(model, {
      systemPrompt: 'SDK prompt',
      tools,
      messages: histories[turn]
    }, {
      apiKey: 'offline-only',
      cacheRetention: retention,
      thinkingEnabled: true,
      effort: efforts[turn],
      maxRetries: 0,
      onPayload: async (raw: unknown) => {
        assert.ok(isRecord(raw))
        const payload = structuredClone(raw) as unknown as Payload
        assert.deepEqual(payload.output_config, { effort: efforts[turn] })
        assert.ok(payload.messages.every((message) => message.role !== 'system'))
        if (automatic) {
          payload.cache_control = retention === 'long' ? longCache : defaultCache
        }
        const { output } = await createHarness({
          modelId,
          fingerprintOrder: order === 'none' ? undefined : order,
          agentDir: join(tmpdir(), 'r4-unmanaged-agent'),
          payload
        })
        return output
      },
      fetch: async (_input: unknown, init: { body?: unknown }) => {
        assert.ok(typeof init.body === 'string')
        wire = init.body
        throw new Error('offline serialized body captured')
      }
    }).result()
    assert.equal(result.stopReason, 'error')
    assert.ok(wire, 'SDK must serialize the request and invoke its offline transport')
    const body = JSON.parse(wire) as Payload
    assertRequestShape(body)
    assert.deepEqual(body.output_config, { effort: efforts[turn] })
    assert.ok(body.messages.every((message) => message.output_config === undefined))
    bodies.push(body)
  }
  return bodies
}
function assertUnmanagedPrefix (bodies: Payload[]): void {
  assert.equal(bodies.length, 3)
  for (let turn = 1; turn < bodies.length; turn++) {
    const previous = bodies[turn - 1]
    const current = bodies[turn]
    assert.deepEqual(withoutCacheControls(current.tools),
      withoutCacheControls(previous.tools), 'tools prefix drift')
    assert.deepEqual(withoutCacheControls(current.system),
      withoutCacheControls(previous.system), 'system prefix drift')
    const assistantCount = previous.messages.filter((message) =>
      message.role === 'assistant').length
    const nextAssistant = current.messages.flatMap((message, index) =>
      message.role === 'assistant' ? [index] : [])[assistantCount]
    assert.notEqual(nextAssistant, undefined)
    assert.deepEqual(withoutCacheControls(current.messages.slice(0, nextAssistant)),
      withoutCacheControls(previous.messages), 'history prefix drift')
  }
  const assistants = bodies[2].messages.filter((message) => message.role === 'assistant')
  assert.ok(Array.isArray(assistants[0]?.content))
  assert.ok(Array.isArray(assistants[1]?.content))
  assert.deepEqual(assistants[0].content[0], {
    type: 'thinking',
    thinking: 'Signed reasoning',
    signature: 'opaque-signature-1'
  })
  assert.deepEqual(assistants[1].content[0], {
    type: 'redacted_thinking',
    data: 'opaque-redacted-2'
  })
}
function inlineAddition (cache: unknown): Message {
  return {
    role: 'system',
    content: [{
      type: 'tool_addition',
      tool: {
        type: 'tool_definition',
        definition: {
          name: 'inline',
          input_schema: {
            type: 'object',
            cache_control: { type: 'not-a-marker' },
            properties: { cache_control: { type: 'string' } }
          },
          ...(cache === undefined ? {} : { cache_control: cache })
        }
      }
    }]
  }
}
for (const modelId of Object.keys(modelFixtures)) {
  for (const order of ['none', 'before', 'after'] as const) {
    for (const retention of ['short', 'long', 'none'] as const) {
      for (const automatic of [false, true]) {
        void test(`R4WARN1 unmanaged wire ${modelId} ${order} ${retention} ` +
          `auto=${String(automatic)}`,
        async () => assertUnmanagedPrefix(await unmanagedWireHistory(
          modelId, order, retention, automatic
        )))
      }
    }
    const fingerprintOrder = order === 'none' ? undefined : order
    for (const retention of ['short', 'long', 'none'] as const) {
      void test(`R4WARN1 managed initial SDK control ${modelId} ${order} ${retention}`,
        async () => await managedInitialWire(modelId, fingerprintOrder, retention))
    }
    for (const marker of [longCache, { type: 'ephemeral', ttl: '5m' }] as const) {
      void test(`R4WARN2 inline budget ${modelId} ` +
        `${order} ${String(marker.ttl)}`, async () => {
        const addition = inlineAddition(marker)
        const user = {
          role: 'user',
          content: [{
            type: 'text',
            text: 'First',
            cache_control: marker
          }]
        }
        const tools = [{
          name: 'read',
          input_schema: { type: 'object' },
          cache_control: longCache
        }]
        const payload: Partial<Payload> = {
          tools,
          messages: [user, addition],
          system: [
            { type: 'text', text: 'SDK prompt', cache_control: marker }
          ]
        }
        const { output } = await createHarness({ modelId, fingerprintOrder, payload })
        assertRequestShape(output)
        assert.deepEqual(output.tools, tools)
        assert.deepEqual(output.messages.at(-1), addition)
        const first = output.messages.find((message) => message.role === 'user')
        assert.ok(Array.isArray(first?.content))
        assert.deepEqual(first.content.at(-1), user.content[0])
        assert.equal(cacheMarkers(output).length, 4)
      })
    }
    void test(`R4WARN2 automatic inline target ${modelId} ${order}`, async () => {
      const tools = ['one', 'two', 'three'].map((name) => ({
        name,
        input_schema: { type: 'object' },
        cache_control: longCache
      }))
      const addition = inlineAddition(longCache)
      const payload: Partial<Payload> = {
        system: [],
        tools,
        cache_control: longCache,
        messages: [{ role: 'user', content: 'First' }, addition]
      }
      const { output } = await createHarness({ modelId, fingerprintOrder, payload })
      assertRequestShape(output)
      assert.deepEqual(output.tools, tools)
      assert.deepEqual(output.messages.at(-1), addition)
      assert.deepEqual(output.cache_control, longCache)
      assert.equal(cacheMarkers(output).length, 5)
    })
    void test('R4WARN2 later inline section and distinct automatic slot ' +
      `${modelId} ${order}`,
    async () => {
      const tools = ['one', 'two', 'three'].map((name) => ({
        name,
        input_schema: { type: 'object' },
        cache_control: longCache
      }))
      const addition = inlineAddition(longCache)
      const messages: Message[] = [{ role: 'user', content: 'First' },
        {
          role: 'assistant',
          content: [{
            type: 'server_tool_use',
            id: 'server',
            name: 'search',
            input: { cache_control: { type: 'not-a-marker' } }
          }]
        },
        { role: 'system', content: 'Caller section' }, addition,
        { role: 'system', content: [{ type: 'text', text: 'After addition' }] }]
      await assert.rejects(createHarness({
        modelId,
        fingerprintOrder,
        payload: {
          system: [], tools, messages, cache_control: longCache
        }
      }))
      const valid = await createHarness({
        modelId,
        fingerprintOrder,
        payload: {
          system: [], tools: tools.slice(0, 2), messages, cache_control: longCache
        }
      })
      assertRequestShape(valid.output)
      assert.deepEqual(valid.output.messages.slice(-4), messages.slice(-4))
      assert.equal(cacheMarkers(valid.output).length, 4)
    })
    void test(`R4WARN2 inline TTL ordering ${modelId} ${order}`, async () => {
      const addition = inlineAddition(longCache)
      const user = {
        role: 'user',
        content: [{
          type: 'text',
          text: 'First',
          cache_control: defaultCache
        }]
      }
      await assert.rejects(createHarness({
        modelId,
        fingerprintOrder,
        payload: { system: [], tools: [], messages: [user, addition] }
      }))
    })
    void test(`R4WARN2 inline automatic mismatch ${modelId} ${order}`, async () => {
      await assert.rejects(createHarness({
        modelId,
        fingerprintOrder,
        payload: {
          system: [],
          tools: [],
          cache_control: defaultCache,
          messages: [{ role: 'user', content: 'First' }, inlineAddition(longCache)]
        }
      }))
    })
    void test(`R4WARN2 inline without marker ${modelId} ${order}`, async () => {
      const addition = inlineAddition(undefined)
      const { output } = await createHarness({
        modelId,
        fingerprintOrder,
        payload: {
          system: [], tools: [], messages: [{ role: 'user', content: 'First' }, addition]
        }
      })
      assertRequestShape(output)
      assert.deepEqual(output.messages.at(-1), addition)
      assert.deepEqual(cacheMarkers(output), [])
    })
    for (const location of ['top', 'system', 'tool', 'user', 'image', 'result',
      'nested-result', 'inline', 'scoped'] as const) {
      void test(`R4WARN4 nullable ${location} ${modelId} ${order}`, async () => {
        const text = {
          type: 'text',
          text: 'First',
          ...(location === 'user' ? { cache_control: null } : {})
        }
        const image = {
          type: 'image',
          source: {
            type: 'base64',
            media_type: 'image/png',
            data: 'aW1hZ2U='
          },
          ...(location === 'image' ? { cache_control: null } : {})
        }
        const nested = {
          type: 'text',
          text: 'Result',
          ...(location === 'nested-result' ? { cache_control: null } : {})
        }
        const result = {
          type: 'tool_result',
          tool_use_id: 'call',
          content: [nested],
          ...(location === 'result' ? { cache_control: null } : {})
        }
        const tools = [{
          name: 'read',
          input_schema: { type: 'object' },
          ...(location === 'tool' ? { cache_control: null } : {})
        }]
        const section = location === 'inline'
          ? [inlineAddition(null)]
          : location === 'scoped'
            ? [{
                role: 'system',
                clear_at: 'next_user_message',
                content: [{ type: 'text', text: 'Scoped', cache_control: null }]
              }]
            : []
        const messages: Message[] = [{ role: 'user', content: [text, image] }, ...section,
          {
            role: 'assistant',
            content: [{
              type: 'tool_use',
              id: 'call',
              name: 'read',
              input: {
                cache_control: { type: 'not-a-marker' },
                data: inlineAddition({ type: 'not-a-marker' })
              }
            }]
          },
          { role: 'user', content: [result] }]
        const payload: Partial<Payload> = {
          tools,
          messages,
          system: [{
            type: 'text',
            text: 'SDK prompt',
            ...(location === 'system' ? { cache_control: null } : {})
          }],
          ...(location === 'top' ? { cache_control: null } : {})
        }
        const { output } = await createHarness({ modelId, fingerprintOrder, payload })
        assertRequestShape(output)
        assert.deepEqual(cacheMarkers(output), [])
        assert.deepEqual(output.tools, tools)
        assert.deepEqual(output.messages.slice(-messages.length + 1), messages.slice(1))
        const first = output.messages.find((message) => message.role === 'user')
        assert.ok(Array.isArray(first?.content))
        assert.ok(first.content.length > 2)
        assert.deepEqual(first.content.slice(-2), [text, image])
        assert.ok(visibleContent(output).includes('You have been invoked in the ' +
          'following environment:'))
        if (location === 'top') assert.equal(output.cache_control, null)
        if (section.length === 0) {
          assert.ok(output.messages.some((message) => message.role === 'system' &&
            !isEmptySystem(message) && systemText(message).startsWith(environmentPrefix)))
        }
      })
    }
    void test(`R4WARN4 nullable automatic last block ${modelId} ${order}`, async () => {
      const tools = ['one', 'two', 'three'].map((name) => ({
        name,
        input_schema: { type: 'object' },
        cache_control: longCache
      }))
      const last = { type: 'text', text: 'First', cache_control: null }
      const payload: Partial<Payload> = {
        system: [],
        tools,
        cache_control: longCache,
        messages: [{ role: 'user', content: [last] }]
      }
      const { output } = await createHarness({ modelId, fingerprintOrder, payload })
      assertRequestShape(output)
      assert.deepEqual(output.tools, tools)
      const first = output.messages.find((message) => message.role === 'user')
      assert.ok(Array.isArray(first?.content))
      assert.deepEqual(first.content.at(-1), last)
      assert.equal(cacheMarkers(output).length, 4)
      await assert.rejects(createHarness({
        modelId,
        fingerprintOrder,
        payload: {
          ...payload,
          tools: [...tools, {
            name: 'four',
            input_schema: { type: 'object' },
            cache_control: longCache
          }]
        }
      }))
    })
    void test(`R4WARN4 nullable top preserves observed effort ${modelId} ${order}`,
      async () => {
        const control = { role: 'system', content: [], output_config: { effort: 'low' } }
        const { output } = await createHarness({
          modelId,
          fingerprintOrder,
          payload: {
            system: [],
            tools: [],
            cache_control: null,
            output_config: { effort: 'low' },
            messages: [{ role: 'user', content: 'First' }, control]
          }
        })
        assertRequestShape(output)
        assert.equal(output.cache_control, null)
        assert.deepEqual(cacheMarkers(output), [])
        const environment = output.messages.find((message) => message.role === 'system' &&
          !isEmptySystem(message))
        assert.ok(environment)
        assert.deepEqual(environment.output_config, { effort: 'low' })
      })
    void test('R4WARN4 nullable inline automatic target ' +
      `${modelId} ${order}`, async () => {
      const tools = ['one', 'two', 'three'].map((name) => ({
        name,
        input_schema: { type: 'object' },
        cache_control: longCache
      }))
      const addition = inlineAddition(null)
      const { output } = await createHarness({
        modelId,
        fingerprintOrder,
        payload: {
          system: [],
          tools,
          cache_control: longCache,
          messages: [{ role: 'user', content: 'First' }, addition]
        }
      })
      assertRequestShape(output)
      assert.deepEqual(output.tools, tools)
      assert.deepEqual(output.messages.at(-1), addition)
      assert.equal(cacheMarkers(output).length, 4)
    })
    void test('R4WARN4 nullable with native SDK default ' +
      `${modelId} ${order}`, async () => {
      const base = sdkBody('default', true)
      const nullable = structuredClone(base)
      nullable.cache_control = null
      const tools = nullable.tools as Array<Record<string, unknown>>
      tools.unshift({
        name: 'nullable',
        input_schema: { type: 'object' },
        cache_control: null
      })
      const messages = nullable.messages
      assert.ok(messages)
      const first = messages.find((message) => message.role === 'user')
      assert.ok(first)
      first.content = [{ type: 'text', text: 'First', cache_control: null }]
      const equivalent = structuredClone(nullable)
      delete equivalent.cache_control
      const equivalentTools = equivalent.tools as Array<Record<string, unknown>>
      delete equivalentTools[0].cache_control
      const equivalentFirst = equivalent.messages?.find((message) =>
        message.role === 'user')
      assert.ok(Array.isArray(equivalentFirst?.content))
      delete equivalentFirst.content[0].cache_control
      const agentDir = join(tmpdir(), 'r4-default-agent')
      const actual = await createHarness({
        modelId,
        fingerprintOrder,
        agentDir,
        payload: nullable
      })
      const expectedOutput = await createHarness({
        modelId,
        fingerprintOrder,
        agentDir,
        payload: equivalent
      })
      assertRequestShape(actual.output)
      assert.deepEqual(withoutNullCacheControls(actual.output), expectedOutput.output)
      assert.deepEqual(cacheMarkers(actual.output), cacheMarkers(expectedOutput.output))
      assert.equal(actual.output.cache_control, null)
      const actualTools = actual.output.tools as Array<Record<string, unknown>>
      assert.equal(actualTools[0].cache_control, null)
      const actualFirst = actual.output.messages.find((message) =>
        message.role === 'user')
      assert.ok(Array.isArray(actualFirst?.content))
      assert.equal(actualFirst.content.at(-1)?.cache_control, null)
    })
  }
}
