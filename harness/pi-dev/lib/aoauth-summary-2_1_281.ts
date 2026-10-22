// cspell:ignore convo
import {
  compact, generateBranchSummary, getAgentDir, prepareBranchEntries, SettingsManager
} from '@earendil-works/pi-coding-agent'
import type {
  ExtensionAPI, ExtensionContext, SessionBeforeCompactEvent, SessionBeforeTreeEvent,
  SessionEntry
} from '@earendil-works/pi-coding-agent'
import type { Api, Model } from '@earendil-works/pi-ai'
type StreamFn = NonNullable<Parameters<typeof compact>[7]>
export interface FingerprintDependencies {
  sessionId: string
  applyHeaders: (headers: Record<string, string | null>, sessionId: string) => void
  applyPayload: (payload: Record<string, unknown>, modelId: string) =>
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
    const auth = await waitOrAbort(
      ctx.modelRegistry.getApiKeyAndHeaders(model), signal
    ).catch(() => { throw new SummaryFailure('auth') })
    signal.throwIfAborted()
    if (!auth.ok || !ctx.modelRegistry.isUsingOAuth(model)) {
      throw new SummaryFailure('auth')
    }
    const requestModel = auth.baseUrl !== undefined && auth.baseUrl !== ''
      ? { ...model, baseUrl: auth.baseUrl }
      : model
    const source = options ?? {}
    const headers = mergeHeaders(auth.headers, source.headers)
    fingerprint.applyHeaders(headers, fingerprint.sessionId)
    const env = auth.env !== undefined || source.env !== undefined
      ? { ...auth.env, ...source.env }
      : undefined
    const providerRetry = settings.getProviderRetrySettings()
    const idleTimeout = settings.getHttpIdleTimeoutMs()
    const effectiveTimeout = idleTimeout === 0 ? 2147483647 : idleTimeout
    signal.throwIfAborted()
    const stream = provider.streamSimple(requestModel, context, {
      ...source,
      signal,
      apiKey: source.apiKey ?? auth.apiKey,
      headers,
      env,
      timeoutMs: source.timeoutMs ?? providerRetry.timeoutMs ?? effectiveTimeout,
      websocketConnectTimeoutMs: source.websocketConnectTimeoutMs ??
        settings.getWebSocketConnectTimeoutMs(),
      maxRetries: source.maxRetries ?? providerRetry.maxRetries,
      maxRetryDelayMs: source.maxRetryDelayMs ?? providerRetry.maxRetryDelayMs,
      onPayload: async (payload, actualModel) => {
        const replacement = await source.onPayload?.(payload, actualModel)
        const prepared = replacement === undefined ? payload : replacement
        if (!isSummaryPayload(prepared, actualModel)) throw new SummaryFailure('summary')
        return fingerprint.applyPayload(prepared, actualModel.id)
      },
      onResponse: source.onResponse
    })
    const response = await waitOrAbort(stream.result(), signal)
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
