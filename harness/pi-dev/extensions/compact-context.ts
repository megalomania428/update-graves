import { Type } from 'typebox'
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent'

export const COMPACT_CONTEXT_MESSAGE_TYPE = 'compact-context'
export const COMPACTION_CANCELLED = 'Compaction cancelled'

const CompactContextParams = Type.Object(
  {
    instructions: Type.Optional(Type.String({
      description: 'What the summary must preserve: goals, decisions, files, open issues'
    })),
    next: Type.Optional(Type.String({
      description: 'Concrete note on what to do right after compaction'
    }))
  },
  { additionalProperties: true }
)

export function isCancellation (error: Error): boolean {
  return error.message === COMPACTION_CANCELLED || error.name === 'AbortError'
}

function optionalText (value: string | undefined): string | undefined {
  const text = value?.trim() ?? ''
  return text !== '' ? text : undefined
}

export function continuationText (next: string | undefined, error?: Error): string {
  const lines = error === undefined
    ? ['Context compaction requested via compact_context has finished.']
    : [
        `Context compaction requested via compact_context failed: ${error.message}`,
        'The context was not compacted.'
      ]
  lines.push('Continue the interrupted task from where it stopped; do not call ' +
    'compact_context again unless explicitly instructed.')
  if (next !== undefined) lines.push('', 'Next step noted before compaction:', next)
  return lines.join('\n')
}

export default function compactContext (pi: ExtensionAPI): void {
  let pending = false
  let generation = 0

  pi.on('session_shutdown', () => {
    generation += 1
    pending = false
  })

  pi.registerTool({
    name: 'compact_context',
    label: 'Compact Context',
    description: 'Compact the conversation context and then continue the current task ' +
      'automatically. Intended for skills only: never call it on your own initiative, ' +
      'only when the user or an active skill explicitly instructs to compact the ' +
      'context. Starting compaction aborts the current turn; when compaction ends, a ' +
      'continuation message arrives and work resumes. Pass instructions to focus the ' +
      'summary and next to note what to do right after compaction.',
    parameters: CompactContextParams,
    async execute (_toolCallId, params, _signal, _onUpdate, ctx) {
      if (pending) throw new Error('Context compaction is already in progress')
      pending = true
      generation += 1
      const current = generation
      const instructions = optionalText(params.instructions)
      const next = optionalText(params.next)
      const settle = (): boolean => {
        if (!pending || current !== generation) return false
        pending = false
        return true
      }
      const resume = (error?: Error): void => {
        pi.sendMessage({
          customType: COMPACT_CONTEXT_MESSAGE_TYPE,
          content: continuationText(next, error),
          display: true,
          details: { error: error?.message, next }
        }, { triggerTurn: true, deliverAs: 'followUp' })
      }
      try {
        ctx.compact({
          customInstructions: instructions,
          onComplete: () => {
            if (settle()) resume()
          },
          onError: (error) => {
            if (!settle()) return
            if (!isCancellation(error)) {
              resume(error)
              return
            }
            if (ctx.hasUI) ctx.ui.notify('Context compaction was cancelled', 'warning')
          }
        })
      } catch (error) {
        settle()
        throw error
      }
      const result = {
        success: true as const,
        message: 'Context compaction started: the current turn is aborted and a ' +
          'continuation message follows when compaction ends'
      }
      return {
        content: [{ type: 'text', text: JSON.stringify(result) }],
        details: result
      }
    }
  })
}
