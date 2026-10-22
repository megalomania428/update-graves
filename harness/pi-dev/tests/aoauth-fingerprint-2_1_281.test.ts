// cspell:ignore convo postchecks
/* eslint @typescript-eslint/promise-function-async: 0 */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { setImmediate } from 'node:timers/promises'
import { test, type TestContext } from 'node:test'
import {
  compact, generateBranchSummary, getAgentDir, prepareBranchEntries, SettingsManager
} from '@earendil-works/pi-coding-agent'
import type {
  CompactionResult, ExtensionAPI, ExtensionContext, ModelRegistry,
  SessionBeforeCompactEvent, SessionBeforeTreeEvent, SessionEntry
} from '@earendil-works/pi-coding-agent'
import {
  createAssistantMessageEventStream
} from '@earendil-works/pi-ai'
import type {
  Api, AssistantMessage, AssistantMessageEventStream, Context, Model, Provider,
  SimpleStreamOptions, Usage
} from '@earendil-works/pi-ai'
import { anthropicProvider } from '@earendil-works/pi-ai/providers/anthropic'
import fingerprintFactory, {
  applyFingerprintHeaders, applyFingerprintPayload, buildHashFor
} from '../extensions/aoauth-fingerprint-2_1_281'
import {
  createSummaryStream, fileDetails, isSummaryPayload, mergeHeaders, ownDetails,
  prepareSummaryCompaction, prepareSummaryEntries, readSummarySettings,
  registerSummaryHooks, waitOrAbort
} from '../lib/aoauth-summary-2_1_281'
import type { FingerprintDependencies } from '../lib/aoauth-summary-2_1_281'
import ordinary from './fixtures/ordinary-281.json'
type StreamFn = NonNullable<Parameters<typeof compact>[7]>
type Auth = Awaited<ReturnType<ModelRegistry['getApiKeyAndHeaders']>>
type BeforeEvent = SessionBeforeCompactEvent | SessionBeforeTreeEvent
type Hook = (event: never, ctx: ExtensionContext) => unknown
interface HookResult {
  cancel?: boolean
  compaction?: CompactionResult
  summary?: { summary: string, details?: unknown, usage?: Usage }
  label?: string
  fromHook?: boolean
}
interface Overrides {
  compact?: typeof compact
  generateBranchSummary?: typeof generateBranchSummary
}
interface NativeGenerators {
  compact: typeof compact
  generateBranchSummary: typeof generateBranchSummary
}
const model: Model<'anthropic-messages'> = {
  id: 'claude-sonnet-5',
  provider: 'anthropic',
  api: 'anthropic-messages',
  name: 'Offline model',
  baseUrl: 'https://offline.invalid',
  reasoning: true,
  input: ['text'],
  contextWindow: 200000,
  maxTokens: 8192,
  cost: { input: 1, output: 2, cacheRead: 3, cacheWrite: 4 }
}
const usage: Usage = {
  input: 11,
  output: 7,
  cacheRead: 3,
  cacheWrite: 2,
  totalTokens: 23,
  cost: { input: 0.01, output: 0.02, cacheRead: 0.03, cacheWrite: 0.04, total: 0.1 }
}
const fingerprint: FingerprintDependencies = {
  sessionId: 'offline-factory-session',
  applyHeaders: applyFingerprintHeaders,
  applyPayload: applyFingerprintPayload
}
function isRecord (value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
// Partial fakes are typed at construction and fail on every unexpected API access.
function strictFake<T extends object> (partial: Partial<T>): T {
  return new Proxy(partial, {
    get (target, key, receiver) {
      if (!Reflect.has(target, key)) {
        throw new Error(`Unexpected fake API: ${String(key)}`)
      }
      return Reflect.get(target, key, receiver)
    }
  }) as T
}
function isAnthropicModel (value: Model<Api>): value is Model<'anthropic-messages'> {
  return value.api === 'anthropic-messages'
}
function terminal (
  reason: AssistantMessage['stopReason'] = 'stop', text = 'Offline summary'
): AssistantMessage {
  return {
    role: 'assistant',
    api: model.api,
    provider: model.provider,
    model: model.id,
    timestamp: 0,
    stopReason: reason,
    usage: structuredClone(usage),
    content: text !== '' ? [{ type: 'text', text }] : [],
    ...(reason === 'error' ? { errorMessage: '400 fake-secret-not-for-ui' } : {})
  }
}
function userEntry (id = 'user', text = 'Synthetic conversation'): SessionEntry {
  return {
    type: 'message',
    id,
    parentId: null,
    timestamp: '2026-01-01T00:00:00Z',
    message: { role: 'user', content: text, timestamp: 0 }
  }
}
function toolEntry (name: string, path: string, id = name): SessionEntry {
  return {
    type: 'message',
    id,
    parentId: null,
    timestamp: '2026-01-01T00:00:00Z',
    message: {
      ...terminal(),
      content: [
        { type: 'toolCall', id: 'call-' + id, name, arguments: { path } }
      ]
    }
  }
}
function summaryEntry (
  type: 'compaction' | 'branch_summary', id: string, details: unknown,
  fromHook = true, firstKeptEntryId = 'user'
): SessionEntry {
  const base = {
    id,
    parentId: null,
    timestamp: '2026-01-01T00:00:00Z',
    summary: 'Earlier synthetic summary',
    fromHook,
    details
  }
  return type === 'compaction'
    ? { ...base, type, firstKeptEntryId, tokensBefore: 123 }
    : { ...base, type, fromId: 'old-leaf' }
}
function own (readFiles: string[], modifiedFiles: string[] = []): {
  readFiles: string[]
  modifiedFiles: string[]
  aoauthFingerprintSummary: number
} {
  return { readFiles, modifiedFiles, aoauthFingerprintSummary: 1 }
}
function compactEvent (
  reason: SessionBeforeCompactEvent['reason'] = 'manual'
): SessionBeforeCompactEvent {
  return {
    type: 'session_before_compact',
    reason,
    willRetry: reason === 'overflow',
    customInstructions: 'Preserve the offline decision',
    signal: new AbortController().signal,
    branchEntries: [userEntry(), userEntry('kept', 'Retained suffix')],
    preparation: {
      firstKeptEntryId: 'kept',
      tokensBefore: 12345,
      messagesToSummarize: [{
        role: 'user',
        content: 'Synthetic conversation',
        timestamp: 0
      }],
      turnPrefixMessages: [],
      isSplitTurn: false,
      settings: { enabled: true, reserveTokens: 2048, keepRecentTokens: 100 },
      fileOps: {
        read: new Set(['read.ts', 'edited.ts']),
        edited: new Set(['edited.ts']),
        written: new Set(['written.ts'])
      }
    }
  }
}
function treeEvent (entries: SessionEntry[] = [userEntry()]): SessionBeforeTreeEvent {
  return {
    type: 'session_before_tree',
    signal: new AbortController().signal,
    preparation: {
      targetId: 'target',
      oldLeafId: 'old-leaf',
      commonAncestorId: 'ancestor',
      entriesToSummarize: entries,
      userWantsSummary: true,
      customInstructions: 'Keep the branch decision',
      replaceInstructions: false,
      label: 'unchanged-label'
    }
  }
}
function textPayload (context: Context, requestModel: Model<Api>): {
  model: string
  max_tokens: number
  messages: Array<{
    role: string
    content: Context['messages'][number]['content'] | undefined
  } | {
    role: string
    content: never[]
    output_config: { effort: string }
  }>
  system: Array<{ type: string, text: string | undefined }>
} {
  const compat = requestModel.compat
  const managed = compat !== undefined && 'supportsMidConvoEffort' in compat &&
    compat.supportsMidConvoEffort === true
  return {
    model: requestModel.id,
    max_tokens: 1024,
    messages: [
      { role: 'user', content: context.messages[0]?.content },
      ...(managed
        ? [{ role: 'system', content: [], output_config: { effort: 'high' } }]
        : [])
    ],
    system: [
      { type: 'text', text: "You are Claude Code, Anthropic's official CLI for Claude." },
      { type: 'text', text: context.systemPrompt }
    ]
  }
}
class Harness {
  readonly hooks = new Map<string, Hook[]>()
  readonly notices: string[] = []
  readonly errors: unknown[] = []
  readonly calls: Array<{
    model: Model<Api>
    context: Context
    options: SimpleStreamOptions
  }> = []

  readonly bodies: Array<Record<string, unknown>> = []
  readonly streams: AssistantMessageEventStream[] = []
  readonly checkpoints: HookResult[] = []
  readonly settings = SettingsManager.inMemory({
    retry: { enabled: true, maxRetries: 2, baseDelayMs: 0 },
    branchSummary: { reserveTokens: 4096 },
    httpIdleTimeoutMs: 0
  })

  readonly controller = new AbortController()
  readonly pi: ExtensionAPI
  readonly ctx: ExtensionContext
  readonly provider: Provider
  model: Model<Api> | undefined = model
  oauth: boolean | (() => boolean) = true
  auth: () => Promise<Auth> = async () => ({
    ok: true, apiKey: 'sk-ant-oat-FAKE-OFFLINE'
  })

  responses: AssistantMessage[] = []
  authCalls = 0
  oauthCalls = 0
  providerReads = 0
  settingsReads = 0
  resultCalls = 0
  payloadCalls = 0
  responseCalls = 0
  defaultCalls = 0
  continuations = 0
  notify: (message: string) => void | Promise<void> = () => {}
  failProvider: Error | undefined
  failResult: Error | undefined
  onProvider: (() => void) | undefined
  onSettings: (() => void) | undefined
  constructor (factoryTest?: TestContext, register = true) {
    const on: ExtensionAPI['on'] = (name, handler) => {
      const handlers = this.hooks.get(name) ?? []
      handlers.push(handler)
      this.hooks.set(name, handlers)
    }
    this.pi = strictFake<ExtensionAPI>({ on })
    const streamSimple: Provider['streamSimple'] = (requestModel, context, source) => {
      if (this.failProvider != null) throw this.failProvider
      const options = source ?? {}
      this.calls.push({ model: requestModel, context, options })
      this.onProvider?.()
      const stream = createAssistantMessageEventStream()
      this.streams.push(stream)
      const result = stream.result.bind(stream)
      stream.result = () => {
        this.resultCalls++
        return (this.failResult != null) ? Promise.reject(this.failResult) : result()
      }
      const response = this.responses.shift() ?? terminal()
      this.complete(stream, requestModel, context, options, response).catch((error) => {
        stream.push({
          type: 'error',
          reason: 'error',
          error: {
            ...terminal('error'), errorMessage: String(error)
          }
        })
        stream.end()
      })
      return stream
    }
    this.provider = strictFake<Provider>({ streamSimple })
    const registry = strictFake<ModelRegistry>({
      isUsingOAuth: () => {
        this.oauthCalls++
        return typeof this.oauth === 'function' ? this.oauth() : this.oauth
      },
      getProvider: () => { this.providerReads++; return this.provider },
      // Preserve synchronous throws and the identity of the auth promise.
      getApiKeyAndHeaders: () => { this.authCalls++; return this.auth() }
    })
    const getModel = (): Model<Api> | undefined => this.model
    this.ctx = strictFake<ExtensionContext>({
      cwd: '/offline/project',
      mode: 'print',
      hasUI: false,
      signal: undefined,
      get model () { return getModel() },
      thinkingLevel: 'high',
      modelRegistry: registry,
      isProjectTrusted: () => false,
      ui: strictFake<ExtensionContext['ui']>({
        // Preserve synchronous throws and returned promises behind the void API.
        notify: (message, type) => {
          assert.equal(type, 'error')
          this.notices.push(message)
          return this.notify(message) as ReturnType<ExtensionContext['ui']['notify']>
        }
      })
    })
    if (factoryTest !== undefined) {
      factoryTest.mock.method(SettingsManager, 'create', (...args: unknown[]) => {
        assert.deepEqual(args, ['/offline/project', getAgentDir(),
          { projectTrusted: false }])
        this.settingsReads++
        this.onSettings?.()
        return this.settings
      })
      if (register) fingerprintFactory(this.pi)
    } else if (register) {
      registerSummaryHooks(this.pi, fingerprint, () => {
        this.settingsReads++
        this.onSettings?.()
        return this.settings
      })
    }
  }

  async complete (
    stream: AssistantMessageEventStream, requestModel: Model<Api>, context: Context,
    options: SimpleStreamOptions, response: AssistantMessage
  ): Promise<void> {
    const payload = {
      ...textPayload(context, requestModel), max_tokens: options.maxTokens
    }
    this.payloadCalls++
    const replacement = await options.onPayload?.(payload, requestModel)
    const prepared = replacement === undefined ? payload : replacement
    assert.ok(isRecord(prepared))
    this.bodies.push(prepared)
    if (options.onResponse !== undefined) {
      this.responseCalls++
      await options.onResponse({ status: 200, headers: { fixture: 'yes' } }, requestModel)
    }
    if (response.stopReason === 'error' || response.stopReason === 'aborted') {
      stream.push({ type: 'error', reason: response.stopReason, error: response })
    } else {
      if (response.stopReason === 'pending') throw new Error('Non-terminal fake response')
      stream.push({ type: 'done', reason: response.stopReason, message: response })
    }
    stream.end()
  }

  // Mirrors installed runner: last truthy result, swallowed errors, immediate cancel.
  async emit (event: BeforeEvent): Promise<HookResult | undefined> {
    let result: HookResult | undefined
    for (const handler of this.hooks.get(event.type) ?? []) {
      try {
        const next = await handler(event as never, this.ctx)
        const hasNext = Boolean(next)
        if (hasNext) {
          assert.ok(isRecord(next))
          result = next
          if (result.cancel === true) return result
        }
      } catch (error) {
        this.errors.push(error)
      }
    }
    return result
  }

  async dispatch (
    event: BeforeEvent, queued = false
  ): Promise<HookResult | undefined> {
    const result = await this.emit(event)
    if (result?.cancel !== true) {
      if ((result?.compaction != null) || ((result?.summary) != null)) {
        this.checkpoints.push({ ...result, fromHook: true })
      } else this.defaultCalls++
    }
    if (event.type === 'session_before_compact' && event.reason !== 'manual' && queued) {
      this.continuations++
    }
    return result
  }

  stream (signal = this.controller.signal): StreamFn {
    return createSummaryStream(this.ctx, signal, this.settings, fingerprint)
  }
}
function sseResponse (): Response {
  const events = [
    {
      type: 'message_start',
      message: {
        id: 'offline-message',
        type: 'message',
        role: 'assistant',
        model: model.id,
        content: [],
        stop_reason: null,
        stop_sequence: null,
        usage: { input_tokens: 11, output_tokens: 0 }
      }
    },
    { type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } },
    {
      type: 'content_block_delta',
      index: 0,
      delta: { type: 'text_delta', text: 'Native adapter offline summary' }
    },
    { type: 'content_block_stop', index: 0 },
    {
      type: 'message_delta',
      delta: { stop_reason: 'end_turn', stop_sequence: null },
      usage: { output_tokens: 7 }
    },
    { type: 'message_stop' }
  ]
  return new Response(events.map((event) =>
    `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(''),
  { status: 200, headers: { 'content-type': 'text/event-stream' } })
}
function assertCancelled (
  h: Harness, result: HookResult | undefined, category?: string
): void {
  assert.deepEqual(result, { cancel: true })
  assert.equal(h.defaultCalls, 0)
  assert.equal(h.checkpoints.length, 0)
  assert.equal(h.errors.length, 0)
  if (category !== undefined && category !== '') {
    assert.match(h.notices.at(-1) ?? '', new RegExp(`Category: ${category}\\.`))
  }
  assert.ok(h.notices.every((message) => !message.includes('fake-secret')))
}
function assertFiles (
  result: HookResult | undefined, read: string[], modified: string[]
): CompactionResult | NonNullable<HookResult['summary']> {
  const summary = result?.compaction ?? result?.summary
  assert.ok(summary)
  const details = fileDetails(summary.details)
  assert.ok(details)
  assert.deepEqual(details.readFiles, read)
  assert.deepEqual(details.modifiedFiles, modified)
  assert.equal(details.aoauthFingerprintSummary, 1)
  return summary
}
function assertBilling (body: Record<string, unknown>, prompt: string): void {
  assert.ok(Array.isArray(body.system))
  const system = body.system
  const billing = system.filter((block) => isRecord(block) &&
    typeof block.text === 'string' &&
    block.text.startsWith('x-anthropic-billing-header:'))
  assert.equal(billing.length, 1)
  assert.equal(system[0], billing[0])
  assert.deepEqual(billing[0], {
    type: 'text',
    text: 'x-anthropic-billing-header: cc_version=2.1.281.' +
      `${buildHashFor('2.1.281', prompt)}; cc_entrypoint=cli;`
  })
}
export async function runTests (
  overrides: Overrides, native: NativeGenerators, moveFactory: (pi: ExtensionAPI) => void,
  prepareNative: (entries: SessionEntry[],
    settings: SessionBeforeCompactEvent['preparation']['settings']) =>
  SessionBeforeCompactEvent['preparation'] | undefined
): Promise<void> {
  await test('ordinary hooks retain frozen pre-refactor results and guards',
    async (t) => {
      const h = new Harness(t)
      assert.deepEqual([...h.hooks.keys()], [
        'session_before_compact', 'session_before_tree',
        'before_provider_headers', 'before_provider_request'
      ])
      const headerHook = h.hooks.get('before_provider_headers')?.[0]
      const payloadHook = h.hooks.get('before_provider_request')?.[0]
      assert.ok((headerHook != null) && payloadHook)
      let sessionId: string | null | undefined
      for (const fixture of ordinary.cases) {
        h.model = { ...model, id: fixture.id }
        const headers: Record<string, string | null> = structuredClone(ordinary.headers)
        const headerEvent = { headers }
        assert.equal(await headerHook(headerEvent as never, h.ctx), undefined)
        assert.match(headers['X-Claude-Code-Session-Id'] ?? '', /^[0-9a-f-]{36}$/u)
        sessionId ??= headers['X-Claude-Code-Session-Id']
        assert.equal(headers['X-Claude-Code-Session-Id'], sessionId)
        const platformOS: Record<string, string> = {
          darwin: 'MacOS', win32: 'Windows', linux: 'Linux', freebsd: 'FreeBSD'
        }
        const expectedHeaders = {
          ...fixture.headers,
          'X-Stainless-OS': platformOS[process.platform] ?? 'Unknown',
          'X-Stainless-Arch': process.arch === 'ia32' ? 'x32' : process.arch
        }
        assert.deepEqual({ ...headers, 'X-Claude-Code-Session-Id': '$SESSION' },
          expectedHeaders)
        const input = structuredClone(ordinary.payload)
        const payloadEvent = { payload: input }
        const output: unknown = await payloadHook(payloadEvent as never, h.ctx)
        assert.deepEqual(output, fixture.result)
        assert.deepEqual(input, ordinary.payload)
        assert.deepEqual(applyFingerprintPayload(input, fixture.id), fixture.result)
      }
      assert.equal(
        buildHashFor('2.1.281', 'The real synthetic prompt for regression.'), 'b83'
      )
      for (const scope of ['no-model', 'api-key', 'other-provider']) {
        h.model = scope === 'no-model'
          ? undefined
          : {
              ...model, provider: scope === 'other-provider' ? 'omni' : 'anthropic'
            }
        h.oauth = scope !== 'api-key'
        const headers = structuredClone(ordinary.headers)
        const headerEvent = { headers }
        assert.equal(await headerHook(headerEvent as never, h.ctx), undefined)
        assert.deepEqual(headers, ordinary.headers)
        const payloadEvent = { payload: ordinary.payload }
        assert.equal(await payloadHook(payloadEvent as never, h.ctx), undefined)
      }
      h.model = model
      h.oauth = true
      for (const payload of [undefined, null, [], 1]) {
        const payloadEvent = { payload }
        assert.equal(await payloadHook(payloadEvent as never, h.ctx), undefined)
      }
      for (const payload of [{}, { system: 'Plain system', messages: [] },
        { system: null, messages: [{ role: 'user', content: 'Plain user' }] }]) {
        const payloadEvent = { payload }
        const result = await payloadHook(payloadEvent as never, h.ctx)
        assert.ok(isRecord(result))
        assertBilling(result, payload.messages?.[0]?.content ?? '')
      }
    })
  await test('factory uses native compact for all reasons, ' +
    'same header session, no global hooks',
  async (t) => {
    const h = new Harness(t)
    let globalHooks = 0
    for (const name of ['before_provider_request', 'before_provider_headers',
      'after_provider_response', 'before_agent_start', 'context']) {
      const handlers = h.hooks.get(name) ?? []
      handlers.push(() => { globalHooks++; throw new Error('Ordinary pipeline invoked') })
      h.hooks.set(name, handlers)
    }
    const ordinaryHeaders: Record<string, string | null> = {}
    const hook = h.hooks.get('before_provider_headers')?.[0]
    assert.ok(hook)
    const headerEvent = { headers: ordinaryHeaders }
    await hook(headerEvent as never, h.ctx)
    const routing = new Set<string | undefined>()
    for (const reason of ['manual', 'threshold', 'overflow'] as const) {
      const event = compactEvent(reason)
      const original = structuredClone(event.preparation)
      const result = await h.dispatch(event)
      const summary = assertFiles(result, ['read.ts'], ['edited.ts', 'written.ts'])
      assert.deepEqual(summary.usage, usage)
      assert.equal(result?.compaction?.tokensBefore, 12345)
      assert.equal(result?.compaction?.firstKeptEntryId, 'kept')
      assert.equal(result?.compaction?.estimatedTokensAfter, undefined)
      assert.deepEqual(event.preparation, original)
      const call = h.calls.at(-1)
      assert.ok(call)
      assert.equal(call.options.signal, event.signal)
      assert.equal(call.options.reasoning, 'high')
      assert.equal(call.options.maxTokens, 1638)
      assert.equal(call.options.cacheRetention, 'none')
      assert.equal(call.options.headers?.['X-Claude-Code-Session-Id'],
        ordinaryHeaders['X-Claude-Code-Session-Id'])
      assert.notEqual(call.options.sessionId, ordinaryHeaders['X-Claude-Code-Session-Id'])
      routing.add(call.options.sessionId)
      assert.equal(call.context.tools, undefined)
      assert.match(call.context.systemPrompt ?? '', /context summarization assistant/u)
      assert.equal(call.context.messages.length, 1)
      const prompt = call.context.messages[0].content
      assert.ok(Array.isArray(prompt) && prompt[0].type === 'text')
      assert.match(prompt[0].text, /Additional focus: Preserve the offline decision/u)
      assertBilling(h.bodies.at(-1) ?? {}, prompt[0].text)
    }
    assert.equal(routing.size, 3)
    assert.equal(globalHooks, 0)
    assert.equal(h.authCalls, 3)
    assert.equal(h.settingsReads, 3)
    assert.equal(h.resultCalls, 6)
    assert.equal(h.checkpoints.length, 3)
  })
  await test('split turns preserve history, previous summary, ' +
    'prefix budgets and combined costs',
  async () => {
    const h = new Harness()
    const event = compactEvent()
    event.preparation.isSplitTurn = true
    event.preparation.previousSummary = 'Earlier offline decision'
    event.preparation.turnPrefixMessages = [
      { role: 'user', content: 'Prefix conversation', timestamp: 0 }
    ]
    const result = await h.dispatch(event)
    assert.equal(h.calls.length, 2)
    assert.equal(h.calls[0].options.maxTokens, 1638)
    assert.equal(h.calls[1].options.maxTokens, 1024)
    for (const call of h.calls) {
      assert.equal(call.options.signal, event.signal)
      assert.equal(call.options.cacheRetention, 'none')
    }
    const history = JSON.stringify(h.calls[0].context)
    const prefix = JSON.stringify(h.calls[1].context)
    assert.match(history, /<previous-summary>\\nEarlier offline decision/u)
    assert.match(history, /Additional focus/u)
    assert.match(prefix, /PREFIX of a turn/u)
    assert.ok(!prefix.includes('Earlier offline decision'))
    assert.match(result?.compaction?.summary ?? '', /Turn Context \(split turn\)/u)
    for (const key of [
      'input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens'
    ] as const) {
      assert.equal(result?.compaction?.usage?.[key], usage[key] * 2)
    }
    for (const key of ['input', 'output', 'cacheRead', 'cacheWrite', 'total'] as const) {
      assert.equal(result?.compaction?.usage?.cost[key], usage.cost[key] * 2)
    }
    assert.notEqual(h.calls[0].options.sessionId, h.calls[1].options.sessionId)
    const prefixOnly = compactEvent()
    prefixOnly.preparation.isSplitTurn = true
    prefixOnly.preparation.messagesToSummarize = []
    prefixOnly.preparation.turnPrefixMessages = event.preparation.turnPrefixMessages
    const h2 = new Harness()
    const second = await h2.dispatch(prefixOnly)
    assert.equal(h2.calls.length, 1)
    assert.match(second?.compaction?.summary ?? '', /No prior history/u)
    assert.deepEqual(second?.compaction?.usage, usage)
    const capped = new Harness()
    capped.model = { ...model, maxTokens: 512, reasoning: false }
    await capped.dispatch(compactEvent())
    assert.equal(capped.calls[0].options.maxTokens, 512)
    assert.equal(capped.calls[0].options.reasoning, undefined)
  })
  await test('tree append/replace instructions, budget, usage, ' +
    'event signal and immutable entries',
  async () => {
    for (const replaceInstructions of [false, true]) {
      const h = new Harness()
      const entries = [userEntry(), toolEntry('read', 'a.ts'), toolEntry('edit', 'b.ts')]
      const event = treeEvent(entries)
      event.preparation.replaceInstructions = replaceInstructions
      const original = structuredClone(event.preparation)
      const result = await h.dispatch(event)
      const summary = assertFiles(result, ['a.ts'], ['b.ts'])
      assert.deepEqual(summary.usage, usage)
      assert.deepEqual(event.preparation, original)
      assert.equal(result?.label, undefined)
      assert.equal(h.calls.length, 1)
      const call = h.calls[0]
      assert.equal(call.options.signal, event.signal)
      assert.equal(call.options.maxTokens, 4096)
      assert.equal(call.options.cacheRetention, 'none')
      assert.match(JSON.stringify(call.context), /Keep the branch decision/u)
      assert.equal(JSON.stringify(call.context).includes('Additional focus'),
        !replaceInstructions)
    }
  })
  await test('native branch budget keeps recent content and cumulative own file details',
    async () => {
      const h = new Harness()
      h.model = { ...model, contextWindow: 500, maxTokens: 512 }
      h.settings.getBranchSummarySettings = () => ({
        reserveTokens: 300, skipPrompt: false
      })
      const entries = [
        summaryEntry('branch_summary', 'old-summary', own(['budget-carried.ts'])),
        userEntry('old', 'EXCLUDED_OLD_CONTEXT '.repeat(100)),
        userEntry('new', 'RETAINED_RECENT_CONTEXT')
      ]
      assertFiles(await h.dispatch(treeEvent(entries)), ['budget-carried.ts'], [])
      assert.equal(h.calls[0].options.maxTokens, 512)
      const request = JSON.stringify(h.calls[0].context)
      assert.ok(request.includes('RETAINED_RECENT_CONTEXT'))
      assert.ok(!request.includes('EXCLUDED_OLD_CONTEXT'))
    })
  await test('budget-empty native tree rejects own metadata ' +
    'and oversized file tool calls',
  async () => {
    for (const name of ['read', 'write', 'edit'] as const) {
      const h = new Harness()
      const requestModel = { ...model, contextWindow: 64, maxTokens: 512 }
      const reserveTokens = 63
      const tokenBudget = requestModel.contextWindow - reserveTokens
      h.model = requestModel
      h.settings.getBranchSummarySettings = () => ({
        reserveTokens, skipPrompt: false
      })
      const details = own(['carried-read.ts'], ['carried-modified.ts'])
      const previous = summaryEntry('branch_summary', 'previous', details)
      const path = `newest-${name}-file.ts`
      const newest = toolEntry(name, path)
      const entries = [previous, newest]
      const event = treeEvent(entries)
      const original = structuredClone(event.preparation)
      const normalized = prepareSummaryEntries(entries)
      const budgeted = prepareBranchEntries(normalized, tokenBudget)
      const unlimited = prepareBranchEntries(normalized)
      assert.ok(tokenBudget > 0)
      assert.ok(prepareBranchEntries([newest]).totalTokens > tokenBudget)
      assert.deepEqual(budgeted.messages, [])
      assert.equal(budgeted.totalTokens, 0)
      assert.equal(unlimited.messages.length, 2)
      assert.ok(budgeted.fileOps.read.has('carried-read.ts'))
      assert.ok(budgeted.fileOps.edited.has('carried-modified.ts'))
      const operation = name === 'read' ? 'read' : name === 'write' ? 'written' : 'edited'
      assert.ok(budgeted.fileOps[operation].has(path))
      let streamRequests = 0
      const actual = await native.generateBranchSummary(normalized, {
        model: requestModel,
        reserveTokens,
        signal: event.signal,
        streamFn: () => { streamRequests++; throw new Error('No stream allowed') }
      })
      assert.deepEqual(actual, { summary: 'No content to summarize' })
      assert.equal(streamRequests, 0)
      assertCancelled(h, await h.dispatch(event), 'summary')
      assert.equal(h.calls.length, 0)
      assert.equal(h.authCalls, 0)
      assert.equal(h.providerReads, 0)
      assert.equal(h.payloadCalls, 0)
      assert.equal(event.preparation.entriesToSummarize, entries)
      assert.equal(entries[0], previous)
      assert.equal(ownDetails(previous), details)
      assert.deepEqual(event.preparation, original)
    }
  })
  await test('budget-empty native tree rejects an oversized user ' +
    'even without file operations',
  async () => {
    const h = new Harness()
    const requestModel = { ...model, contextWindow: 64, maxTokens: 512 }
    const reserveTokens = 63
    const tokenBudget = requestModel.contextWindow - reserveTokens
    h.model = requestModel
    h.settings.getBranchSummarySettings = () => ({ reserveTokens, skipPrompt: false })
    const entries = [userEntry('oversized', 'Real summary content '.repeat(20))]
    const event = treeEvent(entries)
    const original = structuredClone(event.preparation)
    const budgeted = prepareBranchEntries(entries, tokenBudget)
    const unlimited = prepareBranchEntries(entries)
    assert.ok(tokenBudget > 0)
    assert.deepEqual(budgeted.messages, [])
    assert.equal(unlimited.messages.length, 1)
    assert.ok(unlimited.totalTokens > tokenBudget)
    for (const key of ['read', 'written', 'edited'] as const) {
      assert.equal(budgeted.fileOps[key].size, 0)
      assert.equal(unlimited.fileOps[key].size, 0)
    }
    let streamRequests = 0
    const actual = await native.generateBranchSummary(entries, {
      model: requestModel,
      reserveTokens,
      signal: event.signal,
      streamFn: () => { streamRequests++; throw new Error('No stream allowed') }
    })
    assert.deepEqual(actual, { summary: 'No content to summarize' })
    assert.equal(streamRequests, 0)
    assertCancelled(h, await h.dispatch(event), 'summary')
    assert.equal(h.calls.length, 0)
    assert.equal(h.authCalls, 0)
    assert.equal(h.providerReads, 0)
    assert.equal(h.payloadCalls, 0)
    assert.deepEqual(event.preparation, original)
  })
  await test('metadata-only native tree sentinel is preserved without model/auth request',
    async () => {
      const entries: SessionEntry[] = [
        {
          type: 'model_change',
          id: 'm',
          parentId: null,
          timestamp: '0',
          provider: 'anthropic',
          modelId: model.id
        },
        {
          type: 'label', id: 'l', parentId: 'm', timestamp: '0', targetId: 'm', label: 'x'
        },
        {
          type: 'thinking_level_change',
          id: 't',
          parentId: 'l',
          timestamp: '0',
          thinkingLevel: 'high'
        },
        { type: 'custom', id: 'c', parentId: 't', timestamp: '0', customType: 'offline' },
        { type: 'session_info', id: 's', parentId: 'c', timestamp: '0', name: 'offline' }
      ]
      const h = new Harness()
      const event = treeEvent(entries)
      const actual = await native.generateBranchSummary(entries, {
        model,
        signal: event.signal,
        streamFn: () => { throw new Error('No stream allowed') }
      })
      assert.deepEqual(actual, { summary: 'No content to summarize' })
      const result = await h.dispatch(event)
      const summary = assertFiles(result, [], [])
      assert.equal(summary.summary, actual.summary)
      assert.equal(summary.usage, undefined)
      assert.equal(h.calls.length, 0)
      assert.equal(h.authCalls, 0)
      assert.equal(h.defaultCalls, 0)
      assert.equal(h.checkpoints.length, 1)
    })
  await test('summary bypasses the real move-reminders factory ' +
    'in both load orders', async (t) => {
    for (const moveFirst of [true, false]) {
      const h = new Harness(t, false)
      for (const factory of moveFirst
        ? [moveFactory, fingerprintFactory]
        : [fingerprintFactory, moveFactory]) {
        factory(h.pi)
      }
      let ordinaryCalls = 0
      for (const name of ['before_provider_request', 'before_provider_headers',
        'before_agent_start', 'context', 'after_provider_response']) {
        const handlers = h.hooks.get(name) ?? []
        h.hooks.set(name, handlers.map((handler) => (...args) => {
          ordinaryCalls++
          return handler(...args)
        }))
      }
      await h.dispatch(compactEvent())
      await h.dispatch(treeEvent())
      assert.equal(h.checkpoints.length, 2)
      assert.equal(h.calls[0].options.headers?.['X-Claude-Code-Session-Id'],
        h.calls[1].options.headers?.['X-Claude-Code-Session-Id'])
      assert.notEqual(h.calls[0].options.sessionId, h.calls[1].options.sessionId)
      assert.equal(ordinaryCalls, 0)
      for (const body of h.bodies) {
        assert.ok(Array.isArray(body.system))
        assert.ok(body.system.some((block) => isRecord(block) &&
          typeof block.text === 'string' &&
          block.text.includes('summarization assistant')))
        assert.ok(!JSON.stringify(body).includes('Offline agent system'))
        assert.ok(!JSON.stringify(body).includes('Offline reminder'))
      }
    }
  })
  await test('effective provider receives refreshed auth, baseUrl, ' +
    'nullable headers and options',
  async () => {
    const h = new Harness()
    h.auth = async () => ({
      ok: true,
      apiKey: 'fresh-auth',
      baseUrl: 'https://auth.offline.invalid',
      headers: {
        'x-case': 'auth',
        'x-null': 'auth',
        'X-Auth': 'yes',
        'X-Client-Request-Id': 'remove',
        'USER-AGENT': 'auth-agent'
      },
      env: { authOnly: 'a', same: 'auth' }
    })
    let payloadCallbacks = 0
    let responseCallbacks = 0
    const context: Context = {
      systemPrompt: 'Offline summary instructions',
      messages: [{ role: 'user', content: 'Offline user prompt', timestamp: 0 }]
    }
    const stream = await h.stream()(model, context, {
      signal: h.controller.signal,
      maxTokens: 777,
      reasoning: 'medium',
      apiKey: 'explicit-key',
      headers: {
        'X-Case': 'options',
        'X-Null': null,
        'user-agent': 'options-agent',
        'X-Nullable': null
      },
      env: { same: 'options', optionsOnly: 'b' },
      timeoutMs: 23,
      websocketConnectTimeoutMs: 24,
      maxRetries: 4,
      maxRetryDelayMs: 34,
      sessionId: 'standalone-routing',
      cacheRetention: 'none',
      temperature: 0.2,
      transport: 'sse',
      onPayload: (payload, actualModel) => {
        payloadCallbacks++
        assert.equal(actualModel.baseUrl, 'https://auth.offline.invalid')
        assert.ok(isRecord(payload))
        return { ...payload, fixture: 'replacement' }
      },
      onResponse: (response, actualModel) => {
        responseCallbacks++
        assert.equal(response.status, 200)
        assert.equal(actualModel.baseUrl, 'https://auth.offline.invalid')
      }
    })
    const result = await stream.result()
    assert.equal(stream, h.streams[0])
    assert.equal(await stream.result(), result)
    assert.equal(h.calls.length, 1)
    assert.equal(payloadCallbacks, 1)
    assert.equal(responseCallbacks, 1)
    const call = h.calls[0]
    assert.notEqual(call.model, model)
    assert.deepEqual(call.model, { ...model, baseUrl: 'https://auth.offline.invalid' })
    assert.equal(model.baseUrl, 'https://offline.invalid')
    assert.equal(call.options.apiKey, 'explicit-key')
    assert.deepEqual(call.options.env, {
      authOnly: 'a', same: 'options', optionsOnly: 'b'
    })
    assert.equal(call.options.headers?.['X-Case'], 'options')
    assert.equal(call.options.headers?.['x-case'], undefined)
    assert.equal(call.options.headers?.['X-Null'], null)
    assert.equal(call.options.headers?.['x-null'], undefined)
    assert.equal(call.options.headers?.['X-Nullable'], null)
    assert.equal(call.options.headers?.['X-Auth'], 'yes')
    assert.equal(call.options.headers?.['X-Client-Request-Id'], undefined)
    assert.equal(
      call.options.headers?.['User-Agent'], 'claude-cli/2.1.281 (external, cli)'
    )
    assert.equal(call.options.headers?.['user-agent'], undefined)
    assert.equal(call.options.timeoutMs, 23)
    assert.equal(call.options.websocketConnectTimeoutMs, 24)
    assert.equal(call.options.maxRetries, 4)
    assert.equal(call.options.maxRetryDelayMs, 34)
    assert.equal(call.options.sessionId, 'standalone-routing')
    assert.equal(call.options.temperature, 0.2)
    assert.equal(call.options.transport, 'sse')
    assert.equal(h.bodies[0].fixture, 'replacement')
    assertBilling(h.bodies[0], 'Offline user prompt')
    h.auth = async () => ({ ok: true, apiKey: 'fresh-second' })
    await h.stream()(model, context, {
      maxTokens: 512, onPayload: () => undefined, signal: h.controller.signal
    })
    assert.equal(h.calls[1].model, model)
    assert.equal(h.calls[1].options.apiKey, 'fresh-second')
    assert.equal(h.calls[1].options.timeoutMs, 2147483647)
    assert.equal(h.calls[1].options.env, undefined)
    assert.equal(h.authCalls, 2)
    assert.deepEqual(mergeHeaders({ 'X-Drop': 'old', 'x-drop': 'duplicate' },
      { 'X-DROP': null }), { 'X-DROP': null })
  })
  await test('settings snapshot honors trust, drain errors ' +
    'and SDK timeout priority', async (t) => {
    const h = new Harness(t)
    const settings = readSummarySettings(h.ctx)
    assert.equal(settings, h.settings)
    assert.equal(h.settingsReads, 1)
    const cases = [
      { idle: 0, providerTimeout: undefined, expected: 2147483647 },
      { idle: 17, providerTimeout: undefined, expected: 17 },
      { idle: 17, providerTimeout: 29, expected: 29 }
    ]
    for (const fixture of cases) {
      t.mock.method(h.settings, 'getHttpIdleTimeoutMs', () => fixture.idle)
      t.mock.method(h.settings, 'getProviderRetrySettings', () => ({
        timeoutMs: fixture.providerTimeout, maxRetries: 2, maxRetryDelayMs: 31
      }))
      t.mock.method(h.settings, 'getWebSocketConnectTimeoutMs', () => 43)
      await h.dispatch(compactEvent())
      const options = h.calls.at(-1)?.options
      assert.equal(options?.timeoutMs, fixture.expected)
      assert.equal(options?.websocketConnectTimeoutMs, 43)
      assert.equal(options?.maxRetries, 2)
      assert.equal(options?.maxRetryDelayMs, 31)
    }
    t.mock.method(h.settings, 'drainErrors', () => [{
      scope: 'global', error: new Error('fake-secret-settings')
    }])
    const before = h.calls.length
    const result = await h.emit(compactEvent())
    assert.deepEqual(result, { cancel: true })
    assert.equal(h.calls.length, before)
    assert.match(h.notices.at(-1) ?? '', /Category: settings/u)
    assert.ok(!h.notices.join('').includes('fake-secret'))
    t.mock.method(SettingsManager, 'create', () => {
      throw new Error('fake-secret-create')
    })
    assert.deepEqual(await h.emit(treeEvent()), { cancel: true })
    assert.match(h.notices.at(-1) ?? '', /Category: settings/u)
  })
  await test('non-target scopes and tree early exits ' +
    'do not read auth/settings/provider', async () => {
    for (const scope of ['no-model', 'api-key', 'other-provider']) {
      const h = new Harness()
      h.model = scope === 'no-model'
        ? undefined
        : {
            ...model, provider: scope === 'other-provider' ? 'omni' : 'anthropic'
          }
      h.oauth = scope !== 'api-key'
      const compact = compactEvent()
      compact.preparation.firstKeptEntryId = 'unknown-no-metadata'
      assert.equal(await h.emit(compact), undefined)
      assert.equal(await h.emit(treeEvent()), undefined)
      assert.equal(h.authCalls, 0)
      assert.equal(h.providerReads, 0)
      assert.equal(h.settingsReads, 0)
      assert.equal(h.calls.length, 0)
    }
    for (const event of [treeEvent([]), treeEvent()]) {
      const h = new Harness()
      if (event.preparation.entriesToSummarize.length > 0) {
        event.preparation.userWantsSummary = false
      }
      h.oauth = () => { throw new Error('Guard must not run') }
      assert.equal(await h.emit(event), undefined)
      assert.equal(h.oauthCalls, 0)
      assert.equal(h.settingsReads, 0)
      assert.equal(h.authCalls, 0)
    }
  })
  await test('auth abort before/during/after refresh cancels ' +
    'without late dispatch or rejection',
  async () => {
    const unhandled: unknown[] = []
    const listener = (error: unknown): void => { unhandled.push(error) }
    process.on('unhandledRejection', listener)
    try {
      for (const lateReject of [false, true]) {
        const h = new Harness()
        const pendingAuth = Promise.withResolvers<Auth>()
        const started = Promise.withResolvers<undefined>()
        h.auth = () => { started.resolve(undefined); return pendingAuth.promise }
        const event = compactEvent()
        event.signal = h.controller.signal
        const operation = h.dispatch(event)
        await started.promise
        h.controller.abort()
        assertCancelled(h, await operation, 'aborted')
        if (lateReject) pendingAuth.reject(new Error('fake-secret-late-auth'))
        else pendingAuth.resolve({ ok: true, apiKey: 'late-fake-auth' })
        await setImmediate()
        assert.equal(h.calls.length, 0)
      }
      for (const event of [compactEvent(), treeEvent()]) {
        const h = new Harness()
        event.signal = h.controller.signal
        h.controller.abort()
        assertCancelled(h, await h.dispatch(event), 'aborted')
        assert.equal(h.authCalls, 0)
        assert.equal(h.settingsReads, 0)
      }
      const h = new Harness()
      h.auth = async () => {
        h.controller.abort()
        return { ok: true, apiKey: 'fake-after-refresh' }
      }
      const event = treeEvent()
      event.signal = h.controller.signal
      assertCancelled(h, await h.dispatch(event), 'aborted')
      assert.equal(h.calls.length, 0)
      const alreadyAborted = new AbortController()
      alreadyAborted.abort()
      const late = Promise.withResolvers<undefined>()
      await assert.rejects(waitOrAbort(late.promise, alreadyAborted.signal))
      late.reject(new Error('fake-secret-direct-late-reject'))
      await setImmediate()
      assert.deepEqual(unhandled, [])
    } finally {
      process.off('unhandledRejection', listener)
    }
  })
  await test('model snapshot, OAuth recheck and changed signal ' +
    'fail before provider dispatch',
  async () => {
    const h = new Harness()
    h.auth = async () => {
      h.model = { ...model, id: 'changed-selection', provider: 'other' }
      return { ok: true, apiKey: 'snapshot-auth' }
    }
    await h.dispatch(compactEvent())
    assert.equal(h.calls[0].model.id, model.id)
    assert.equal(h.bodies[0].model, model.id)
    for (const invalid of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      const h = new Harness()
      await assert.rejects(async () => await h.stream()(model, { messages: [] }, {
        maxTokens: invalid, signal: h.controller.signal
      }))
      assert.equal(h.authCalls, 0)
      assert.equal(h.calls.length, 0)
    }
    const mismatch = new Harness()
    await assert.rejects(async () => await mismatch.stream()(model, { messages: [] }, {
      maxTokens: 512, signal: new AbortController().signal
    }))
    assert.equal(mismatch.authCalls, 0)
    assert.equal(mismatch.calls.length, 0)
    const logout = new Harness()
    logout.oauth = () => logout.oauthCalls < 3
    assertCancelled(logout, await logout.dispatch(compactEvent()), 'auth')
    assert.equal(logout.authCalls, 1)
    assert.equal(logout.calls.length, 0)
    try {
      overrides.compact = (...args) => {
        const stream = args[7]
        assert.ok(stream)
        args[7] = (model, context, options) => stream(model, context, {
          ...options, signal: new AbortController().signal
        })
        return native.compact(...args)
      }
      const cancelled = new Harness()
      assertCancelled(cancelled, await cancelled.dispatch(compactEvent()), 'summary')
      assert.equal(cancelled.authCalls, 0)
    } finally {
      delete overrides.compact
    }
  })
  await test('counted native StreamFn and effective provider ' +
    'receive the identical event signal',
  async () => {
    let expected: AbortSignal
    let counted = 0
    const count = (stream: StreamFn): StreamFn => (model, context, options) => {
      counted++
      assert.equal(options?.signal, expected)
      return stream(model, context, options)
    }
    try {
      overrides.compact = (...args) => {
        assert.ok(args[7])
        args[7] = count(args[7])
        return native.compact(...args)
      }
      overrides.generateBranchSummary = (entries, options) => {
        assert.ok(options.streamFn)
        return native.generateBranchSummary(entries,
          { ...options, streamFn: count(options.streamFn) })
      }
      for (const event of [compactEvent(), treeEvent()]) {
        expected = event.signal
        const h = new Harness()
        await h.dispatch(event)
        assert.equal(h.calls[0].options.signal, event.signal)
        assert.equal(h.checkpoints.length, 1)
      }
      assert.equal(counted, 2)
    } finally {
      delete overrides.compact
      delete overrides.generateBranchSummary
    }
  })
  await test('all target failures cancel safely, ' +
    'including guard/settings/serialization/notify',
  async (t) => {
    for (const event of [compactEvent(), treeEvent()]) {
      for (const failure of ['guard', 'auth-reject', 'auth-false', 'provider-missing',
        'provider-invalid', 'settings', 'provider-throw', 'result-reject'] as const) {
        const h = new Harness()
        if (failure === 'guard') h.oauth = () => { throw new Error('fake-secret-guard') }
        if (failure === 'auth-reject') {
          h.auth = async () => {
            throw new Error('fake-secret-auth')
          }
        }
        if (failure === 'auth-false') {
          h.auth = async () => ({
            ok: false, error: 'fake-secret-auth-result'
          })
        }
        if (failure === 'provider-missing') {
          t.mock.method(h.ctx.modelRegistry, 'getProvider', () => undefined)
        }
        if (failure === 'provider-invalid') {
          t.mock.method(h.ctx.modelRegistry, 'getProvider', () =>
            strictFake<Provider>({ streamSimple: undefined }))
        }
        if (failure === 'settings') {
          h.onSettings = () => {
            throw new Error('fake-secret-settings')
          }
        }
        if (failure === 'provider-throw') {
          h.failProvider = new Error(
            'fake-secret-provider')
        }
        if (failure === 'result-reject') h.failResult = new Error('fake-secret-result')
        const category = failure === 'guard' || failure.startsWith('auth-')
          ? 'auth'
          : failure.startsWith('provider-') && failure !== 'provider-throw'
            ? 'provider'
            : failure === 'settings' ? 'settings' : 'summary'
        assertCancelled(h, await h.dispatch(event), category)
      }
    }
    for (const event of [compactEvent(), treeEvent()]) {
      const h = new Harness()
      const cyclic = toolEntry('read', 'bad.ts')
      assert.equal(cyclic.type, 'message')
      if (cyclic.type !== 'message' || cyclic.message.role !== 'assistant') {
        throw new Error('Invalid tool fixture')
      }
      cyclic.message.content = [{
        type: 'toolCall',
        id: 'bad',
        name: 'read',
        arguments: { unsupported: 1n }
      }]
      if (event.type === 'session_before_compact') {
        event.preparation.messagesToSummarize = [cyclic.message]
      } else event.preparation.entriesToSummarize = [cyclic]
      assertCancelled(h, await h.dispatch(event), 'summary')
      assert.equal(h.calls.length, 0)
    }
    for (const notify of [() => { throw new Error('fake-secret-notify') },
      async () => { throw new Error('fake-secret-notify-reject') }]) {
      const h = new Harness()
      h.notify = notify
      h.failProvider = new Error('fake-secret-provider')
      assertCancelled(h, await h.dispatch(compactEvent()), 'summary')
    }
  })
  await test('provider aborted and empty stop fail before native file sections ' +
    'can hide them',
  async () => {
    for (const kind of ['compact', 'tree']) {
      for (const fixture of [terminal('aborted'), terminal('aborted', ''),
        terminal('stop', ''), terminal('stop', '   '),
        {
          ...terminal('stop', ''),
          content: [{
            type: 'thinking' as const,
            thinking: 'Not summary text'
          }]
        }]) {
        const h = new Harness()
        h.responses = [fixture]
        const event = kind === 'compact'
          ? compactEvent()
          : treeEvent([toolEntry('read', 'nonempty-file-list.ts')])
        assert.equal(event.signal.aborted, false)
        assertCancelled(h, await h.dispatch(event),
          fixture.stopReason === 'aborted' ? 'aborted' : 'summary')
        assert.equal(h.calls.length, 1)
        assert.equal(h.resultCalls, 1)
      }
    }
    for (const reason of ['error', 'length', 'toolUse'] as const) {
      const h = new Harness()
      const response = terminal(reason)
      if (reason === 'toolUse') {
        response.content = [
          { type: 'toolCall', id: 'bad', name: 'read', arguments: { path: 'x' } }
        ]
      }
      h.responses = [response]
      const stream = await h.stream()(model, {
        systemPrompt: 'Summarize',
        messages: [{ role: 'user', content: 'Fixture', timestamp: 0 }]
      }, { maxTokens: 512, signal: h.controller.signal })
      assert.equal(stream, h.streams[0])
      assert.equal(await stream.result(), response)
      assert.equal(await stream.result(), response)
      assert.equal(h.calls.length, 1)
      assert.equal(h.resultCalls, 3)
    }
  })
  await test('native assistant retries have no extra loop ' +
    'and retain routing IDs', async () => {
    for (const kind of ['compact', 'tree']) {
      for (const mode of ['success', 'disabled', 'exhausted']) {
        const h = new Harness()
        if (mode === 'disabled') {
          h.settings.getRetrySettings = () => ({
            enabled: false,
            maxRetries: 2,
            baseDelayMs: 0
          })
        }
        const transient = { ...terminal('error'), errorMessage: 'terminated fake-secret' }
        h.responses = mode === 'success'
          ? [transient, terminal()]
          : [transient, transient, transient]
        const result = await h.dispatch(kind === 'compact' ? compactEvent() : treeEvent())
        const expected = mode === 'disabled' ? 1 : mode === 'success' ? 2 : 3
        assert.equal(h.calls.length, expected)
        assert.equal(h.authCalls, expected)
        assert.equal(h.payloadCalls, expected)
        assert.equal(h.resultCalls, expected * 2)
        assert.equal(new Set(h.calls.map((call) => call.options.sessionId)).size, 1)
        assert.equal(h.settingsReads, 1)
        assert.deepEqual(h.bodies[0], h.bodies.at(-1))
        if (mode === 'success') assert.equal(h.checkpoints.length, 1)
        else assertCancelled(h, result, 'summary')
      }
      for (const reason of ['error', 'length', 'toolUse', 'aborted'] as const) {
        const h = new Harness()
        const response = terminal(reason)
        if (reason === 'toolUse') {
          response.content.push({
            type: 'toolCall', id: 'call', name: 'read', arguments: {}
          })
        }
        h.responses = [response]
        assertCancelled(h,
          await h.dispatch(kind === 'compact' ? compactEvent() : treeEvent()),
          reason === 'aborted' ? 'aborted' : 'summary')
        assert.equal(h.calls.length, 1)
      }
    }
  })
  await test('strict own schema and consumed compact range ' +
    'exclude stale/retained/foreign data',
  async () => {
    const details = own(['carry.ts', 'promote.ts'], ['previous-edit.ts'])
    const previous = summaryEntry('compaction', 'previous', details, true, 'start')
    const entries = [
      userEntry('old'),
      summaryEntry('compaction', 'obsolete', own(['obsolete.ts']), true, 'old'),
      summaryEntry('branch_summary', 'outside', own(['outside.ts'])),
      userEntry('start'), previous,
      summaryEntry('branch_summary', 'consumed', own(['branch-read.ts'],
        ['promote.ts', 'branch-edit.ts'])),
      summaryEntry('branch_summary', 'malformed', {
        ...own(['malformed.ts']), modifiedFiles: [1]
      }),
      summaryEntry('branch_summary', 'foreign', {
        readFiles: ['foreign.ts'],
        modifiedFiles: []
      }),
      summaryEntry('branch_summary', 'future', {
        ...own(['future.ts']),
        aoauthFingerprintSummary: 2
      }),
      userEntry('kept'),
      summaryEntry('branch_summary', 'retained', own(['retained.ts']))
    ]
    const event = compactEvent()
    event.branchEntries = entries
    const original = structuredClone(event.preparation)
    const originalEntries = structuredClone(entries)
    const copy = prepareSummaryCompaction(event)
    assert.notEqual(copy, event.preparation)
    assert.notEqual(copy.fileOps, event.preparation.fileOps)
    for (const key of ['read', 'written', 'edited'] as const) {
      assert.notEqual(copy.fileOps[key], event.preparation.fileOps[key])
    }
    assert.deepEqual([...copy.fileOps.written], ['written.ts'])
    assert.deepEqual(event.preparation, original)
    const h = new Harness()
    assertFiles(await h.dispatch(event), ['branch-read.ts', 'carry.ts', 'read.ts'],
      ['branch-edit.ts', 'edited.ts', 'previous-edit.ts', 'promote.ts', 'written.ts'])
    assert.deepEqual(entries, originalEntries)
    assert.deepEqual(event.preparation, original)
    for (const invalid of [undefined, {}, [], { ...own(['x']), readFiles: [1] },
      { ...own(['x']), modifiedFiles: ['valid', null] },
      { ...own(['x']), aoauthFingerprintSummary: '1' }]) {
      assert.equal(ownDetails(summaryEntry('branch_summary', 'bad', invalid)), undefined)
    }
    assert.equal(fileDetails(null), undefined)
    assert.deepEqual(ownDetails(previous), details)
    const fallback = compactEvent()
    fallback.branchEntries = [
      summaryEntry('compaction', 'missing-start', own(['previous.ts']), true, 'missing'),
      summaryEntry('branch_summary', 'consumed', own(['included.ts'])),
      userEntry('kept')
    ]
    assert.ok(prepareSummaryCompaction(fallback).fileOps.read.has('included.ts'))
    assert.ok(prepareSummaryCompaction(fallback).fileOps.read.has('previous.ts'))
  })
  await test('non-OAuth metadata bridge changes only aliased Sets/elements ' +
    'after validation',
  async () => {
    const h = new Harness()
    h.oauth = false
    const event = compactEvent()
    const ownBranch = summaryEntry('branch_summary', 'own',
      own(['own.ts'], ['own-edit.ts']))
    event.branchEntries.unshift(ownBranch)
    const alias = event.preparation.fileOps
    const originalWritten = alias.written
    assert.equal(await h.emit(event), undefined)
    assert.equal(event.preparation.fileOps, alias)
    assert.equal(alias.written, originalWritten)
    assert.deepEqual([...alias.read], ['read.ts', 'edited.ts', 'own.ts'])
    assert.deepEqual([...alias.edited], ['edited.ts', 'own-edit.ts'])
    assert.deepEqual([...alias.written], ['written.ts'])
    const foreign = summaryEntry('branch_summary', 'foreign', {
      readFiles: ['foreign.ts'],
      modifiedFiles: []
    })
    const compaction = summaryEntry('compaction', 'compact', own(['gap.ts']))
    const invalid = summaryEntry('branch_summary', 'invalid', {
      ...own(['bad.ts']),
      modifiedFiles: [null]
    })
    const entries = [ownBranch, foreign, compaction, invalid]
    const normalized = prepareSummaryEntries(entries)
    assert.notEqual(normalized, entries)
    assert.notEqual(normalized[0], ownBranch)
    assert.equal(normalized[1], foreign)
    assert.equal(normalized[2], compaction)
    assert.equal(normalized[3], invalid)
    const tree = treeEvent(entries)
    const originalDetails = ownBranch.type === 'branch_summary'
      ? ownBranch.details
      : null
    assert.equal(await h.emit(tree), undefined)
    assert.equal(tree.preparation.entriesToSummarize, entries)
    assert.notEqual(entries[0], ownBranch)
    assert.equal(entries[0].type === 'branch_summary' && entries[0].fromHook, false)
    assert.equal(ownBranch.type === 'branch_summary' && ownBranch.fromHook, true)
    assert.equal(entries[0].type === 'branch_summary' && entries[0].details,
      originalDetails)
    assert.equal(entries[1], foreign)
    assert.equal(entries[2], compaction)
    assert.equal(entries[3], invalid)
    assert.equal(h.authCalls, 0)
    assert.equal(h.settingsReads, 0)
    assert.equal(h.calls.length, 0)
    for (const oauth of [false, true]) {
      for (const boundary of ['unknown', 'reversed']) {
        const h = new Harness()
        h.oauth = oauth
        const event = compactEvent()
        event.branchEntries = [userEntry('kept'),
          summaryEntry('compaction', 'previous', own(['valid.ts']), true, 'start'),
          userEntry('start')]
        if (boundary === 'unknown') event.preparation.firstKeptEntryId = 'missing'
        const before = structuredClone(event.preparation.fileOps)
        assertCancelled(h, await h.dispatch(event), 'metadata')
        assert.deepEqual(event.preparation.fileOps, before)
        if (!oauth) assert.match(h.notices[0], /^Summary metadata preparation failed/u)
      }
    }
  })
  await test('real generators inherit two own compactions ' +
    'and OAuth/non-OAuth/OAuth chains',
  async () => {
    const entries: SessionEntry[] = [userEntry('u0'), toolEntry('read', 'carry.ts'),
      toolEntry('write', 'written.ts'), userEntry('k0')]
    const h = new Harness()
    const settings = { enabled: true, reserveTokens: 2048, keepRecentTokens: 0 }
    const makeEvent = (): SessionBeforeCompactEvent => {
      const event = compactEvent()
      const preparation = prepareNative(entries, settings)
      assert.ok(preparation)
      event.preparation = preparation
      event.branchEntries = entries
      return event
    }
    const first = await h.dispatch(makeEvent())
    assertFiles(first, ['carry.ts'], ['written.ts'])
    assert.ok(first?.compaction)
    entries.push({
      ...first.compaction,
      type: 'compaction',
      id: 'c1',
      parentId: 'k0',
      timestamp: '2026-01-01T00:00:00Z',
      fromHook: true
    },
    userEntry('u1'), userEntry('k1'))
    const second = await h.dispatch(makeEvent())
    assertFiles(second, ['carry.ts'], ['written.ts'])
    assert.ok(second?.compaction)
    entries.push({
      ...second.compaction,
      type: 'compaction',
      id: 'c2',
      parentId: 'k1',
      timestamp: '2026-01-01T00:00:00Z',
      fromHook: true
    },
    userEntry('u2'), toolEntry('edit', 'carry.ts', 'edit-carry'), userEntry('k2'))
    const nonOAuth = new Harness()
    nonOAuth.model = { ...model, provider: 'other-provider' }
    const event = makeEvent()
    assert.equal(await nonOAuth.emit(event), undefined)
    assert.equal(nonOAuth.authCalls, 0)
    assert.equal(nonOAuth.settingsReads, 0)
    const nativeResult = await native.compact(
      event.preparation, nonOAuth.model, undefined, undefined, undefined, event.signal,
      undefined, (model, context, options) =>
        nonOAuth.provider.streamSimple(model, context, options)
    )
    const details = fileDetails(nativeResult.details)
    assert.ok(details)
    assert.deepEqual(details.readFiles, [])
    assert.deepEqual(details.modifiedFiles, ['carry.ts', 'written.ts'])
    assert.equal(details.aoauthFingerprintSummary, undefined)
    assert.ok(!JSON.stringify(nonOAuth.bodies).includes('x-anthropic-billing-header'))
    entries.push({
      ...nativeResult,
      type: 'compaction',
      id: 'native',
      parentId: 'k2',
      timestamp: '2026-01-01T00:00:00Z',
      fromHook: false
    },
    userEntry('u3'), toolEntry('read', 'new.ts', 'new-read'), userEntry('k3'))
    const final = await h.dispatch(makeEvent())
    assertFiles(final, ['new.ts'], ['carry.ts', 'written.ts'])
  })
  await test('real tree generators inherit two own summaries ' +
    'and provider transitions', async () => {
    const h = new Harness()
    const first = await h.dispatch(treeEvent([userEntry(), toolEntry('read', 'carry.ts'),
      toolEntry('write', 'written.ts')]))
    assertFiles(first, ['carry.ts'], ['written.ts'])
    assert.ok(first?.summary)
    const firstEntry: SessionEntry = {
      ...first.summary,
      type: 'branch_summary',
      id: 'b1',
      parentId: null,
      timestamp: '2026-01-01T00:00:00Z',
      fromHook: true,
      fromId: 'old'
    }
    const second = await h.dispatch(treeEvent([firstEntry, userEntry('u2')]))
    assertFiles(second, ['carry.ts'], ['written.ts'])
    assert.ok(second?.summary)
    const secondEntry: SessionEntry = {
      ...second.summary,
      type: 'branch_summary',
      id: 'b2',
      parentId: null,
      timestamp: '2026-01-01T00:00:00Z',
      fromHook: true,
      fromId: 'old'
    }
    const event = treeEvent([secondEntry, toolEntry('edit', 'carry.ts'), userEntry('u3')])
    const alias = event.preparation.entriesToSummarize
    const nonOAuth = new Harness()
    nonOAuth.oauth = false
    assert.equal(await nonOAuth.emit(event), undefined)
    assert.equal(event.preparation.entriesToSummarize, alias)
    const nativeResult = await native.generateBranchSummary(alias, {
      model,
      signal: event.signal,
      streamFn: (model, context, options) =>
        nonOAuth.provider.streamSimple(model, context, options)
    })
    assert.deepEqual(nativeResult.readFiles, [])
    assert.deepEqual(nativeResult.modifiedFiles, ['carry.ts', 'written.ts'])
    const nativeEntry = summaryEntry('branch_summary', 'native-tree', {
      readFiles: nativeResult.readFiles, modifiedFiles: nativeResult.modifiedFiles
    }, false)
    const third = await h.dispatch(treeEvent([nativeEntry,
      toolEntry('read', 'new.ts', 'new'), userEntry('u4')]))
    assertFiles(third, ['new.ts'], ['carry.ts', 'written.ts'])
    assert.equal(nonOAuth.authCalls, 0)
    assert.equal(nonOAuth.settingsReads, 0)
    assert.ok(!JSON.stringify(nonOAuth.bodies).includes('x-anthropic-billing-header'))
    const compact = compactEvent()
    compact.branchEntries.unshift(secondEntry)
    assertFiles(await h.dispatch(compact), ['carry.ts', 'read.ts'],
      ['edited.ts', 'written.ts'])
  })
  await test('native compact-to-tree metadata gap remains explicit, ' +
    'not silently repaired',
  async () => {
    const compaction = summaryEntry('compaction', 'gap', own(['not-inherited.ts']))
    assert.equal(prepareSummaryEntries([compaction])[0], compaction)
    const prepared = prepareBranchEntries([compaction, userEntry()])
    assert.deepEqual([...prepared.fileOps.read], [])
    const h = new Harness()
    assertFiles(await h.dispatch(treeEvent([compaction, userEntry()])), [], [])
    const nonOAuth = new Harness()
    nonOAuth.oauth = false
    const event = treeEvent([compaction, userEntry()])
    assert.equal(await nonOAuth.emit(event), undefined)
    assert.deepEqual([...prepareBranchEntries(event.preparation.entriesToSummarize)
      .fileOps.read], [])
    const documentation = await readFile(new URL('./README.md', import.meta.url), 'utf8')
    assert.match(documentation, /compact→tree/u)
    assert.match(documentation, /0\.85\.1/u)
  })
  await test('real Opus5, constructed Opus5.5 and Sonnet adapters ' +
    'preserve final fingerprint body',
  async (t) => {
    const provider = anthropicProvider()
    const opus5 = provider.getModels().find((entry) => entry.id === 'claude-opus-5')
    const sonnet = provider.getModels().find((entry) => entry.id === 'claude-sonnet-4-5')
    assert.ok(opus5 !== undefined && sonnet !== undefined)
    const constructed55 = { ...opus5, id: 'claude-opus-5-5' }
    for (const requestModel of [opus5, constructed55, sonnet]) {
      const h = new Harness(t)
      h.model = requestModel
      const rawBodies: Array<Record<string, unknown>> = []
      const finalBodies: Array<Record<string, unknown>> = []
      let providerCalls = 0
      let callbacks = 0
      const fetch: typeof globalThis.fetch = async (_input, init) => {
        const body: unknown = JSON.parse(String(init?.body))
        assert.ok(isRecord(body))
        finalBodies.push(body)
        return sseResponse()
      }
      t.mock.method(h.ctx.modelRegistry, 'getProvider', () => strictFake<Provider>({
        streamSimple: (model, context, options) => {
          providerCalls++
          assert.equal(options?.apiKey, 'sk-ant-oat-FAKE-OFFLINE')
          const onPayload = options?.onPayload
          assert.ok(isAnthropicModel(model))
          return provider.streamSimple(model, context, {
            ...options,
            fetch,
            maxRetries: 0,
            onPayload: async (payload, actualModel) => {
              callbacks++
              assert.ok(isSummaryPayload(payload, actualModel))
              rawBodies.push(structuredClone(payload))
              return onPayload?.(payload, actualModel)
            }
          })
        }
      }))
      const result = await h.dispatch(compactEvent())
      assert.equal(h.checkpoints.length, 1)
      assert.match(result?.compaction?.summary ?? '', /Native adapter offline summary/u)
      assert.equal(providerCalls, 1)
      assert.equal(callbacks, 1)
      assert.equal(finalBodies.length, 1)
      const raw = rawBodies[0]
      const body = finalBodies[0]
      assert.ok(Array.isArray(raw.messages) && Array.isArray(body.messages))
      const managed = requestModel.id !== 'claude-sonnet-4-5'
      assert.equal(raw.messages.length, managed ? 2 : 1)
      assert.equal(body.messages.length, 1)
      assert.ok(isRecord(body.messages[0]))
      assert.equal(body.messages[0].role, 'user')
      assert.equal(body.messages[0].output_config, undefined)
      assert.ok(isRecord(raw.messages[0]))
      const content = raw.messages[0].content
      assert.ok(Array.isArray(content) && isRecord(content[0]))
      assert.equal(typeof content[0].text, 'string')
      const prompt = String(content[0].text)
      assertBilling(body, prompt)
      assert.ok(Array.isArray(body.system))
      const systemText = body.system.map((entry) => isRecord(entry) ? entry.text : '')
      assert.ok(systemText.some((text) => typeof text === 'string' &&
          text.includes("You are Claude Code, Anthropic's official CLI")))
      assert.ok(systemText.some((text) => typeof text === 'string' &&
          text.includes('context summarization assistant')))
      if (managed) {
        assert.deepEqual(body.thinking, { type: 'adaptive' })
        assert.ok(isRecord(raw.messages[1]))
        assert.deepEqual(raw.messages[1].content, [])
        assert.ok(isRecord(raw.messages[1].output_config))
        assert.equal(typeof raw.messages[1].output_config.effort, 'string')
      }
      const fixture = ordinary.cases.find((entry) => entry.id === requestModel.id)
      if (fixture !== undefined) {
        assert.deepEqual(applyFingerprintPayload(raw, requestModel.id).betas,
          fixture.result.betas)
      }
    }
  })
  await test('summary-only guards reject callback corruption ' +
    'before real adapter fetch', async (t) => {
    const provider = anthropicProvider()
    const opus5 = provider.getModels().find((entry) => entry.id === 'claude-opus-5')
    assert.ok(opus5)
    const context: Context = {
      systemPrompt: 'Summarize the offline context',
      messages: [
        { role: 'user', content: 'Offline summary user', timestamp: 0 }
      ]
    }
    const managed = textPayload(context, opus5)
    assert.ok(isSummaryPayload(managed, opus5))
    assert.ok(isSummaryPayload(textPayload(context, model), model))
    const mutations: Array<(payload: Record<string, unknown>) => unknown> = [
      () => ({}), () => null, (payload) => ({ ...payload, messages: [] }),
      (payload) => ({ ...payload, system: [] }),
      (payload) => ({ ...payload, system: '' }),
      (payload) => ({ ...payload, max_tokens: 0 }),
      (payload) => ({ ...payload, messages: [managed.messages[0]] }),
      (payload) => ({
        ...payload,
        messages: [managed.messages[0],
          { role: 'user', content: 'second user' }]
      }),
      (payload) => ({
        ...payload,
        messages: [managed.messages[0],
          { role: 'assistant', content: 'not a tail' }]
      }),
      (payload) => ({
        ...payload,
        messages: [managed.messages[0],
          {
            role: 'system',
            content: [{ type: 'text', text: 'arbitrary system' }],
            output_config: { effort: 'high' }
          }]
      }),
      (payload) => ({ ...payload, messages: [...managed.messages, managed.messages[1]] }),
      (payload) => ({
        ...payload,
        messages: [managed.messages[0],
          { role: 'system', content: [], output_config: { effort: '' } }]
      }),
      (payload) => ({
        ...payload,
        messages: [{ role: 'user', content: '' },
          managed.messages[1]]
      })
    ]
    assert.equal(isSummaryPayload(managed, model), false)
    for (const mutate of mutations) {
      assert.equal(isSummaryPayload(mutate(managed), opus5), false)
    }
    assert.equal(isSummaryPayload([], opus5), false)
    assert.equal(isSummaryPayload({ ...managed, max_tokens: Number.NaN }, opus5), false)
    let fetches = 0
    try {
      for (const mutate of mutations) {
        const h = new Harness()
        h.model = opus5
        t.mock.method(h.ctx.modelRegistry, 'getProvider', () => strictFake<Provider>({
          streamSimple: (model, context, options) => {
            assert.ok(isAnthropicModel(model))
            return provider.streamSimple(model, context, {
              ...options,
              maxRetries: 0,
              fetch: async () => { fetches++; return sseResponse() }
            })
          }
        }))
        overrides.compact = (...args) => {
          const stream = args[7]
          assert.ok(stream)
          args[7] = (model, context, options) => stream(model, context, {
            ...options,
            onPayload: (payload) => {
              assert.ok(isRecord(payload))
              return mutate(payload)
            }
          })
          return native.compact(...args)
        }
        assertCancelled(h, await h.dispatch(compactEvent()), 'summary')
      }
      assert.equal(fetches, 0)
    } finally {
      delete overrides.compact
    }
    assert.equal(applyFingerprintPayload(managed, 'claude-sonnet-5').messages,
      managed.messages)
  })
  await test('HTTP retries reuse body/header callbacks ' +
    'while assistant attempts re-authenticate',
  async (t) => {
    const provider = anthropicProvider()
    const sonnet = provider.getModels().find((entry) => entry.id === 'claude-sonnet-4-5')
    assert.ok(sonnet)
    const h = new Harness()
    let fetches = 0
    let payloads = 0
    let responses = 0
    const bodies: string[] = []
    const sessionHeaders: Array<string | null> = []
    t.mock.method(h.ctx.modelRegistry, 'getProvider', () => provider)
    const stream = await h.stream()(sonnet, {
      systemPrompt: 'Summarize offline',
      messages: [{ role: 'user', content: 'Offline HTTP retry fixture', timestamp: 0 }]
    }, {
      maxTokens: 512,
      signal: h.controller.signal,
      apiKey: 'sk-ant-oat-FAKE-OFFLINE',
      maxRetries: 1,
      maxRetryDelayMs: 1,
      onPayload: () => { payloads++ },
      onResponse: () => { responses++ },
      fetch: async (_input, init) => {
        fetches++
        bodies.push(String(init?.body))
        sessionHeaders.push(new Headers(init?.headers).get('X-Claude-Code-Session-Id'))
        if (fetches === 1) {
          return new Response(JSON.stringify({
            type: 'error',
            error: { type: 'overloaded_error', message: 'offline overloaded' }
          }), {
            status: 500,
            headers: {
              'content-type': 'application/json',
              'retry-after-ms': '0'
            }
          })
        }
        return sseResponse()
      }
    })
    assert.equal((await stream.result()).stopReason, 'stop')
    assert.equal(fetches, 2)
    assert.equal(payloads, 1)
    assert.equal(responses, 1)
    assert.equal(h.authCalls, 1)
    assert.equal(bodies[0], bodies[1])
    assert.equal(sessionHeaders[0], sessionHeaders[1])
    assert.equal(sessionHeaders[0], fingerprint.sessionId)
  })
  await test('postchecks reject malformed native results and sentinel errors, ' +
    'never raw fallback',
  async () => {
    try {
      for (const mutate of [
        (result: CompactionResult) => ({ ...result, summary: ' ' }),
        (result: CompactionResult) => ({ ...result, details: undefined }),
        (result: CompactionResult) => ({
          ...result,
          details: {
            readFiles: [],
            modifiedFiles: [1]
          }
        })
      ]) {
        overrides.compact = async (...args) => mutate(await native.compact(...args))
        const h = new Harness()
        assertCancelled(h, await h.dispatch(compactEvent()), 'summary')
      }
      overrides.compact = async (...args) => ({
        ...await native.compact(...args),
        estimatedTokensAfter: 42,
        details: {
          readFiles: [],
          modifiedFiles: [],
          extraNativeDetail: 'preserved'
        }
      })
      const h = new Harness()
      const preserved = await h.dispatch(compactEvent())
      assert.equal(preserved?.compaction?.estimatedTokensAfter, 42)
      assert.equal(fileDetails(preserved?.compaction?.details)?.extraNativeDetail,
        'preserved')
      assert.deepEqual(preserved?.compaction?.usage, usage)
      for (const metadataOnly of [true, false]) {
        for (const error of [undefined, '', 'fake-secret-error']) {
          overrides.generateBranchSummary = async () => ({
            summary: 'No content to summarize', error
          })
          const entries: SessionEntry[] = metadataOnly
            ? [
                {
                  type: 'model_change',
                  id: 'm',
                  parentId: null,
                  timestamp: '0',
                  provider: 'anthropic',
                  modelId: model.id
                },
                {
                  type: 'label',
                  id: 'l',
                  parentId: 'm',
                  timestamp: '0',
                  targetId: 'm',
                  label: 'offline'
                }
              ]
            : [userEntry()]
          const original = structuredClone(entries)
          const h = new Harness()
          const result = await h.dispatch(treeEvent(entries))
          if (metadataOnly && error === undefined) {
            const summary = assertFiles(result, [], [])
            assert.equal(summary.usage, undefined)
            assert.equal(h.checkpoints.length, 1)
          } else assertCancelled(h, result, 'summary')
          assert.equal(h.calls.length, 0)
          assert.equal(h.authCalls, 0)
          assert.equal(h.providerReads, 0)
          assert.equal(h.defaultCalls, 0)
          assert.deepEqual(entries, original)
        }
      }
      for (const result of [
        { summary: 'No content to summarize', readFiles: [] },
        { summary: 'Different sentinel' }, { summary: '' },
        { summary: 'No content to summarize', aborted: true },
        { summary: 'Real summary', readFiles: ['ok'], modifiedFiles: undefined }
      ]) {
        overrides.generateBranchSummary = async () => result
        const h = new Harness()
        assertCancelled(h, await h.dispatch(treeEvent()),
          result.aborted === true ? 'aborted' : 'summary')
      }
      overrides.generateBranchSummary = async (...args) => {
        await native.generateBranchSummary(...args)
        return { summary: 'No content to summarize' }
      }
      const sent = new Harness()
      assertCancelled(sent, await sent.dispatch(treeEvent()), 'summary')
      assert.equal(sent.calls.length, 1)
      for (const field of ['readFiles', 'modifiedFiles'] as const) {
        overrides.generateBranchSummary = async (...args) => {
          const result = await native.generateBranchSummary(...args)
          Object.defineProperty(result, field, { value: ['valid.ts', 1] })
          return result
        }
        const malformed = new Harness()
        assertCancelled(malformed, await malformed.dispatch(treeEvent()), 'summary')
        assert.equal(malformed.calls.length, 1)
      }
      overrides.compact = async (...args) => {
        await native.compact(...args)
        args[5]?.throwIfAborted()
        return {
          summary: 'valid',
          firstKeptEntryId: 'kept',
          tokensBefore: 1,
          details: { readFiles: [], modifiedFiles: [] }
        }
      }
      const abort = new Harness()
      abort.onProvider = () => abort.controller.abort()
      const event = compactEvent()
      event.signal = abort.controller.signal
      assertCancelled(abort, await abort.dispatch(event), 'aborted')
    } finally {
      delete overrides.compact
      delete overrides.generateBranchSummary
    }
  })
  await test('settings getters and callback exceptions ' +
    'remain inside the fail-closed boundary',
  async (t) => {
    for (const getter of ['getRetrySettings', 'getProviderRetrySettings',
      'getBranchSummarySettings', 'getHttpIdleTimeoutMs',
      'getWebSocketConnectTimeoutMs'] as const) {
      const h = new Harness()
      t.mock.method(h.settings, getter, () => { throw new Error('fake-secret-getter') })
      const event = getter === 'getBranchSummarySettings' ? treeEvent() : compactEvent()
      assertCancelled(h, await h.dispatch(event), 'summary')
      assert.equal(h.calls.length, 0)
    }
    const trust = new Harness(t)
    t.mock.method(trust.ctx, 'isProjectTrusted', () => {
      throw new Error('fake-secret-trust')
    })
    assertCancelled(trust, await trust.dispatch(compactEvent()), 'settings')
    try {
      for (const mode of ['payload', 'response']) {
        overrides.compact = (...args) => {
          const stream = args[7]
          assert.ok(stream)
          args[7] = (model, context, options) => stream(model, context, {
            ...options,
            ...(mode === 'payload'
              ? {
                  onPayload: async () => {
                    throw new Error('fake-secret-payload')
                  }
                }
              : {
                  onResponse: async () => {
                    throw new Error('fake-secret-response')
                  }
                })
          })
          return native.compact(...args)
        }
        const h = new Harness()
        assertCancelled(h, await h.dispatch(compactEvent()), 'summary')
      }
      delete overrides.compact
      overrides.generateBranchSummary = async (...args) => {
        await native.generateBranchSummary(...args)
        return { aborted: true }
      }
      const h = new Harness()
      const event = treeEvent()
      assert.equal(event.signal.aborted, false)
      assertCancelled(h, await h.dispatch(event), 'aborted')
    } finally {
      delete overrides.compact
      delete overrides.generateBranchSummary
    }
    for (const reserveTokens of [0, -1, Number.NaN]) {
      const h = new Harness()
      const event = compactEvent()
      event.preparation.settings.reserveTokens = reserveTokens
      assertCancelled(h, await h.dispatch(event), 'summary')
      assert.equal(h.authCalls, 0)
      assert.equal(h.calls.length, 0)
    }
  })
  await test('exclusive-owner limitation matches last-result ' +
    'and immediate-cancel runner semantics',
  async () => {
    for (const kind of ['compact', 'tree']) {
      const event = kind === 'compact' ? compactEvent() : treeEvent()
      const late = new Harness()
      late.hooks.get(event.type)?.push(() => ({ label: 'late-modifier' }))
      assert.deepEqual(await late.dispatch(event), { label: 'late-modifier' })
      assert.equal(late.calls.length, 1)
      assert.equal(late.defaultCalls, 1)
      const early = new Harness()
      early.hooks.get(event.type)?.unshift(() =>
        ({ customInstructions: 'invisible-return' }))
      await early.dispatch(event)
      assert.equal(early.checkpoints.length, 1)
      assert.ok(!JSON.stringify(early.calls[0].context).includes('invisible-return'))
      for (const position of ['early', 'late']) {
        const h = new Harness()
        const handlers = h.hooks.get(event.type)
        assert.ok(handlers)
        const cancel = (): { cancel: boolean } => ({ cancel: true })
        if (position === 'early') handlers.unshift(cancel)
        else handlers.push(cancel)
        assert.deepEqual(await h.dispatch(event), { cancel: true })
        assert.equal(h.calls.length, position === 'early' ? 0 : 1)
        assert.equal(h.defaultCalls, 0)
        assert.equal(h.checkpoints.length, 0)
      }
      const failing = new Harness()
      failing.failProvider = new Error('fake-secret-failure')
      let laterCalls = 0
      failing.hooks.get(event.type)?.push(() => {
        laterCalls++; return { label: 'late' }
      })
      assertCancelled(failing, await failing.dispatch(event), 'summary')
      assert.equal(laterCalls, 0)
    }
    const documentation = await readFile(new URL('./README.md', import.meta.url), 'utf8')
    assert.match(documentation, /exclusive-owner/u)
  })
  await test('cancel notifications distinguish compaction-triggered retry ' +
    'from queued continuation',
  async () => {
    for (const reason of ['manual', 'threshold', 'overflow'] as const) {
      for (const queued of [false, true]) {
        for (const willRetry of [false, true]) {
          const h = new Harness()
          h.failProvider = new Error('fake-secret-400')
          const event = compactEvent(reason)
          event.willRetry = willRetry
          assertCancelled(h, await h.dispatch(event, queued), 'summary')
          assert.equal(h.continuations, reason !== 'manual' && queued ? 1 : 0)
          const notice = h.notices[0]
          if (reason === 'manual') assert.match(notice, /history was not compacted/u)
          if (reason === 'overflow' && willRetry) {
            assert.match(notice, /compaction-triggered retry was not performed/u)
            assert.match(notice, /Queued messages may still continue/u)
          }
          if (reason === 'overflow' && !willRetry) {
            assert.match(notice, /queued messages may still continue/u)
          }
        }
      }
    }
    const h = new Harness()
    await h.dispatch(compactEvent())
    await h.dispatch(treeEvent())
    for (const checkpoint of h.checkpoints) {
      assert.equal(checkpoint.fromHook, true)
      assert.deepEqual((checkpoint.compaction ?? checkpoint.summary)?.usage, usage)
      assert.ok(fileDetails((checkpoint.compaction ?? checkpoint.summary)?.details))
    }
  })
}
