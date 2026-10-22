// cspell:ignore subsessions
import { Type } from 'typebox'
import type { ExtensionAPI, ExtensionContext } from '@earendil-works/pi-coding-agent'

export const COMPACT_CONTEXT_MESSAGE_TYPE = 'compact-context'
export const COMPACTION_CANCELLED = 'Compaction cancelled'
export const ALREADY_COMPACTED = 'Already compacted'
export const SINGLE_CALL_REASON =
  'compact_context must be the only tool call in the message'

const CompactContextParams = Type.Object(
  {
    instructions: Type.Optional(Type.String({
      description: 'What the summary must preserve: goals, decisions, files, open issues'
    })),
    next: Type.Optional(Type.String({
      description: 'Concrete note on what to do right after compaction'
    })),
    minContextTokens: Type.Optional(Type.Integer({
      minimum: 1,
      description: 'Compact only if the current context has at least this many tokens, ' +
        'otherwise skip without ending the turn'
    }))
  },
  { additionalProperties: true }
)

type SendOptions = Parameters<ExtensionAPI['sendMessage']>[1]

interface CompactRequest {
  generation: number
  instructions: string | undefined
  next: string | undefined
  signal: AbortSignal | undefined
  compacted: boolean
  autoCompacted: boolean
  aborted: boolean
}

export function isCancellation (error: Error): boolean {
  return error.message === COMPACTION_CANCELLED || error.name === 'AbortError'
}

function optionalText (value: string | undefined): string | undefined {
  const text = value?.trim() ?? ''
  return text !== '' ? text : undefined
}

export function continuationText (
  next: string | undefined, error?: Error, automatic = false
): string {
  const lines = error !== undefined
    ? [
        `Context compaction requested via compact_context failed: ${error.message}`,
        'The context was not compacted.'
      ]
    : automatic
      ? ['Context compaction requested via compact_context has finished: Pi already ' +
          'compacted the context automatically after the request.']
      : ['Context compaction requested via compact_context has finished.']
  lines.push('Continue the task from where it stopped; do not call compact_context ' +
    'again unless explicitly instructed.')
  if (next !== undefined) lines.push('', 'Next step noted before compaction:', next)
  return lines.join('\n')
}

export function skippedText (tokens: number | null, minContextTokens: number): string {
  const reason = tokens === null
    ? 'the context size is unknown'
    : `the context has ${tokens} tokens, below minContextTokens ${minContextTokens}`
  return `Context compaction skipped: ${reason}. Continue the task as planned.`
}

export function notStartedText (reason: string): string {
  return `Context compaction requested via compact_context was not started: ${reason}. ` +
    'Call compact_context again when it is appropriate, only if the user or the ' +
    'active skill still requires it.'
}

export function autoCompactedText (next: string | undefined): string {
  const lines = [
    'Context compaction requested via compact_context is no longer needed: Pi already ' +
      'compacted the context automatically after the request. Do not call ' +
      'compact_context again; handle the new messages first, then continue the task.'
  ]
  if (next !== undefined) lines.push('', 'Next step noted before compaction:', next)
  return lines.join('\n')
}

function toolCallCount (ctx: ExtensionContext, toolCallId: string): number {
  const branch = ctx.sessionManager.getBranch()
  for (let index = branch.length - 1; index >= 0; index -= 1) {
    const entry = branch[index]
    if (entry.type !== 'message' || entry.message.role !== 'assistant') continue
    const calls = entry.message.content.filter((block) => block.type === 'toolCall')
    if (calls.some((call) => call.id === toolCallId)) return calls.length
  }
  return 0
}

export default function compactContext (pi: ExtensionAPI): void {
  let state: 'idle' | 'requested' | 'compacting' = 'idle'
  let request: CompactRequest | undefined
  let generation = 0
  // Id of the steer notice queued by turn_start: hasPendingMessages misses it.
  let queuedNotice: number | undefined
  let noticeCount = 0

  const reset = (): void => {
    state = 'idle'
    request = undefined
  }
  const settle = (current: CompactRequest): boolean => {
    if (state !== 'compacting' || current.generation !== generation) return false
    reset()
    return true
  }
  const resume = (current: CompactRequest, error?: Error, automatic = false): void => {
    pi.sendMessage({
      customType: COMPACT_CONTEXT_MESSAGE_TYPE,
      content: continuationText(current.next, error, automatic),
      display: true,
      details: { error: error?.message, next: current.next }
    }, { triggerTurn: true, deliverAs: 'followUp' })
  }
  const notStarted = (reason: string, options: SendOptions, notice?: number): void => {
    pi.sendMessage({
      customType: COMPACT_CONTEXT_MESSAGE_TYPE,
      content: notStartedText(reason),
      display: true,
      details: { error: reason, notice }
    }, options)
  }
  const cancelled = (ctx: ExtensionContext): void => {
    if (ctx.hasUI) ctx.ui.notify('Context compaction was cancelled', 'warning')
  }

  pi.on('session_shutdown', () => {
    generation += 1
    queuedNotice = undefined
    reset()
  })

  pi.on('tool_call', (event, ctx) => {
    if (event.toolName !== 'compact_context') return
    if (toolCallCount(ctx, event.toolCallId) === 1) return
    return { block: true, reason: SINGLE_CALL_REASON }
  })

  pi.on('turn_start', () => {
    if (state !== 'requested' || request === undefined) return
    const current = request
    reset()
    noticeCount += 1
    const notice = noticeCount
    queuedNotice = notice
    const steer = { triggerTurn: true, deliverAs: 'steer' } as const
    if (!current.autoCompacted) {
      notStarted('new messages arrived before the turn ended', steer, notice)
      return
    }
    pi.sendMessage({
      customType: COMPACT_CONTEXT_MESSAGE_TYPE,
      content: autoCompactedText(current.next),
      display: true,
      details: { next: current.next, notice }
    }, steer)
  })

  pi.on('message_start', (event) => {
    const { message } = event
    if (queuedNotice === undefined || message.role !== 'custom') return
    if (message.customType !== COMPACT_CONTEXT_MESSAGE_TYPE) return
    const details = message.details as { notice?: unknown } | undefined
    if (details?.notice === queuedNotice) queuedNotice = undefined
  })

  pi.on('session_compact', (event) => {
    if (request === undefined) return
    request.compacted = true
    const automatic = event.reason === 'threshold' || event.reason === 'overflow'
    if (state === 'requested' && automatic) request.autoCompacted = true
  })

  pi.on('session_compact_failed', (event) => {
    if (state !== 'requested' || request === undefined) return
    if (event.aborted) request.aborted = true
  })

  pi.on('agent_settled', (_event, ctx) => {
    queuedNotice = undefined
    if (state !== 'requested' || request === undefined) return
    const current = request
    if (current.aborted || current.signal?.aborted === true) {
      reset()
      cancelled(ctx)
      return
    }
    state = 'compacting'
    if (current.autoCompacted) {
      reset()
      resume(current, undefined, true)
      return
    }
    if (!ctx.isIdle() || ctx.hasPendingMessages()) {
      reset()
      const reason = 'the session became busy before compaction could start'
      if (ctx.signal !== undefined) {
        notStarted(reason, { triggerTurn: true, deliverAs: 'followUp' })
        return
      }
      // Without a running turn, triggerTurn would start a run beside another compaction.
      notStarted(reason, { deliverAs: 'nextTurn' })
      if (!ctx.hasUI) return
      ctx.ui.notify(`Context compaction was not started: ${reason}`, 'warning')
      return
    }
    try {
      ctx.compact({
        customInstructions: current.instructions,
        onComplete: () => {
          if (settle(current)) resume(current)
        },
        onError: (error) => {
          if (!settle(current)) return
          if (isCancellation(error)) {
            cancelled(ctx)
            return
          }
          if (error.message === ALREADY_COMPACTED && current.compacted) {
            resume(current)
            return
          }
          resume(current, error)
        }
      })
    } catch (error) {
      if (!settle(current)) return
      resume(current, error instanceof Error ? error : new Error(String(error)))
    }
  })

  pi.registerTool({
    name: 'compact_context',
    label: 'Compact Context',
    description: 'Compact the conversation context and then continue the current task ' +
      'automatically. Intended for skills only: never call it on your own initiative, ' +
      'only when the user or an active skill explicitly instructs to compact the ' +
      'context. Call it as the only tool call in the message. The turn ends right ' +
      'after the call, compaction starts once the turn has finished, and then a ' +
      'continuation message arrives and work resumes. Pass instructions to focus the ' +
      'summary and next to note what to do right after compaction, for example ' +
      're-read the skill file and the run journal. Pass minContextTokens to compact ' +
      'only when the current context has at least that many tokens: below it, or ' +
      'when the context size is unknown, the call is skipped and returns at once, ' +
      'the turn continues, and the next planned action must follow, for example ' +
      'yield_to_subsessions.',
    parameters: CompactContextParams,
    async execute (_toolCallId, params, signal, _onUpdate, ctx) {
      if (state !== 'idle') throw new Error('Context compaction is already in progress')
      const { minContextTokens } = params
      if (minContextTokens !== undefined) {
        const usage = ctx.getContextUsage()
        const tokens = usage?.tokens ?? null
        if (tokens === null || tokens < minContextTokens) {
          const skipped = {
            success: true as const,
            skipped: true as const,
            reason: tokens === null ? 'unknown' as const : 'below-threshold' as const,
            tokens,
            minContextTokens,
            ...(usage !== undefined
              ? { contextWindow: usage.contextWindow, percent: usage.percent }
              : {}),
            message: skippedText(tokens, minContextTokens)
          }
          return {
            content: [{ type: 'text', text: JSON.stringify(skipped) }],
            details: skipped
          }
        }
      }
      if (ctx.hasPendingMessages() || queuedNotice !== undefined) {
        throw new Error('Queued messages are pending: handle them first, then call ' +
          'compact_context again')
      }
      generation += 1
      request = {
        generation,
        instructions: optionalText(params.instructions),
        next: optionalText(params.next),
        signal,
        compacted: false,
        autoCompacted: false,
        aborted: false
      }
      state = 'requested'
      const result = {
        success: true as const,
        message: 'Context compaction will start after this turn ends, then a ' +
          'continuation message follows; do nothing else in this turn'
      }
      return {
        content: [{ type: 'text', text: JSON.stringify(result) }],
        details: result,
        terminate: true
      }
    }
  })
}
