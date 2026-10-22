import assert from 'node:assert/strict'
import { test } from 'node:test'
import type {
  AgentToolResult, CompactOptions, CompactionResult, ExtensionAPI, ExtensionContext,
  ExtensionUIContext, ToolDefinition
} from '@earendil-works/pi-coding-agent'
import compactContext, {
  COMPACT_CONTEXT_MESSAGE_TYPE, COMPACTION_CANCELLED, continuationText
} from '../extensions/compact-context'

type Handler = (...args: unknown[]) => unknown
type Sent = Parameters<ExtensionAPI['sendMessage']>
interface Params {
  instructions?: string
  next?: string
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
  summary: 'Offline summary', firstKeptEntryId: 'entry-1', tokensBefore: 1000
}
class Harness {
  readonly hooks = new Map<string, Handler[]>()
  readonly tools: ToolDefinition[] = []
  readonly sent: Sent[] = []
  readonly compacts: Array<CompactOptions | undefined> = []
  readonly notices: Array<[string, string | undefined]> = []
  compactError: Error | undefined
  hasUI = true
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
    return strictFake<ExtensionContext>({
      compact, hasUI: this.hasUI, ui: strictFake<ExtensionUIContext>({ notify })
    })
  }

  async execute (params: Params): Promise<AgentToolResult<unknown>> {
    return await this.tool.execute('call-1', params, undefined, undefined, this.context())
  }

  options (index = 0): CompactOptions {
    const options = this.compacts[index]
    assert.ok(options !== undefined)
    return options
  }

  shutdown (): void {
    for (const handler of this.hooks.get('session_shutdown') ?? []) handler({})
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
    assert.deepEqual([...h.hooks.keys()], ['session_shutdown'])
    const properties = (h.tool.parameters as { properties: Record<string, unknown> })
      .properties
    assert.deepEqual(Object.keys(properties).sort(), ['instructions', 'next'])
  })
  await test('returns before compaction finishes and resumes once on success',
    async () => {
      const h = new Harness()
      const result = await h.execute({ instructions: '  keep files  ', next: ' step 2 ' })
      assert.deepEqual(JSON.parse(textOf(result)), result.details)
      assert.equal((result.details as { success: boolean }).success, true)
      assert.equal(h.compacts.length, 1)
      assert.equal(h.options().customInstructions, 'keep files')
      assert.equal(h.sent.length, 0)
      h.options().onComplete?.(compaction)
      h.options().onComplete?.(compaction)
      assert.equal(h.sent.length, 1)
      const [message, options] = h.sent[0]
      assert.equal(message.customType, COMPACT_CONTEXT_MESSAGE_TYPE)
      assert.equal(message.display, true)
      assert.equal(message.content, continuationText('step 2'))
      assert.match(String(message.content), /Next step noted before compaction:\nstep 2$/)
      assert.deepEqual(options, { triggerTurn: true, deliverAs: 'followUp' })
    })
  await test('omits blank instructions and next', async () => {
    const h = new Harness()
    await h.execute({ instructions: ' ', next: '' })
    assert.equal(h.options().customInstructions, undefined)
    h.options().onComplete?.(compaction)
    assert.equal(h.sent[0][0].content, continuationText(undefined))
    assert.doesNotMatch(String(h.sent[0][0].content), /Next step/)
  })
  await test('rejects a second call while compaction is pending', async () => {
    const h = new Harness()
    await h.execute({})
    await assert.rejects(h.execute({}), /already in progress/)
    assert.equal(h.compacts.length, 1)
    h.options().onComplete?.(compaction)
    await h.execute({})
    assert.equal(h.compacts.length, 2)
  })
  await test('resumes with the error text when compaction fails', async () => {
    const h = new Harness()
    await h.execute({ next: 'step 3' })
    h.options().onError?.(new Error('Nothing to compact (session too small)'))
    assert.equal(h.sent.length, 1)
    const [message, options] = h.sent[0]
    assert.equal(message.content, continuationText('step 3',
      new Error('Nothing to compact (session too small)')))
    assert.match(String(message.content), /failed: Nothing to compact/)
    assert.deepEqual(options, { triggerTurn: true, deliverAs: 'followUp' })
    await h.execute({})
    assert.equal(h.compacts.length, 2)
  })
  await test('only notifies when compaction is cancelled or aborted', async () => {
    const h = new Harness()
    await h.execute({})
    h.options().onError?.(new Error(COMPACTION_CANCELLED))
    assert.equal(h.sent.length, 0)
    assert.deepEqual(h.notices, [['Context compaction was cancelled', 'warning']])
    const aborted = new Harness()
    await aborted.execute({})
    const abortError = new Error('This operation was aborted')
    abortError.name = 'AbortError'
    aborted.options().onError?.(abortError)
    assert.equal(aborted.sent.length, 0)
    assert.deepEqual(aborted.notices, [['Context compaction was cancelled', 'warning']])
    await aborted.execute({})
    assert.equal(aborted.compacts.length, 2)
    const headless = new Harness()
    headless.hasUI = false
    await headless.execute({})
    headless.options().onError?.(new Error(COMPACTION_CANCELLED))
    assert.equal(headless.sent.length, 0)
    assert.equal(headless.notices.length, 0)
  })
  await test('releases the guard when ctx.compact throws', async () => {
    const h = new Harness()
    h.compactError = new Error('stale context')
    await assert.rejects(h.execute({}), /stale context/)
    h.compactError = undefined
    await h.execute({})
    assert.equal(h.compacts.length, 1)
  })
  await test('ignores callbacks that arrive after session_shutdown', async () => {
    const h = new Harness()
    await h.execute({})
    h.shutdown()
    h.options().onComplete?.(compaction)
    h.options().onError?.(new Error('late failure'))
    assert.equal(h.sent.length, 0)
    await h.execute({})
    h.options(1).onComplete?.(compaction)
    assert.equal(h.sent.length, 1)
  })
}
