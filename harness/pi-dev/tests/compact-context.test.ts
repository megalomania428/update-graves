// cspell:ignore subsessions
import assert from 'node:assert/strict'
import { test } from 'node:test'
import type {
  AgentToolResult, CompactOptions, CompactionEntry, CompactionResult, ContextUsage,
  ExtensionAPI, ExtensionContext, ExtensionUIContext, SessionEntry, SessionMessageEntry,
  ToolDefinition
} from '@earendil-works/pi-coding-agent'
import compactContext, {
  ALREADY_COMPACTED, COMPACT_CONTEXT_MESSAGE_TYPE, COMPACTION_CANCELLED,
  SINGLE_CALL_REASON, autoCompactedText, continuationText, notStartedText, skippedText,
  type ContinuationUsage
} from '../extensions/compact-context'

type Handler = (...args: unknown[]) => unknown
type Sent = Parameters<ExtensionAPI['sendMessage']>
type SessionManager = ExtensionContext['sessionManager']
type AssistantMessage = Extract<SessionMessageEntry['message'], { role: 'assistant' }>
interface Params {
  instructions?: string
  next?: string
  minContextTokens?: number
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
const compaction: CompactionResult = {
  summary: 'Offline summary',
  firstKeptEntryId: 'entry-1',
  tokensBefore: 1000,
  estimatedTokensAfter: 300
}
function assistantEntry (...ids: string[]): SessionEntry {
  const message: AssistantMessage = {
    role: 'assistant',
    content: ids.map((id) => ({ type: 'toolCall', id, name: 'tool', arguments: {} })),
    api: 'anthropic-messages',
    provider: 'anthropic',
    model: 'offline',
    usage: {
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 }
    },
    stopReason: 'toolUse',
    timestamp: 0
  }
  return { type: 'message', id: 'entry-2', parentId: null, timestamp: '', message }
}
const HOOKS = [
  'session_shutdown', 'tool_call', 'turn_start', 'message_start', 'session_compact',
  'session_compact_failed', 'agent_settled'
]
const FOLLOW_UP = { triggerTurn: true, deliverAs: 'followUp' }
const WINDOW = 200000
function usageOf (tokens: number): ContextUsage {
  return { tokens, contextWindow: WINDOW, percent: tokens / WINDOW * 100 }
}
const CALL_TOKENS = 50000
const REQUESTED = 'Context compaction will start after this turn ends, then a ' +
  'continuation message follows; do nothing else in this turn'
// Pi estimates the context right after this compaction entry as 104 tokens.
const COMPACTION_ENTRY: CompactionEntry = {
  type: 'compaction',
  id: 'compaction-1',
  parentId: 'user-1',
  timestamp: '',
  summary: 'Offline summary',
  firstKeptEntryId: 'user-1',
  tokensBefore: 5000
}
const COMPACTED_BRANCH: SessionEntry[] = [
  {
    type: 'message',
    id: 'user-1',
    parentId: null,
    timestamp: '',
    message: { role: 'user', content: 'x'.repeat(400), timestamp: 0 }
  },
  COMPACTION_ENTRY
]
const COMPLETED: ContinuationUsage = {
  tokensAtCall: CALL_TOKENS,
  percentAtCall: 25,
  tokensBefore: 1000,
  percentBefore: 0.5,
  tokensAfter: 300,
  percentAfter: 0.15,
  contextWindow: WINDOW,
  minContextTokens: null
}
const AUTOMATIC: ContinuationUsage = {
  ...COMPLETED,
  tokensBefore: 5000,
  percentBefore: 2.5,
  tokensAfter: 104,
  percentAfter: 0.05
}
function usageBlock (content: unknown): unknown {
  const match = /\nContext usage:\n```json\n([\s\S]*?)\n```(?:\n|$)/.exec(String(content))
  assert.ok(match !== null)
  return JSON.parse(match[1])
}
class Harness {
  readonly hooks = new Map<string, Handler[]>()
  readonly tools: ToolDefinition[] = []
  readonly sent: Sent[] = []
  readonly compacts: Array<CompactOptions | undefined> = []
  readonly notices: Array<[string, string | undefined]> = []
  branch: SessionEntry[] = [assistantEntry('call-1')]
  compactError: Error | undefined
  hasUI = true
  idle = true
  pending = false
  signal: AbortSignal | undefined
  // Returning undefined models a session without a model or context window.
  usage: () => ContextUsage | undefined = () => usageOf(CALL_TOKENS)
  readonly pi: ExtensionAPI
  constructor () {
    const on = (name: string, handler: Handler): void => {
      this.hooks.set(name, [...(this.hooks.get(name) ?? []), handler])
    }
    const registerTool = (tool: ToolDefinition): void => {
      this.tools.push(tool)
    }
    const sendMessage: ExtensionAPI['sendMessage'] = (...args) => {
      this.sent.push(args)
    }
    this.pi = strictFake<ExtensionAPI>({
      on: on as ExtensionAPI['on'],
      registerTool: registerTool as ExtensionAPI['registerTool'],
      sendMessage
    })
    compactContext(this.pi)
  }

  get tool (): ToolDefinition {
    assert.equal(this.tools.length, 1)
    return this.tools[0]
  }

  context (): ExtensionContext {
    const notify: ExtensionUIContext['notify'] = (message, type) => {
      this.notices.push([message, type])
    }
    const compact: ExtensionContext['compact'] = (options) => {
      if (this.compactError !== undefined) throw this.compactError
      this.compacts.push(options)
    }
    const getBranch: SessionManager['getBranch'] = () => this.branch
    return strictFake<ExtensionContext>({
      compact,
      hasUI: this.hasUI,
      ui: strictFake<ExtensionUIContext>({ notify }),
      isIdle: () => this.idle,
      hasPendingMessages: () => this.pending,
      signal: this.signal,
      sessionManager: strictFake<SessionManager>({ getBranch }),
      getContextUsage: () => this.usage()
    })
  }

  async execute (
    params: Params, signal?: AbortSignal
  ): Promise<AgentToolResult<unknown>> {
    return await this.tool.execute('call-1', params, signal, undefined, this.context())
  }

  emit (name: string, event: object = {}): unknown[] {
    return (this.hooks.get(name) ?? []).map((handler) => handler(
      { type: name, ...event }, this.context()
    ))
  }

  async request (params: Params = {}): Promise<CompactOptions> {
    await this.execute(params)
    this.emit('agent_settled')
    return this.options(this.compacts.length - 1)
  }

  options (index = 0): CompactOptions {
    const options = this.compacts[index]
    assert.ok(options !== undefined)
    return options
  }
}
function textOf (result: AgentToolResult<unknown>): string {
  const [content] = result.content
  assert.ok(content !== undefined && content.type === 'text')
  return content.text
}
export async function runTests (): Promise<void> {
  await test('registers compact_context with skills-only description', () => {
    const h = new Harness()
    assert.equal(h.tool.name, 'compact_context')
    assert.equal(h.tool.label, 'Compact Context')
    assert.match(h.tool.description, /skills only/)
    assert.match(h.tool.description, /never call it on your own initiative/)
    assert.match(h.tool.description, /only tool call in the message/)
    assert.match(h.tool.description, /re-read the skill file and the run journal/)
    assert.match(h.tool.description, /minContextTokens/)
    assert.match(h.tool.description, /the turn continues/)
    assert.match(h.tool.description, /yield_to_subsessions/)
    assert.deepEqual([...h.hooks.keys()], HOOKS)
    assert.equal(COMPACT_CONTEXT_MESSAGE_TYPE, 'compact-context')
    assert.equal(COMPACTION_CANCELLED, 'Compaction cancelled')
    assert.equal(ALREADY_COMPACTED, 'Already compacted')
    assert.equal(SINGLE_CALL_REASON,
      'compact_context must be the only tool call in the message')
    const properties = (h.tool.parameters as { properties: Record<string, unknown> })
      .properties
    assert.deepEqual(Object.keys(properties).sort(),
      ['instructions', 'minContextTokens', 'next'])
    const min = properties.minContextTokens as { type: string, minimum: number }
    assert.equal(min.type, 'integer')
    assert.equal(min.minimum, 1)
  })
  await test('defers compaction to agent_settled and resumes once on success',
    async () => {
      const h = new Harness()
      const result = await h.execute({ instructions: '  keep files  ', next: ' step 2 ' })
      assert.deepEqual(JSON.parse(textOf(result)), result.details)
      assert.deepEqual(result.details, {
        success: true,
        tokens: CALL_TOKENS,
        minContextTokens: null,
        contextWindow: WINDOW,
        percent: 25,
        message: REQUESTED
      })
      assert.equal(result.terminate, true)
      assert.match(textOf(result), /after this turn ends/)
      assert.equal(h.compacts.length, 0)
      h.emit('agent_settled')
      assert.equal(h.compacts.length, 1)
      assert.equal(h.options().customInstructions, 'keep files')
      h.emit('agent_settled')
      assert.equal(h.compacts.length, 1)
      assert.equal(h.sent.length, 0)
      h.options().onComplete?.(compaction)
      h.options().onComplete?.(compaction)
      assert.equal(h.sent.length, 1)
      const [message, options] = h.sent[0]
      assert.equal(message.customType, COMPACT_CONTEXT_MESSAGE_TYPE)
      assert.equal(message.display, true)
      assert.equal(message.content,
        continuationText('step 2', undefined, false, COMPLETED))
      assert.match(String(message.content),
        /^Context compaction requested via compact_context has finished\.\n/)
      assert.match(String(message.content), /Next step noted before compaction:\nstep 2$/)
      assert.deepEqual(usageBlock(message.content), COMPLETED)
      assert.match(String(message.content),
        /instructed\.\n\nContext usage:\n```json\n\{\n {2}"tokensAtCall": 50000,\n/)
      assert.match(String(message.content),
        /\n\}\n```\n\nNext step noted before compaction:\n/)
      assert.deepEqual(message.details,
        { error: undefined, next: 'step 2', ...COMPLETED })
      assert.doesNotMatch(String(message.content), /interrupted/)
      assert.deepEqual(options, FOLLOW_UP)
    })
  await test('omits blank instructions and next', async () => {
    const h = new Harness()
    const options = await h.request({ instructions: ' ', next: '' })
    assert.equal(options.customInstructions, undefined)
    options.onComplete?.(compaction)
    assert.equal(h.sent[0][0].content,
      continuationText(undefined, undefined, false, COMPLETED))
    assert.doesNotMatch(String(h.sent[0][0].content), /Next step/)
  })
  await test('rejects a second call while requested or compacting', async () => {
    const h = new Harness()
    await h.execute({})
    await assert.rejects(h.execute({}), /already in progress/)
    h.emit('agent_settled')
    await assert.rejects(h.execute({}), /already in progress/)
    assert.equal(h.compacts.length, 1)
    h.options().onComplete?.(compaction)
    await h.request()
    assert.equal(h.compacts.length, 2)
  })
  await test('rejects the call while messages are queued', async () => {
    const h = new Harness()
    h.pending = true
    await assert.rejects(h.execute({}), /Queued messages are pending/)
    h.emit('agent_settled')
    assert.equal(h.compacts.length, 0)
    h.pending = false
    await h.request()
    assert.equal(h.compacts.length, 1)
  })
  await test('skips compaction below minContextTokens without ending the turn',
    async () => {
      const h = new Harness()
      h.usage = () => usageOf(999)
      const result = await h.execute({ minContextTokens: 1000 })
      const details = {
        success: true,
        skipped: true,
        reason: 'below-threshold',
        tokens: 999,
        minContextTokens: 1000,
        contextWindow: WINDOW,
        percent: 0.5,
        message: skippedText(999, 1000)
      }
      assert.deepEqual(result.details, details)
      assert.deepEqual(JSON.parse(textOf(result)), details)
      assert.equal(skippedText(999, 1000), 'Context compaction skipped: the context ' +
        'has 999 tokens, below minContextTokens 1000. Continue the task as planned.')
      assert.equal(result.terminate, undefined)
      h.emit('turn_start', { turnIndex: 1, timestamp: 0 })
      h.emit('agent_settled')
      assert.equal(h.compacts.length, 0)
      assert.equal(h.sent.length, 0)
    })
  await test('rounds the skipped percent to two decimals', async () => {
    const h = new Harness()
    h.usage = () => ({ tokens: 214846, contextWindow: 1000000, percent: 21.4846 })
    const result = await h.execute({ minContextTokens: 222222 })
    const details = {
      success: true,
      skipped: true,
      reason: 'below-threshold',
      tokens: 214846,
      minContextTokens: 222222,
      contextWindow: 1000000,
      percent: 21.48,
      message: skippedText(214846, 222222)
    }
    assert.deepEqual(result.details, details)
    assert.deepEqual(JSON.parse(textOf(result)), details)
  })
  await test('skips compaction when the context size is unknown', async () => {
    const message = skippedText(null, 1)
    assert.equal(message, 'Context compaction skipped: the context size is unknown. ' +
      'Continue the task as planned.')
    const h = new Harness()
    h.usage = () => ({ tokens: null, contextWindow: WINDOW, percent: null })
    const known = await h.execute({ minContextTokens: 1 })
    const details = {
      success: true,
      skipped: true,
      reason: 'unknown',
      tokens: null,
      minContextTokens: 1,
      contextWindow: WINDOW,
      percent: null,
      message
    }
    assert.deepEqual(known.details, details)
    assert.deepEqual(JSON.parse(textOf(known)), details)
    assert.equal(known.terminate, undefined)
    h.usage = () => undefined
    const missing = await h.execute({ minContextTokens: 1 })
    const unsized = {
      success: true,
      skipped: true,
      reason: 'unknown',
      tokens: null,
      minContextTokens: 1,
      contextWindow: null,
      percent: null,
      message
    }
    assert.deepEqual(missing.details, unsized)
    assert.deepEqual(JSON.parse(textOf(missing)), unsized)
    assert.equal(missing.terminate, undefined)
    h.emit('agent_settled')
    assert.equal(h.compacts.length, 0)
    assert.equal(h.sent.length, 0)
  })
  await test('requests compaction when the context reaches minContextTokens',
    async () => {
      const h = new Harness()
      let reads = 0
      h.usage = () => {
        reads += 1
        return usageOf(1000)
      }
      const result = await h.execute({ minContextTokens: 1000 })
      assert.equal(reads, 1)
      assert.equal(result.terminate, true)
      assert.deepEqual(JSON.parse(textOf(result)), result.details)
      assert.equal((result.details as { skipped?: boolean }).skipped, undefined)
      assert.match(textOf(result), /after this turn ends/)
      assert.deepEqual(result.details, {
        success: true,
        tokens: 1000,
        minContextTokens: 1000,
        contextWindow: WINDOW,
        percent: 0.5,
        message: REQUESTED
      })
      assert.equal(h.compacts.length, 0)
      h.emit('agent_settled')
      assert.equal(h.compacts.length, 1)
      h.options().onComplete?.(compaction)
      assert.equal(h.sent.length, 1)
      assert.deepEqual(h.sent[0][1], FOLLOW_UP)
      assert.equal(reads, 2)
      assert.deepEqual(usageBlock(h.sent[0][0].content), {
        ...COMPLETED, tokensAtCall: 1000, percentAtCall: 0.5, minContextTokens: 1000
      })
    })
  await test('reports the context size at call without minContextTokens', async () => {
    const h = new Harness()
    let reads = 0
    h.usage = () => {
      reads += 1
      return undefined
    }
    const result = await h.execute({})
    assert.equal(reads, 1)
    assert.equal(result.terminate, true)
    assert.deepEqual(result.details, {
      success: true,
      tokens: null,
      minContextTokens: null,
      contextWindow: null,
      percent: null,
      message: REQUESTED
    })
    assert.deepEqual(JSON.parse(textOf(result)), result.details)
    h.emit('agent_settled')
    assert.equal(h.compacts.length, 1)
    h.options().onComplete?.({ ...compaction, estimatedTokensAfter: undefined })
    assert.deepEqual(usageBlock(h.sent[0][0].content), {
      tokensAtCall: null,
      percentAtCall: null,
      tokensBefore: 1000,
      percentBefore: null,
      tokensAfter: null,
      percentAfter: null,
      contextWindow: null,
      minContextTokens: null
    })
  })
  await test('accepts an ordinary call after a skipped one', async () => {
    const h = new Harness()
    h.usage = () => usageOf(10)
    const skipped = await h.execute({ minContextTokens: 1000 })
    assert.equal((skipped.details as { skipped: boolean }).skipped, true)
    const result = await h.execute({ next: 'step 10' })
    assert.equal(result.terminate, true)
    await assert.rejects(h.execute({ minContextTokens: 1000 }), /already in progress/)
    h.emit('agent_settled')
    assert.equal(h.compacts.length, 1)
    h.options().onComplete?.(compaction)
    assert.equal(h.sent[0][0].content, continuationText('step 10', undefined, false,
      { ...COMPLETED, tokensAtCall: 10, percentAtCall: 0.01 }))
  })
  await test('skips below minContextTokens even while messages are queued',
    async () => {
      const h = new Harness()
      h.usage = () => usageOf(10)
      h.pending = true
      const pending = await h.execute({ minContextTokens: 1000 })
      assert.equal((pending.details as { skipped: boolean }).skipped, true)
      assert.equal(pending.terminate, undefined)
      await assert.rejects(h.execute({ minContextTokens: 10 }),
        /Queued messages are pending/)
      h.pending = false
      await h.execute({})
      h.emit('turn_start', { turnIndex: 1, timestamp: 0 })
      const notice = await h.execute({ minContextTokens: 1000 })
      assert.equal((notice.details as { skipped: boolean }).skipped, true)
      await assert.rejects(h.execute({ minContextTokens: 10 }),
        /Queued messages are pending/)
      assert.equal(h.compacts.length, 0)
    })
  await test('blocks compact_context unless it is the only tool call', () => {
    const h = new Harness()
    const call = { toolName: 'compact_context', toolCallId: 'call-1', input: {} }
    assert.deepEqual(h.emit('tool_call', call), [undefined])
    h.branch = [assistantEntry('call-1', 'call-2')]
    const blocked = { block: true, reason: SINGLE_CALL_REASON }
    assert.deepEqual(h.emit('tool_call', call), [blocked])
    const skippable = { ...call, input: { minContextTokens: 1000 } }
    assert.deepEqual(h.emit('tool_call', skippable), [blocked])
    assert.deepEqual(h.emit('tool_call', { ...call, toolName: 'read' }), [undefined])
    h.branch = [assistantEntry('call-1'), assistantEntry('call-3')]
    assert.deepEqual(h.emit('tool_call', call), [undefined])
    h.branch = [assistantEntry('call-3')]
    assert.deepEqual(h.emit('tool_call', call), [blocked])
  })
  await test('cancels the request when the turn continues with queued messages',
    async () => {
      const h = new Harness()
      h.emit('turn_start', { turnIndex: 0, timestamp: 0 })
      assert.equal(h.sent.length, 0)
      await h.execute({ next: 'step 4' })
      h.emit('turn_start', { turnIndex: 1, timestamp: 0 })
      assert.equal(h.sent.length, 1)
      const [message, options] = h.sent[0]
      assert.equal(message.customType, COMPACT_CONTEXT_MESSAGE_TYPE)
      assert.equal(message.content,
        notStartedText('new messages arrived before the turn ended'))
      assert.match(String(message.content),
        /was not started: new messages arrived before the turn ended/)
      assert.deepEqual(options, { triggerTurn: true, deliverAs: 'steer' })
      h.emit('agent_settled')
      assert.equal(h.compacts.length, 0)
      await h.request()
      h.emit('turn_start', { turnIndex: 0, timestamp: 0 })
      assert.equal(h.sent.length, 1)
    })
  await test('reports an automatic compaction from turn_start', async () => {
    const h = new Harness()
    h.branch = COMPACTED_BRANCH
    await h.execute({ next: 'step 9' })
    h.emit('session_compact', { reason: 'threshold', compactionEntry: COMPACTION_ENTRY })
    h.emit('turn_start', { turnIndex: 1, timestamp: 0 })
    assert.equal(h.sent.length, 1)
    const [message, options] = h.sent[0]
    assert.equal(message.customType, COMPACT_CONTEXT_MESSAGE_TYPE)
    assert.equal(message.content, autoCompactedText('step 9', AUTOMATIC))
    assert.match(String(message.content), /Do not call compact_context again/)
    assert.match(String(message.content), /Next step noted before compaction:\nstep 9$/)
    assert.deepEqual(usageBlock(message.content), AUTOMATIC)
    assert.deepEqual(message.details, { next: 'step 9', notice: 1, ...AUTOMATIC })
    assert.deepEqual(options, { triggerTurn: true, deliverAs: 'steer' })
    h.emit('agent_settled')
    assert.equal(h.compacts.length, 0)
    assert.equal(h.sent.length, 1)
  })
  await test('rejects the call until the turn_start notice is delivered', async () => {
    const h = new Harness()
    await h.execute({})
    h.emit('turn_start', { turnIndex: 1, timestamp: 0 })
    await assert.rejects(h.execute({}), /Queued messages are pending/)
    h.emit('message_start', { message: { role: 'user', content: 'hi', timestamp: 0 } })
    await assert.rejects(h.execute({}), /Queued messages are pending/)
    const other = { role: 'custom', customType: 'other', content: 'x', display: true }
    h.emit('message_start', { message: { ...other, timestamp: 0 } })
    await assert.rejects(h.execute({}), /Queued messages are pending/)
    const followUp = { ...other, customType: COMPACT_CONTEXT_MESSAGE_TYPE, details: {} }
    h.emit('message_start', { message: { ...followUp, timestamp: 0 } })
    await assert.rejects(h.execute({}), /Queued messages are pending/)
    const notice = { role: 'custom', ...h.sent[0][0], timestamp: 0 }
    h.emit('message_start', { message: notice })
    await h.execute({})
    h.emit('turn_start', { turnIndex: 2, timestamp: 0 })
    await assert.rejects(h.execute({}), /Queued messages are pending/)
    h.emit('agent_settled')
    await h.request()
    assert.equal(h.compacts.length, 1)
  })
  await test('skips compaction when Pi already compacted automatically', async () => {
    for (const reason of ['threshold', 'overflow']) {
      const h = new Harness()
      h.branch = COMPACTED_BRANCH
      await h.execute({ next: 'step 5' })
      h.emit('session_compact', { reason, compactionEntry: COMPACTION_ENTRY })
      h.emit('agent_settled')
      assert.equal(h.compacts.length, 0)
      assert.equal(h.sent.length, 1)
      const [message, options] = h.sent[0]
      assert.equal(message.content,
        continuationText('step 5', undefined, true, AUTOMATIC))
      assert.deepEqual(message.details,
        { error: undefined, next: 'step 5', ...AUTOMATIC })
      assert.match(String(message.content), /compacted the context automatically/)
      assert.deepEqual(options, FOLLOW_UP)
      await h.execute({})
      h.emit('session_compact', { reason: 'manual' })
      h.emit('agent_settled')
      assert.equal(h.compacts.length, 1)
    }
  })
  await test('treats Already compacted as success only after session_compact',
    async () => {
      const h = new Harness()
      await h.execute({ next: 'step 6' })
      h.emit('session_compact', { reason: 'manual' })
      h.emit('agent_settled')
      h.options().onError?.(new Error(ALREADY_COMPACTED))
      assert.equal(h.sent[0][0].content, continuationText('step 6'))
      assert.doesNotMatch(String(h.sent[0][0].content), /Context usage/)
      assert.deepEqual(h.sent[0][0].details, { error: undefined, next: 'step 6' })
      const fresh = new Harness()
      const options = await fresh.request({ next: 'step 7' })
      options.onError?.(new Error(ALREADY_COMPACTED))
      assert.equal(fresh.sent[0][0].content,
        continuationText('step 7', new Error(ALREADY_COMPACTED)))
    })
  await test('resumes with the error text when compaction fails', async () => {
    const h = new Harness()
    const options = await h.request({ next: 'step 3' })
    options.onError?.(new Error('Nothing to compact (session too small)'))
    assert.equal(h.sent.length, 1)
    const [message, sentOptions] = h.sent[0]
    assert.equal(message.content, continuationText('step 3',
      new Error('Nothing to compact (session too small)')))
    assert.match(String(message.content), /failed: Nothing to compact/)
    assert.doesNotMatch(String(message.content), /Context usage/)
    assert.deepEqual(sentOptions, FOLLOW_UP)
    await h.request()
    assert.equal(h.compacts.length, 2)
  })
  await test('resumes with the error text when ctx.compact throws', async () => {
    const h = new Harness()
    h.compactError = new Error('stale context')
    await h.execute({ next: 'step 8' })
    h.emit('agent_settled')
    assert.equal(h.sent.length, 1)
    assert.equal(h.sent[0][0].content, continuationText('step 8', h.compactError))
    assert.deepEqual(h.sent[0][1], FOLLOW_UP)
    h.compactError = undefined
    await h.request()
    assert.equal(h.compacts.length, 1)
  })
  await test('only notifies when compaction is cancelled or aborted', async () => {
    const abortError = new Error('This operation was aborted')
    abortError.name = 'AbortError'
    for (const hasUI of [true, false]) {
      for (const error of [new Error(COMPACTION_CANCELLED), abortError]) {
        const h = new Harness()
        h.hasUI = hasUI
        const options = await h.request()
        options.onError?.(error)
        assert.equal(h.sent.length, 0)
        assert.deepEqual(h.notices,
          hasUI ? [['Context compaction was cancelled', 'warning']] : [])
        await h.request()
        assert.equal(h.compacts.length, 2)
      }
    }
  })
  await test('drops the request when the turn was aborted before settling', async () => {
    const h = new Harness()
    const controller = new AbortController()
    await h.execute({}, controller.signal)
    controller.abort()
    h.emit('agent_settled')
    assert.equal(h.compacts.length, 0)
    assert.equal(h.sent.length, 0)
    assert.deepEqual(h.notices, [['Context compaction was cancelled', 'warning']])
    await h.execute({})
    h.emit('session_compact_failed', { reason: 'threshold', aborted: true })
    h.emit('agent_settled')
    assert.equal(h.compacts.length, 0)
    assert.equal(h.notices.length, 2)
    const options = await h.request()
    h.emit('session_compact_failed', { reason: 'manual', aborted: true })
    options.onComplete?.(compaction)
    assert.equal(h.sent.length, 1)
    const headless = new Harness()
    headless.hasUI = false
    const aborted = new AbortController()
    await headless.execute({}, aborted.signal)
    aborted.abort()
    headless.emit('agent_settled')
    assert.equal(headless.compacts.length, 0)
    assert.equal(headless.sent.length, 0)
    assert.equal(headless.notices.length, 0)
  })
  await test('does not start compaction while the session is busy', async () => {
    const reason = 'the session became busy before compaction could start'
    for (const busy of ['running', 'compacting', 'queued']) {
      const h = new Harness()
      await h.execute({})
      h.idle = busy === 'queued'
      h.pending = busy === 'queued'
      h.signal = busy === 'running' ? new AbortController().signal : undefined
      h.emit('agent_settled')
      assert.equal(h.compacts.length, 0)
      assert.equal(h.sent.length, 1)
      assert.equal(h.sent[0][0].content, notStartedText(reason))
      if (busy === 'running') {
        assert.deepEqual(h.sent[0][1], FOLLOW_UP)
        assert.equal(h.notices.length, 0)
      } else {
        assert.deepEqual(h.sent[0][1], { deliverAs: 'nextTurn' })
        assert.deepEqual(h.notices,
          [[`Context compaction was not started: ${reason}`, 'warning']])
      }
      h.idle = true
      h.pending = false
      h.signal = undefined
      await h.request()
      assert.equal(h.compacts.length, 1)
    }
    const headless = new Harness()
    headless.hasUI = false
    await headless.execute({})
    headless.idle = false
    headless.emit('agent_settled')
    assert.equal(headless.compacts.length, 0)
    assert.deepEqual(headless.sent[0][1], { deliverAs: 'nextTurn' })
    assert.equal(headless.notices.length, 0)
  })
  await test('ignores callbacks that arrive after session_shutdown', async () => {
    const h = new Harness()
    const options = await h.request()
    h.emit('session_shutdown')
    options.onComplete?.(compaction)
    options.onError?.(new Error('late failure'))
    assert.equal(h.sent.length, 0)
    await h.execute({})
    h.emit('session_shutdown')
    h.emit('agent_settled')
    assert.equal(h.compacts.length, 1)
    const next = await h.request()
    next.onComplete?.(compaction)
    assert.equal(h.sent.length, 1)
  })
  await test('estimates tokens after compaction when Pi omits the estimate', async () => {
    const h = new Harness()
    const options = await h.request({ minContextTokens: 1 })
    h.branch = COMPACTED_BRANCH
    options.onComplete?.({ ...compaction, estimatedTokensAfter: undefined })
    assert.deepEqual(usageBlock(h.sent[0][0].content), {
      ...COMPLETED, tokensAfter: 104, percentAfter: 0.05, minContextTokens: 1
    })
  })
  await test('takes the context window at completion, then from the call snapshot',
    async () => {
      const h = new Harness()
      const options = await h.request()
      h.usage = () => ({ tokens: null, contextWindow: 100000, percent: null })
      options.onComplete?.(compaction)
      assert.deepEqual(usageBlock(h.sent[0][0].content), {
        ...COMPLETED, percentBefore: 1, percentAfter: 0.3, contextWindow: 100000
      })
      const fallback = new Harness()
      const next = await fallback.request()
      fallback.usage = () => undefined
      next.onComplete?.(compaction)
      assert.deepEqual(usageBlock(fallback.sent[0][0].content), COMPLETED)
    })
}
