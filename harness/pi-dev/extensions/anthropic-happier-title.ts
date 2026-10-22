import type { ExtensionAPI } from '@earendil-works/pi-coding-agent'

export default function (pi: ExtensionAPI): void {
  let firstTurn = true

  pi.on('before_agent_start', (event, ctx) => {
    if (!firstTurn || ctx.model?.provider !== 'anthropic') return
    firstTurn = false

    return {
      systemPrompt: `${event.systemPrompt}

# Required first action

Before any visible text or any other tool call, call \`mcp__happier__change_title\` exactly once with a short descriptive title for this session. This is required, not optional housekeeping.`
    }
  })
}
