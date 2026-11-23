// cspell:ignore convo
/* eslint @typescript-eslint/no-dynamic-delete: 0 */
import { createHash, randomUUID } from 'node:crypto'
import {
  compact, generateBranchSummary, getAgentDir, prepareBranchEntries, SettingsManager
} from '@earendil-works/pi-coding-agent'
import type {
  ExtensionAPI, ExtensionContext, ModelRuntime, SessionBeforeCompactEvent,
  SessionBeforeTreeEvent, SessionEntry
} from '@earendil-works/pi-coding-agent'
import type { Api, Model } from '@earendil-works/pi-ai'
const CLAUDE_CODE_VERSION = '2.1.322'
const CLAUDE_CODE_STAINLESS_VERSION = '0.128.0'
const CLAUDE_CODE_RUNTIME_VERSION = 'v26.12.1'
const VERSION_HASH_SALT = '59cf53e54c78'
const VERSION_HASH_CHARACTER_INDEXES = [4, 7, 20] as const
const BILLING_PREFIX = 'x-anthropic-billing-header:'
const SUPPORTED_EFFORTS = new Set(['low', 'medium', 'high', 'xhigh', 'max'])
const COMMON_BETAS = [
  'claude-code-20250219',
  'oauth-2025-04-20',
  'interleaved-thinking-2025-05-14',
  'context-management-2025-06-27'
]
const CONTROL_BETAS = [
  'per-turn-control-2026-07-01',
  'mid-conversation-tool-changes-2026-07-01',
  'thinking-token-count-2026-05-13',
  'advanced-tool-use-2025-11-20',
  'effort-2025-11-24',
  'dangerous-tool-use-2026-09-03',
  'thinking-display-updates-2026-08-18',
  'afk-mode-2026-01-31',
  'extended-cache-ttl-2025-04-11'
]
const LEGACY_BASE_BETAS = [
  'claude-code-20250219', 'oauth-2025-04-20',
  'interleaved-thinking-2025-05-14', 'redact-thinking-2026-02-12',
  'thinking-token-count-2026-05-13', 'context-management-2025-06-27',
  'prompt-caching-scope-2026-01-05'
]
const LEGACY_COMMON_BETAS = [
  ...LEGACY_BASE_BETAS, 'mid-conversation-system-2026-04-07'
]
const LEGACY_BETAS_BY_MODEL: Record<string, string[]> = {
  'claude-sonnet-5': [...LEGACY_COMMON_BETAS,
    'effort-2025-11-24', 'extended-cache-ttl-2025-04-11'],
  'claude-opus-5': [...LEGACY_COMMON_BETAS,
    'mid-conversation-tool-changes-2026-07-01', 'effort-2025-11-24',
    'fallback-credit-2026-06-01', 'extended-cache-ttl-2025-04-11'],
  'claude-opus-4-8': [...LEGACY_COMMON_BETAS,
    'mid-conversation-tool-changes-2026-07-01', 'effort-2025-11-24',
    'extended-cache-ttl-2025-04-11'],
  'claude-opus-4-7': [...LEGACY_BASE_BETAS,
    'effort-2025-11-24', 'extended-cache-ttl-2025-04-11']
}
const ARRAY_FALLBACK_BETA = 'server-side-fallback-2026-06-01'
const DEFAULT_FALLBACK_BETA = 'server-side-fallback-2026-07-01'
const TOOL_STREAMING_BETA = 'fine-grained-tool-streaming-2025-05-14'
const SCOPED_SYSTEM_BETA = 'mid-conversation-system-clear-at-2026-08-21'
const THINKING_BINDING_BETA = 'thinking-binding-controls-2026-08-01'
const CAPTURED_BETAS_BY_MODEL: Record<string, string[]> = {
  'claude-opus-5-5': [...COMMON_BETAS, ...CONTROL_BETAS],
  'claude-sonnet-5-5': [...COMMON_BETAS, ...CONTROL_BETAS],
  'claude-haiku-5-5': [
    ...COMMON_BETAS.slice(1), COMMON_BETAS[0], ...CONTROL_BETAS
  ],
  'claude-fable-5-1': [...COMMON_BETAS, ...CONTROL_BETAS],
  'claude-fable-5-2': [...COMMON_BETAS, ...CONTROL_BETAS]
}
export type ProviderPayload = Record<string, unknown>
export type ProviderSystemBlock = Record<string, unknown>
function isProviderPayload (payload: unknown): payload is ProviderPayload {
  return typeof payload === 'object' && payload !== null && !Array.isArray(payload)
}
function hasReplayedThinking (messages: unknown): boolean {
  return Array.isArray(messages) && messages.some((message) =>
    isProviderPayload(message) && message.role === 'assistant' &&
    Array.isArray(message.content) && message.content.some((block) =>
      isProviderPayload(block) &&
      ['thinking', 'redacted_thinking'].includes(String(block.type))))
}
function fingerprintBetas (modelId: string): string[] | undefined {
  if (Object.hasOwn(CAPTURED_BETAS_BY_MODEL, modelId)) {
    return CAPTURED_BETAS_BY_MODEL[modelId]
  }
  if (Object.hasOwn(LEGACY_BETAS_BY_MODEL, modelId)) {
    return LEGACY_BETAS_BY_MODEL[modelId]
  }
}
function withFeatureBetas (
  base: string[], payload: ProviderPayload, model?: Model<Api>
): string[] {
  const requested = Array.isArray(payload.betas) ? payload.betas : []
  const compat = model?.compat
  const arrayFallback = Array.isArray(payload.fallbacks)
    ? payload.fallbacks.length > 0
    : payload.fallbacks === undefined && isProviderPayload(compat) &&
      Array.isArray(compat.allowedFallbackModels) &&
      compat.allowedFallbackModels.length > 0
  const fallback = payload.fallbacks === 'default'
    ? DEFAULT_FALLBACK_BETA
    : arrayFallback ? ARRAY_FALLBACK_BETA : undefined
  const streaming = requested.includes(TOOL_STREAMING_BETA) ||
    (Array.isArray(payload.tools) && payload.tools.some((tool) =>
      isProviderPayload(tool) && isProviderPayload(tool.input_schema) &&
      tool.eager_input_streaming !== true))
  const scoped = requested.includes(SCOPED_SYSTEM_BETA) ||
    (Array.isArray(payload.messages) && payload.messages.some((message) =>
      isProviderPayload(message) && message.role === 'system' &&
      message.clear_at === 'next_user_message'))
  const binding = isProviderPayload(payload.thinking) &&
    isProviderPayload(payload.thinking.block_binding) &&
    hasReplayedThinking(payload.messages)
  const explicit = requested.filter((value): value is string =>
    typeof value === 'string' && value.trim() !== '').map((value) => value.trim())
    .filter((value) => fallback === undefined ||
      !value.startsWith('server-side-fallback-') || value === fallback)
  return [...new Set([...base, ...explicit,
    ...(fallback === undefined ? [] : [fallback]),
    ...(streaming ? [TOOL_STREAMING_BETA] : []),
    ...(scoped ? [SCOPED_SYSTEM_BETA] : []),
    ...(binding ? [THINKING_BINDING_BETA] : [])])]
}
function isSystemTextBlock (
  block: unknown
): block is ProviderSystemBlock & { text: string } {
  return (
    typeof block === 'object' &&
    block !== null &&
    !Array.isArray(block) &&
    (block as ProviderSystemBlock).type === 'text' &&
    typeof (block as ProviderSystemBlock).text === 'string'
  )
}
function isAnthropicOAuth (ctx: ExtensionContext): boolean {
  return (
    ctx.model !== undefined &&
    ctx.model.provider === 'anthropic' &&
    ctx.modelRegistry.isUsingOAuth(ctx.model)
  )
}
export function buildHashFor (version: string, firstUserPrompt: string): string {
  const characters = VERSION_HASH_CHARACTER_INDEXES
    .map((index) => firstUserPrompt[index] ?? '0')
    .join('')
  return createHash('sha256')
    .update(`${VERSION_HASH_SALT}${characters}${version}`)
    .digest('hex')
    .slice(0, 3)
}
function firstUserPrompt (payload: ProviderPayload): string {
  if (!Array.isArray(payload.messages)) return ''
  for (const message of payload.messages) {
    if (!isProviderPayload(message) || message.role !== 'user') continue
    const content = message.content
    if (typeof content === 'string') {
      if (!content.startsWith('<system-reminder>')) return content
      continue
    }
    if (!Array.isArray(content)) continue
    if (
      message === payload.messages[0] &&
      content.length > 1 &&
      isSystemTextBlock(content[0]) &&
      /^(?:<system-reminder>\n)?# Environment\nYou have been invoked /u
        .test(content[0].text)
    ) continue
    for (const block of content) {
      if (!isSystemTextBlock(block)) continue
      if (block.text.startsWith('<system-reminder>')) continue
      return block.text
    }
  }
  return ''
}
function systemWithoutBilling (system: unknown): unknown[] {
  const blocks = typeof system === 'string'
    ? [{ type: 'text', text: system }]
    : Array.isArray(system)
      ? [...system]
      : []
  return blocks.filter(
    (block) => !isSystemTextBlock(block) || !block.text.startsWith(BILLING_PREFIX)
  )
}
function stainlessOS (): string {
  switch (process.platform) {
    case 'darwin':
      return 'MacOS'
    case 'win32':
      return 'Windows'
    case 'linux':
      return 'Linux'
    case 'freebsd':
      return 'FreeBSD'
    default:
      return 'Unknown'
  }
}
function stainlessArch (): string {
  switch (process.arch) {
    case 'x64':
      return 'x64'
    case 'arm64':
      return 'arm64'
    case 'ia32':
      return 'x32'
    default:
      return process.arch
  }
}
function setHeader (
  headers: Record<string, string | null>, name: string, value: string
): void {
  for (const key of Object.keys(headers)) {
    // Keep strict-mode delete errors for non-configurable header properties.
    if (key.toLowerCase() === name.toLowerCase()) delete headers[key]
  }
  headers[name] = value
}
export function applyFingerprintHeaders (
  headers: Record<string, string | null>, sessionId: string, modelId = '',
  model?: Model<Api>
): void {
  for (const key of Object.keys(headers)) {
    // Keep strict-mode delete errors for non-configurable header properties.
    if (key.toLowerCase() === 'x-client-request-id') delete headers[key]
  }
  setHeader(headers, 'Accept', 'application/json')
  setHeader(headers, 'anthropic-version', '2023-06-01')
  setHeader(headers, 'anthropic-dangerous-direct-browser-access', 'true')
  setHeader(headers, 'x-app', 'cli')
  // The adapter inserts lowercase user-agent after its default User-Agent key.
  setHeader(headers, 'user-agent', `claude-cli/${CLAUDE_CODE_VERSION} (external, cli)`)
  setHeader(headers, 'X-Stainless-Package-Version', CLAUDE_CODE_STAINLESS_VERSION)
  setHeader(headers, 'X-Stainless-Timeout', '600')
  setHeader(headers, 'X-Stainless-Arch', stainlessArch())
  setHeader(headers, 'X-Stainless-Lang', 'js')
  setHeader(headers, 'X-Stainless-OS', stainlessOS())
  setHeader(headers, 'X-Stainless-Runtime', 'node')
  setHeader(headers, 'X-Stainless-Runtime-Version', CLAUDE_CODE_RUNTIME_VERSION)
  setHeader(headers, 'X-Stainless-Retry-Count', '0')
  setHeader(headers, 'X-Claude-Code-Session-Id', sessionId)
  const betas = fingerprintBetas(modelId)
  if (betas !== undefined) {
    const requested = Object.entries(headers).flatMap(([name, value]) =>
      name.toLowerCase() === 'anthropic-beta' && typeof value === 'string'
        ? value.split(',').map((feature) => feature.trim())
        : [])
    setHeader(headers, 'anthropic-beta',
      withFeatureBetas(betas, { betas: requested }, model).join(','))
  }
}
export function applyFingerprintPayload (
  payload: ProviderPayload, modelId: string, summary = false
): ProviderPayload {
  const prompt = firstUserPrompt(payload)
  const billingBlock: ProviderSystemBlock & { text: string } = {
    type: 'text',
    text: (
      `x-anthropic-billing-header: cc_version=${CLAUDE_CODE_VERSION}.` +
      `${buildHashFor(CLAUDE_CODE_VERSION, prompt)}; cc_entrypoint=cli;`
    )
  }
  const baseBetas = fingerprintBetas(modelId)
  const capturedModel = Object.hasOwn(CAPTURED_BETAS_BY_MODEL, modelId)
  const normalizeControls = capturedModel || modelId === 'claude-opus-5'
  let outputConfig = payload.output_config
  const providerMessages = Array.isArray(payload.messages) ? payload.messages : undefined
  const tail = providerMessages?.at(-1)
  const tailConfig = isProviderPayload(tail) && tail.role === 'system' &&
    Array.isArray(tail.content) && tail.content.length === 0 &&
    isProviderPayload(tail.output_config)
    ? tail.output_config
    : undefined
  const effort = tailConfig?.effort
  const effortTail = typeof effort === 'string' && SUPPORTED_EFFORTS.has(effort)
  const plainEffortTail = effortTail && Object.keys(tailConfig ?? {}).length === 1 &&
    isProviderPayload(tail) && Object.keys(tail).every((key) =>
    ['role', 'content', 'output_config'].includes(key))
  if (capturedModel && effortTail) {
    outputConfig = {
      ...(isProviderPayload(outputConfig) ? outputConfig : {}), effort
    }
  }
  const thinking = payload.thinking
  const replayingThinking = capturedModel && hasReplayedThinking(providerMessages)
  const normalizedThinking = normalizeControls && isProviderPayload(thinking) &&
    thinking.type === 'adaptive'
    ? {
        type: 'adaptive',
        ...(capturedModel ? { display: 'updates' } : {}),
        ...(replayingThinking
          ? {
              block_binding: isProviderPayload(thinking.block_binding)
                ? thinking.block_binding
                : { prefix_mismatch_behavior: 'drop_block' }
            }
          : {})
      }
    : thinking
  const betas = baseBetas === undefined
    ? payload.betas
    : withFeatureBetas(
      baseBetas, { ...payload, thinking: normalizedThinking }
    )
  const hasAssistantHistory = providerMessages?.some((message) =>
    isProviderPayload(message) && message.role === 'assistant') ?? false
  const hasHistoricalControls = providerMessages?.slice(0, -1).some((message) =>
    isProviderPayload(message) && message.role === 'system' &&
    isProviderPayload(message.output_config)) ?? false
  const messages = providerMessages !== undefined && capturedModel
    ? providerMessages.slice(0, summary && plainEffortTail && !hasAssistantHistory &&
      !hasHistoricalControls
      ? -1
      : undefined)
    : normalizeControls && providerMessages !== undefined
      ? providerMessages
        .filter((message) => !isProviderPayload(message) ||
          message.role !== 'system' || !Array.isArray(message.content) ||
          message.content.length > 0)
        .map((message) => {
          if (!isProviderPayload(message) || message.role !== 'system') return message
          const { output_config: outputConfig, ...systemMessage } = message
          void outputConfig
          return systemMessage
        })
      : payload.messages
  return {
    ...payload,
    ...(betas === undefined ? {} : { betas }),
    ...(normalizedThinking === undefined ? {} : { thinking: normalizedThinking }),
    ...(messages === undefined ? {} : { messages }),
    ...(outputConfig === undefined ? {} : { output_config: outputConfig }),
    ...(capturedModel && payload.context_management === undefined &&
      payload.compaction === undefined
      ? {
          context_management: {
            edits: [{ type: 'clear_thinking_20251015', keep: 'all' }]
          }
        }
      : {}),
    system: [billingBlock, ...systemWithoutBilling(payload.system)]
  }
}
export default function anthropicOAuthFingerprint (pi: ExtensionAPI): void {
  const sessionId = randomUUID()
  registerSummaryHooks(pi, {
    sessionId,
    applyHeaders: applyFingerprintHeaders,
    applyPayload: applyFingerprintPayload
  })
  pi.on('before_provider_headers', (event, ctx) => {
    if (!isAnthropicOAuth(ctx)) return
    applyFingerprintHeaders(
      event.headers as Record<string, string | null>, sessionId, ctx.model?.id, ctx.model
    )
  })
  pi.on('before_provider_request', (event, ctx) => {
    if (!isAnthropicOAuth(ctx) || !isProviderPayload(event.payload)) return
    return applyFingerprintPayload(event.payload, ctx.model?.id ?? '')
  })
}
type StreamFn = NonNullable<Parameters<typeof compact>[7]>
export interface FingerprintDependencies {
  sessionId: string
  applyHeaders: (
    headers: Record<string, string | null>, sessionId: string, modelId?: string,
    model?: Model<Api>
  ) => void
  applyPayload: (payload: Record<string, unknown>, modelId: string, summary?: boolean) =>
  Record<string, unknown>
}
interface FileDetails {
  readFiles: string[]
  modifiedFiles: string[]
  [key: string]: unknown
}
type SummaryCategory = 'metadata' | 'auth' | 'settings' | 'provider' |
'summary' | 'aborted'
class SummaryFailure extends Error {
  constructor (readonly category: SummaryCategory) {
    super('Summary operation failed')
    this.name = 'SummaryFailure'
  }
}
type SummaryRuntime = Pick<ModelRuntime, 'streamSimple'>
// The registry runtime is private to TypeScript only; review-pi_dev retry.ts intercepts
// its streamSimple, so summaries reach the retries only through it.
function summaryRuntime (ctx: ExtensionContext): SummaryRuntime {
  const runtime: unknown = Reflect.get(ctx.modelRegistry, 'runtime')
  if (typeof runtime !== 'object' || runtime === null ||
    typeof Reflect.get(runtime, 'streamSimple') !== 'function') {
    throw new SummaryFailure('provider')
  }
  return runtime as SummaryRuntime
}
export function fileDetails (value: unknown): FileDetails | undefined {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return
  const record = value as Record<string, unknown>
  if (!Array.isArray(record.readFiles) ||
    !record.readFiles.every((path) => typeof path === 'string') ||
    !Array.isArray(record.modifiedFiles) ||
    !record.modifiedFiles.every((path) => typeof path === 'string')) return
  return record as FileDetails
}
export function ownDetails (entry: SessionEntry): FileDetails | undefined {
  if (entry.type !== 'compaction' && entry.type !== 'branch_summary') return
  const details = fileDetails(entry.details)
  return details?.aoauthFingerprintSummary === 1 ? details : undefined
}
export function prepareSummaryCompaction (
  event: SessionBeforeCompactEvent
): SessionBeforeCompactEvent['preparation'] {
  const entries = event.branchEntries
  let previousIndex = -1
  for (let index = entries.length - 1; index >= 0; index--) {
    if (entries[index].type === 'compaction') {
      previousIndex = index
      break
    }
  }
  const end = entries.findIndex(
    (entry) => entry.id === event.preparation.firstKeptEntryId
  )
  if (end < 0) throw new Error('Unknown compaction boundary')
  let start = 0
  const previous = entries[previousIndex]
  if (previous?.type === 'compaction') {
    const kept = entries.findIndex((entry) => entry.id === previous.firstKeptEntryId)
    start = kept >= 0 ? kept : previousIndex + 1
  }
  if (start > end) throw new Error('Invalid compaction range')
  const original = event.preparation.fileOps
  const prepared = {
    ...event.preparation,
    fileOps: {
      read: new Set(original.read),
      written: new Set(original.written),
      edited: new Set(original.edited)
    }
  }
  const add = (details: FileDetails | undefined): void => {
    if (details == null) return
    for (const path of details.readFiles) prepared.fileOps.read.add(path)
    for (const path of details.modifiedFiles) prepared.fileOps.edited.add(path)
  }
  if (previous !== undefined) add(ownDetails(previous))
  for (let index = start; index < end; index++) {
    const entry = entries[index]
    if (entry.type === 'branch_summary') add(ownDetails(entry))
  }
  return prepared
}
export function prepareSummaryEntries (entries: SessionEntry[]): SessionEntry[] {
  return entries.map((entry) => (
    entry.type === 'branch_summary' && (ownDetails(entry) != null)
      ? { ...entry, fromHook: false }
      : entry
  ))
}
function isTextBlock (value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const block = value as Record<string, unknown>
  return block.type === 'text' && typeof block.text === 'string' &&
    block.text.trim().length > 0
}
export function isSummaryPayload (
  value: unknown, model: Model<Api>
): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const payload = value as Record<string, unknown>
  if (typeof payload.model !== 'string' || payload.model.length === 0 ||
    typeof payload.max_tokens !== 'number' || !Number.isFinite(payload.max_tokens) ||
    payload.max_tokens <= 0 || !Array.isArray(payload.messages) ||
    payload.messages.length === 0) return false
  const message = payload.messages[0]
  if (typeof message !== 'object' || message === null || Array.isArray(message)) {
    return false
  }
  const user = message as Record<string, unknown>
  const validUser = user.role === 'user' && (typeof user.content === 'string'
    ? user.content.trim().length > 0
    : Array.isArray(user.content) && user.content.length > 0 &&
      user.content.every(isTextBlock))
  const compat = model.compat
  const managed = compat !== undefined && 'supportsMidConvoEffort' in compat &&
    compat.supportsMidConvoEffort === true
  if (payload.messages.length !== (managed ? 2 : 1)) return false
  const validTail = payload.messages.slice(1).every((value) => {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
    const tail = value as Record<string, unknown>
    const config = tail.output_config
    if (typeof config !== 'object' || config === null || Array.isArray(config)) {
      return false
    }
    const effort = (config as Record<string, unknown>).effort
    return tail.role === 'system' && Array.isArray(tail.content) &&
      tail.content.length === 0 && typeof effort === 'string' && effort.length > 0
  })
  const validSystem = typeof payload.system === 'string'
    ? payload.system.trim().length > 0
    : Array.isArray(payload.system) && payload.system.length > 0 &&
      payload.system.every(isTextBlock)
  return validUser && validTail && validSystem
}
export async function waitOrAbort<T> (
  operation: Promise<T>, signal: AbortSignal
): Promise<T> {
  let onAbort = (): void => {}
  const aborted = new Promise<never>((_resolve, reject) => {
    onAbort = () => reject(signal.reason ?? new Error('Aborted'))
    signal.addEventListener('abort', onAbort, { once: true })
    if (signal.aborted) onAbort()
  })
  try {
    return await Promise.race([operation, aborted])
  } finally {
    signal.removeEventListener('abort', onAbort)
  }
}
export function mergeHeaders (
  base: Record<string, string | null> | undefined,
  overrides: Record<string, string | null> | undefined
): Record<string, string | null> {
  const headers = { ...base }
  for (const [name, value] of Object.entries(overrides ?? {})) {
    for (const key of Object.keys(headers)) {
      if (key.toLowerCase() === name.toLowerCase()) {
        Reflect.deleteProperty(headers, key)
      }
    }
    headers[name] = value
  }
  return headers
}
export function readSummarySettings (ctx: ExtensionContext): SettingsManager {
  const settings = SettingsManager.create(ctx.cwd, getAgentDir(), {
    projectTrusted: ctx.isProjectTrusted()
  })
  if (settings.drainErrors().length > 0) {
    throw new Error('Summary settings unavailable')
  }
  return settings
}
export function createSummaryStream (
  ctx: ExtensionContext,
  signal: AbortSignal,
  settings: SettingsManager,
  fingerprint: FingerprintDependencies
): StreamFn {
  return async (model, context, options) => {
    signal.throwIfAborted()
    if (options?.signal !== undefined && options.signal !== signal) {
      throw new SummaryFailure('summary')
    }
    if (typeof options?.maxTokens !== 'number' ||
      !Number.isFinite(options.maxTokens) || options.maxTokens <= 0) {
      throw new SummaryFailure('summary')
    }
    if (model.provider !== 'anthropic' ||
      !ctx.modelRegistry.isUsingOAuth(model)) {
      throw new SummaryFailure('auth')
    }
    const provider = ctx.modelRegistry.getProvider(model.provider)
    if (provider === undefined || typeof provider.streamSimple !== 'function') {
      throw new SummaryFailure('provider')
    }
    const runtime = summaryRuntime(ctx)
    const auth = await waitOrAbort(
      ctx.modelRegistry.getApiKeyAndHeaders(model), signal
    ).catch(() => { throw new SummaryFailure('auth') })
    signal.throwIfAborted()
    if (!auth.ok || !ctx.modelRegistry.isUsingOAuth(model)) {
      throw new SummaryFailure('auth')
    }
    // The runtime resolves auth, baseUrl, headers and env again on every attempt.
    const source = options ?? {}
    const providerRetry = settings.getProviderRetrySettings()
    const idleTimeout = settings.getHttpIdleTimeoutMs()
    const effectiveTimeout = idleTimeout === 0 ? 2147483647 : idleTimeout
    signal.throwIfAborted()
    // A local abort stops the runtime retries after a payload failure.
    const local = new AbortController()
    const requestSignal = AbortSignal.any([signal, local.signal])
    const failure: { payload?: SummaryFailure } = {}
    const stream = runtime.streamSimple(model, context, {
      ...source,
      signal: requestSignal,
      transformHeaders: (headers) => {
        fingerprint.applyHeaders(headers, fingerprint.sessionId, model.id, model)
        return headers
      },
      timeoutMs: source.timeoutMs ?? providerRetry.timeoutMs ?? effectiveTimeout,
      websocketConnectTimeoutMs: source.websocketConnectTimeoutMs ??
        settings.getWebSocketConnectTimeoutMs(),
      maxRetries: source.maxRetries ?? providerRetry.maxRetries,
      maxRetryDelayMs: source.maxRetryDelayMs ?? providerRetry.maxRetryDelayMs,
      onPayload: async (payload, actualModel) => {
        try {
          const replacement = await source.onPayload?.(payload, actualModel)
          const prepared = replacement === undefined ? payload : replacement
          if (!isSummaryPayload(prepared, actualModel)) {
            throw new SummaryFailure('summary')
          }
          return fingerprint.applyPayload(prepared, actualModel.id, true)
        } catch {
          failure.payload = new SummaryFailure('summary')
          local.abort(failure.payload)
          throw failure.payload
        }
      },
      onResponse: source.onResponse
    })
    const response = await waitOrAbort(stream.result(), requestSignal)
      .catch((error: unknown) => { throw failure.payload ?? error })
    signal.throwIfAborted()
    if (response.stopReason === 'aborted') throw new SummaryFailure('aborted')
    if (response.stopReason === 'stop' && !response.content.some((block) =>
      block.type === 'text' && block.text.trim().length > 0)) {
      throw new SummaryFailure('summary')
    }
    return stream
  }
}
async function notifySummaryFailure (
  ctx: ExtensionContext,
  event: SessionBeforeCompactEvent | SessionBeforeTreeEvent,
  category: SummaryCategory,
  target: boolean
): Promise<void> {
  let message = 'Summary metadata preparation failed; operation was cancelled.'
  if (target) {
    if (event.type === 'session_before_tree') {
      message = 'OAuth branch summarization failed or was cancelled; ' +
        'navigation was cancelled.'
    } else if (event.reason === 'overflow' &&
      (event.willRetry as boolean | undefined) === true) {
      message = 'OAuth context compaction failed or was cancelled; the ' +
        'compaction-triggered retry was not performed because compaction ' +
        'failed or was cancelled. Queued messages may still continue.'
    } else if (event.reason === 'overflow') {
      message = 'OAuth context compaction failed or was cancelled; ' +
        'queued messages may still continue.'
    } else if (event.reason === 'manual') {
      message = 'OAuth context compaction failed or was cancelled; ' +
        'history was not compacted.'
    } else {
      message = 'OAuth context compaction failed or was cancelled.'
    }
  }
  try {
    await Promise.resolve(ctx.ui.notify(`${message} Category: ${category}.`, 'error'))
  } catch {
    // Notification failure must not enable fallback.
  }
}
function failureCategory (
  error: unknown, signal: AbortSignal, fallback: SummaryCategory
): SummaryCategory {
  if (signal.aborted) return 'aborted'
  return error instanceof SummaryFailure ? error.category : fallback
}
export function registerSummaryHooks (
  pi: ExtensionAPI,
  fingerprint: FingerprintDependencies,
  readSettings: (ctx: ExtensionContext) => SettingsManager = readSummarySettings
): void {
  pi.on('session_before_compact', async (event, ctx) => {
    let category: SummaryCategory = 'auth'
    let target = false
    try {
      const model = ctx.model
      target = model?.provider === 'anthropic'
      const oauth = target && model !== undefined &&
        Boolean(ctx.modelRegistry.isUsingOAuth(model))
      target = oauth
      category = 'metadata'
      const hasOwn = event.branchEntries.some((entry) => ownDetails(entry) !== undefined)
      if (!oauth && !hasOwn) return
      const preparedCopy = prepareSummaryCompaction(event)
      if (!oauth || model === undefined) {
        for (const path of preparedCopy.fileOps.read) {
          event.preparation.fileOps.read.add(path)
        }
        for (const path of preparedCopy.fileOps.edited) {
          event.preparation.fileOps.edited.add(path)
        }
        return
      }
      event.signal.throwIfAborted()
      category = 'settings'
      const settings = readSettings(ctx)
      category = 'summary'
      const streamFn = createSummaryStream(ctx, event.signal, settings, fingerprint)
      const result = await compact(
        preparedCopy, model, undefined, undefined, event.customInstructions,
        event.signal, ctx.thinkingLevel, streamFn, undefined, settings.getRetrySettings()
      )
      event.signal.throwIfAborted()
      const details = fileDetails(result.details)
      if ((details == null) || result.summary.trim() === '') {
        throw new SummaryFailure('summary')
      }
      return {
        compaction: {
          ...result,
          details: { ...details, aoauthFingerprintSummary: 1 }
        }
      }
    } catch (error) {
      await notifySummaryFailure(
        ctx, event, failureCategory(error, event.signal, category), target
      )
      return { cancel: true }
    }
  })
  pi.on('session_before_tree', async (event, ctx) => {
    if (!event.preparation.userWantsSummary ||
      event.preparation.entriesToSummarize.length === 0) return
    let category: SummaryCategory = 'auth'
    let target = false
    try {
      const model = ctx.model
      target = model?.provider === 'anthropic'
      const oauth = target && model !== undefined &&
        Boolean(ctx.modelRegistry.isUsingOAuth(model))
      target = oauth
      category = 'metadata'
      const entries = prepareSummaryEntries(event.preparation.entriesToSummarize)
      if (!oauth || model === undefined) {
        for (let index = 0; index < entries.length; index++) {
          event.preparation.entriesToSummarize[index] = entries[index]
        }
        return
      }
      event.signal.throwIfAborted()
      category = 'settings'
      const settings = readSettings(ctx)
      category = 'summary'
      const providerStream = createSummaryStream(
        ctx, event.signal, settings, fingerprint
      )
      let streamRequested = false
      const streamFn: StreamFn = async (model, context, options) => {
        streamRequested = true
        return await providerStream(model, context, options)
      }
      const result = await generateBranchSummary(entries, {
        model,
        signal: event.signal,
        customInstructions: event.preparation.customInstructions,
        replaceInstructions: event.preparation.replaceInstructions,
        reserveTokens: settings.getBranchSummarySettings().reserveTokens,
        streamFn,
        retry: settings.getRetrySettings()
      })
      event.signal.throwIfAborted()
      if (result.aborted === true) {
        throw new SummaryFailure('aborted')
      }
      const emptyNativeResult = !streamRequested && result.error === undefined &&
        result.summary === 'No content to summarize' &&
        result.readFiles === undefined && result.modifiedFiles === undefined
      if (emptyNativeResult) {
        const unlimited = prepareBranchEntries(entries)
        if (unlimited.messages.length > 0 || unlimited.fileOps.read.size > 0 ||
          unlimited.fileOps.written.size > 0 || unlimited.fileOps.edited.size > 0) {
          throw new SummaryFailure('summary')
        }
      }
      const details = fileDetails(emptyNativeResult
        ? { readFiles: [], modifiedFiles: [] }
        : { readFiles: result.readFiles, modifiedFiles: result.modifiedFiles })
      if (result.error !== undefined || result.summary === undefined ||
        result.summary.trim() === '' || (details == null)) {
        throw new SummaryFailure('summary')
      }
      return {
        summary: {
          summary: result.summary,
          usage: result.usage,
          details: { ...details, aoauthFingerprintSummary: 1 }
        }
      }
    } catch (error) {
      await notifySummaryFailure(
        ctx, event, failureCategory(error, event.signal, category), target
      )
      return { cancel: true }
    }
  })
}
