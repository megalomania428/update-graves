// cspell:ignore oneline worktrees
/* eslint no-useless-return: 0 */
import { readFileSync } from 'node:fs'
import { homedir, release, type as osType } from 'node:os'
import { basename, dirname, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { getAgentDir, type ExtensionAPI } from '@earendil-works/pi-coding-agent'

interface PromptFileReference { $ref: string }
type PromptValue = string | PromptFileReference
type SystemPlacement = 'after_all' | 'after_reminders' | 'before_reminders'

interface PromptVariantSource {
  reminders: PromptValue[]
  system: PromptValue[]
}
type PromptConfigSource = PromptVariantSource & {
  systemPlacement?: SystemPlacement
  models?: Record<string, Partial<PromptVariantSource>>
}
interface PromptVariant {
  reminders: string[]
  system: string[]
}
type PromptConfig = PromptVariant & {
  systemPlacement: SystemPlacement
  models: Record<string, PromptVariant>
}

interface EnvironmentSnapshot {
  crs_cwd: string
  crs_workspace: string
  crs_is_repo: string
  crs_platform: string
  crs_shell: string
  crs_os_ver: string
  crs_branch: string
  crs_master: string
  crs_repo_user: string
  crs_repo_status_short: string
  crs_repo_recent_commits: string
  crs_today: string
  crs_global_instructions_path: string
  crs_global_instructions: string
  crs_local_instructions: string
  crs_memory_dir: string
  crs_worktree_notice: string
}

type PromptValues = EnvironmentSnapshot & {
  crs_model_short: string
  crs_model_full: string
  crs_model_name: string
}

interface ProviderMessage {
  role: string
  content: unknown
  [key: string]: unknown
}

interface ProviderPayload {
  messages?: ProviderMessage[]
  tools?: unknown
  [key: string]: unknown
}

interface ExecResult {
  code: number
  stdout: string
}

const extensionDir = dirname(fileURLToPath(import.meta.url))
const promptConfigPath = resolve(extensionDir, '../reminders/cc-2_1_322-p1/move.json')
const placeholderPattern = /\{\{(crs_[^}]*)\}\}/gi
const oneHourCacheModels = new Set([
  'claude-opus-5-5',
  'claude-sonnet-5-5',
  'claude-haiku-5-5',
  'claude-fable-5-1',
  'claude-fable-5-2'
])
const nativeReminderModels = new Set(oneHourCacheModels)
const userReminderIndexes = new Set([1, 2, 3])

function isProviderPayload (payload: unknown): payload is ProviderPayload {
  return typeof payload === 'object' && payload !== null && !Array.isArray(payload)
}

function effortConfig (
  message: ProviderMessage | undefined
): Record<string, unknown> | undefined {
  if (message?.role === 'system' && Array.isArray(message.content) &&
    message.content.length === 0 && isProviderPayload(message.output_config)) {
    return message.output_config
  }
}

function prependUserContent (content: unknown, reminders: string[]): unknown {
  const blocks = reminders.map((text) => ({ type: 'text', text }))
  if (typeof content === 'string') return [...blocks, { type: 'text', text: content }]
  if (!Array.isArray(content)) return blocks
  let results = 0
  while (isProviderPayload(content[results]) && content[results].type === 'tool_result') {
    results++
  }
  return [...content.slice(0, results), ...blocks, ...content.slice(results)]
}

function isEmptySystem (message: ProviderMessage | undefined): boolean {
  return message?.role === 'system' && Array.isArray(message.content) &&
    message.content.length === 0
}

function isEffort (value: unknown): value is string {
  return typeof value === 'string' &&
    ['low', 'medium', 'high', 'xhigh', 'max'].includes(value)
}

function pureEffortConfig (
  message: ProviderMessage | undefined
): Record<string, unknown> | undefined {
  const config = effortConfig(message)
  if (config === undefined || !isEffort(config.effort) ||
    Object.keys(config).some((key) => key !== 'effort') ||
    Object.keys(message ?? {}).some((key) =>
      !['role', 'content', 'output_config'].includes(key))) {
    return
  }
  return config
}

function isDefaultCache (value: unknown): boolean {
  return isProviderPayload(value) && value.type === 'ephemeral' &&
    Object.keys(value).length === 1
}

function cacheMarkers (value: unknown, markers: unknown[]): void {
  if (Array.isArray(value)) {
    for (const item of value) cacheMarkers(item, markers)
    return
  }
  if (!isProviderPayload(value)) return
  if (value.cache_control != null) markers.push(value.cache_control)
  if (value.type === 'tool_addition' && isProviderPayload(value.tool) &&
    value.tool.type === 'tool_definition' && isProviderPayload(value.tool.definition) &&
    value.tool.definition.cache_control != null) {
    markers.push(value.tool.definition.cache_control)
  }
  if (Array.isArray(value.content)) cacheMarkers(value.content, markers)
}

function lastCacheable (payload: ProviderPayload): ProviderPayload | undefined {
  let last: ProviderPayload | undefined
  if (Array.isArray(payload.tools)) {
    for (const tool of payload.tools) if (isProviderPayload(tool)) last = tool
  }
  if (Array.isArray(payload.system)) {
    for (const block of payload.system) if (isProviderPayload(block)) last = block
  } else if (typeof payload.system === 'string') last = undefined
  if (Array.isArray(payload.messages)) {
    for (const message of payload.messages) {
      if (!isProviderPayload(message) ||
        message.clear_at === 'next_user_message') continue
      if (typeof message.content === 'string') last = undefined
      if (Array.isArray(message.content)) {
        for (const block of message.content) {
          if (!isProviderPayload(block)) continue
          last = block
          if (block.type === 'tool_addition' && isProviderPayload(block.tool) &&
            block.tool.type === 'tool_definition' &&
            isProviderPayload(block.tool.definition)) {
            last = block.tool.definition
          }
          if (block.type === 'tool_result' && Array.isArray(block.content)) {
            for (const nested of block.content) {
              if (isProviderPayload(nested)) last = nested
            }
          }
        }
      }
    }
  }
  return last
}

function validCache (payload: ProviderPayload): boolean {
  if (Array.isArray(payload.messages)) {
    for (const message of payload.messages) {
      if (!isProviderPayload(message) || message.clear_at !== 'next_user_message') {
        continue
      }
      const scoped: unknown[] = []
      cacheMarkers(message.content, scoped)
      if (scoped.length > 0) return false
    }
  }
  const markers: unknown[] = []
  cacheMarkers(payload.tools, markers)
  cacheMarkers(payload.system, markers)
  cacheMarkers(payload.messages, markers)
  const automatic = payload.cache_control
  const last = lastCacheable(payload)?.cache_control
  if (automatic != null) {
    if (!isProviderPayload(automatic) || automatic.type !== 'ephemeral' ||
      (automatic.ttl !== undefined && automatic.ttl !== '5m' && automatic.ttl !== '1h')) {
      return false
    }
    if (last != null) {
      if (!isProviderPayload(last) ||
        (automatic.ttl === '1h') !== (last.ttl === '1h')) return false
    } else {
      markers.push(automatic)
    }
  }
  if (markers.length > 4) return false
  let short = false
  for (const marker of markers) {
    if (!isProviderPayload(marker) || marker.type !== 'ephemeral' ||
      (marker.ttl !== undefined && marker.ttl !== '5m' && marker.ttl !== '1h')) {
      return false
    }
    if (marker.ttl === '1h' && short) return false
    if (marker.ttl !== '1h') short = true
  }
  return true
}

function normalizeSdkTools (tools: unknown, sdkDefault: boolean): unknown {
  // Pi marks the last immediate tool before its deferred tools.
  if (!sdkDefault || !Array.isArray(tools) || tools.length === 0) return tools
  let index = tools.length - 1
  while (isProviderPayload(tools[index]) && tools[index].defer_loading === true) index--
  const last = tools[index]
  if (!isProviderPayload(last) || !isDefaultCache(last.cache_control)) return tools
  const { cache_control: cacheControl, ...tool } = last
  void cacheControl
  return [...tools.slice(0, index), tool, ...tools.slice(index + 1)]
}

function normalizeSdkUserMarker (
  messages: ProviderMessage[], tools: unknown, payload: ProviderPayload,
  sdkDefault: boolean, firstUserIndex: number
): ProviderMessage[] {
  if (!sdkDefault) return messages
  let last = messages.length - 1
  while (last >= 0 && isEmptySystem(messages[last])) last--
  const message = messages[last]
  if (message?.role !== 'user' || !Array.isArray(message.content)) return messages
  const block = message.content.at(-1)
  if (!isProviderPayload(block) ||
    !['text', 'tool_result'].includes(String(block.type)) ||
    !isDefaultCache(block.cache_control)) return messages
  const initial = last === firstUserIndex &&
    !messages.some((item) => item.role === 'assistant')
  const { cache_control: cacheControl, ...rest } = block
  void cacheControl
  const replacement = initial
    ? rest
    : {
        ...rest, cache_control: { type: 'ephemeral', ttl: '1h' }
      }
  const updated = [...messages]
  updated[last] = { ...message, content: [...message.content.slice(0, -1), replacement] }
  return initial || validCache({ ...payload, tools, system: [], messages: updated })
    ? updated
    : messages
}

function nativeInstructions (text: string): string {
  return text.replace(/<!--[\s\S]*?-->/gu, '')
    .replace(/\n(?:[\t ]*\n){2,}/gu, '\n\n').trim()
}

function removeEmptyGlobalInstructions (text: string): string {
  return text.replace(
    /Contents of [^\n]+ \(user's private global instructions for all projects\):\n{4}/u,
    ''
  )
}

function commandLineFlagValue (name: string): string | undefined {
  const flag = `--${name}`
  for (let index = 2; index < process.argv.length; index++) {
    const argument: string | undefined = process.argv[index]
    if (argument === undefined || argument === '') continue
    if (argument === flag) return process.argv[index + 1]
    if (argument.startsWith(`${flag}=`)) return argument.slice(flag.length + 1)
  }
}

function loadHappierPromptAddition (): string | undefined {
  const configPath = commandLineFlagValue('happy-tools-config')
  if (configPath === undefined || configPath.trim() === '') return
  try {
    const config = JSON.parse(readFileSync(configPath, 'utf8')) as unknown
    if (typeof config !== 'object' || config === null || Array.isArray(config)) return
    const promptAddition = (config as { promptAddition?: unknown }).promptAddition
    return typeof promptAddition === 'string' && promptAddition.trim() !== ''
      ? promptAddition
      : undefined
  } catch {
    return
  }
}

function isPromptFileReference (value: unknown): value is PromptFileReference {
  return (
    typeof value === 'object' &&
    value !== null &&
    !Array.isArray(value) &&
    Object.keys(value).length === 1 &&
    typeof (value as { $ref?: unknown }).$ref === 'string' &&
    (value as { $ref: string }).$ref.length > 0
  )
}

function loadPromptEntry (
  value: unknown, section: 'reminders' | 'system', index: number, configDir: string
): string {
  if (typeof value === 'string' && value.length > 0) return value
  if (isPromptFileReference(value)) {
    const path: string = isAbsolute(value.$ref)
      ? value.$ref
      : resolve(configDir, value.$ref)
    try {
      return readFileSync(path, 'utf8')
    } catch (error) {
      throw new Error(
        `Failed to load prompt entry at ${section}[${index}]: ${path}.`, { cause: error }
      )
    }
  }
  throw new Error(`Invalid prompt entry at ${section}[${index}].`)
}

function loadPromptConfig (): PromptConfig {
  const config = JSON.parse(readFileSync(promptConfigPath, 'utf8')) as PromptConfigSource
  const configDir = dirname(promptConfigPath)
  if (!Array.isArray(config.reminders) || !Array.isArray(config.system)) {
    throw new Error('Invalid Omni Claude Code prompt configuration.')
  }
  const systemPlacement = config.systemPlacement ?? 'after_all'
  if (
    systemPlacement !== 'after_all' &&
    systemPlacement !== 'after_reminders' &&
    systemPlacement !== 'before_reminders'
  ) {
    throw new Error(`Invalid systemPlacement: ${String(config.systemPlacement)}.`)
  }
  const loadVariant = (variant: PromptVariantSource): PromptVariant => {
    if (!Array.isArray(variant.reminders) || !Array.isArray(variant.system)) {
      throw new Error('Invalid Omni Claude Code prompt variant.')
    }
    return {
      reminders: variant.reminders.map((value, index) => (
        loadPromptEntry(value, 'reminders', index, configDir)
      )),
      system: variant.system.map((value, index) => (
        loadPromptEntry(value, 'system', index, configDir)
      ))
    }
  }
  return {
    ...loadVariant(config),
    systemPlacement,
    models: Object.fromEntries(
      Object.entries(config.models ?? {}).map(([id, variant]) => [
        id,
        loadVariant({
          reminders: variant.reminders ?? config.reminders,
          system: variant.system ?? config.system
        })
      ])
    )
  }
}

function usedPlaceholders (prompt: PromptConfig): Set<string> {
  const placeholders = new Set<string>()
  for (const variant of [prompt, ...Object.values(prompt.models)]) {
    for (const text of [...variant.reminders, ...variant.system]) {
      for (const match of text.matchAll(placeholderPattern)) {
        placeholders.add(match[1].toLowerCase())
      }
    }
  }
  return placeholders
}

function trimOutput (stdout: string, multiline = false): string {
  const value = stdout.replace(/[\r\n]+$/u, '')
  return multiline ? value : value.replace(/[\r\n]/gu, '')
}

async function runGit (
  pi: ExtensionAPI, cwd: string, args: string[]
): Promise<ExecResult> {
  try {
    return (await pi.exec('git', args, { cwd, timeout: 1111 })) as ExecResult
  } catch {
    return { code: 1, stdout: '' }
  }
}

async function resolveMainBranch (pi: ExtensionAPI, cwd: string): Promise<string> {
  const remoteHead = await runGit(pi, cwd, [
    'symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD'
  ])
  if (remoteHead.code === 0 && trimOutput(remoteHead.stdout) !== '') {
    return trimOutput(remoteHead.stdout).replace(/^origin\//u, '')
  }
  for (const branch of ['master', 'main']) {
    const result = await runGit(pi, cwd, [
      'show-ref', '--verify', '--quiet', `refs/heads/${branch}`
    ])
    if (result.code === 0) return branch
  }
  return 'unknown'
}

function shellName (): string {
  const comSpec: string | undefined = process.env.ComSpec
  const shell: string | undefined = process.platform === 'win32'
    ? (comSpec !== undefined && comSpec !== '' ? comSpec : process.env.COMSPEC)
    : process.env.SHELL
  if (shell !== undefined && shell !== '') return basename(shell)
  if (process.platform === 'linux') return 'bash'
  if (process.platform === 'win32') return 'cmd'
  return 'sh'
}

function localDate (date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

function globalInstructions (): { path: string, text: string } {
  const paths = [
    resolve(homedir(), '.AGENTS.md'), resolve(homedir(), '.claude/CLAUDE.md')
  ]
  for (const path of paths) {
    try {
      return { path, text: readFileSync(path, 'utf8') }
    } catch {
      // Try the configured fallback file.
    }
  }
  return { path: '(none)', text: '' }
}

function localInstructions (cwd: string): string {
  try {
    return readFileSync(join(cwd, 'AGENTS.md'), 'utf8')
  } catch {
    return ''
  }
}

function memoryDir (cwd: string): string {
  const encodedCwd = resolve(cwd).replace(/[^A-Za-z0-9-]/gu, '-')
  return join(getAgentDir(), 'projects', encodedCwd, 'memory')
}

async function collectEnvironmentSnapshot (
  pi: ExtensionAPI, cwd: string, placeholders: Set<string>
): Promise<EnvironmentSnapshot> {
  const sessionCwd = cwd
  const sessionDate = new Date()
  const needed = (name: keyof EnvironmentSnapshot): boolean => placeholders.has(name)
  const displayCwd = sessionCwd.replace(/\\/gu, '/')
  const needsGlobal = needed('crs_global_instructions') ||
    needed('crs_global_instructions_path')
  const global = needsGlobal
    ? globalInstructions()
    : { path: '(none)', text: '' }
  const snapshot: EnvironmentSnapshot = {
    crs_cwd: displayCwd,
    crs_workspace: displayCwd,
    crs_is_repo: 'false',
    crs_platform: process.platform,
    crs_shell: shellName(),
    crs_os_ver: `${osType()} ${release()}`,
    crs_branch: 'unknown',
    crs_master: 'unknown',
    crs_repo_user: 'unknown',
    crs_repo_status_short: '(none)',
    crs_repo_recent_commits: '(none)',
    crs_today: localDate(sessionDate),
    crs_global_instructions_path: global.path,
    crs_global_instructions: global.text,
    crs_local_instructions: needed('crs_local_instructions')
      ? localInstructions(sessionCwd)
      : '',
    crs_memory_dir: needed('crs_memory_dir') ? memoryDir(sessionCwd) : '',
    crs_worktree_notice: ''
  }
  const needsGit = [
    'crs_workspace',
    'crs_is_repo',
    'crs_branch',
    'crs_master',
    'crs_repo_user',
    'crs_repo_status_short',
    'crs_repo_recent_commits'
  ].some((name) => placeholders.has(name))
  if (!needsGit) return snapshot

  const repository = await runGit(pi, sessionCwd, ['rev-parse', '--is-inside-work-tree'])
  const isRepository = repository.code === 0 && trimOutput(repository.stdout) === 'true'
  snapshot.crs_is_repo = String(isRepository)
  if (isRepository && placeholders.has('crs_worktree_notice')) {
    const [gitDir, commonDir] = await Promise.all([
      runGit(pi, sessionCwd, ['rev-parse', '--git-dir']),
      runGit(pi, sessionCwd, ['rev-parse', '--git-common-dir'])
    ])
    if (gitDir.code === 0 && commonDir.code === 0) {
      const worktreePath = resolve(sessionCwd, trimOutput(gitDir.stdout))
      const commonPath = resolve(sessionCwd, trimOutput(commonDir.stdout))
      if (worktreePath !== commonPath) {
        snapshot.crs_worktree_notice =
          ' - This is a git worktree — an isolated copy of the repository. Run all ' +
          'commands from this directory. Do NOT `cd` to the original repository root.\n' +
          ' - The git stash stack is shared with the main checkout and all other ' +
          'worktrees, and other Claude sessions may push or pop it concurrently. ' +
          'Never use bare `git stash` / `git stash pop` — you could pop another ' +
          'session\'s changes. Prefer a temporary WIP commit to set work aside; if ' +
          'you must stash, use `git stash push -u -m "<unique-tag>"`, immediately ' +
          'capture your entry\'s SHA via `git stash list --format=\'%H %gs\'`, ' +
          'restore with `git stash apply <sha>` (not pop), and afterwards drop the ' +
          'entry, re-finding its current `stash@{n}` by tag first.'
      }
    }
  }
  if (!isRepository) {
    snapshot.crs_branch = '(not a git repository)'
    snapshot.crs_master = '(not a git repository)'
    snapshot.crs_repo_status_short = '(not a git repository)'
    return snapshot
  }

  const [workspace, branch, master, user, status, commits] = await Promise.all([
    needed('crs_workspace')
      ? runGit(pi, sessionCwd, ['rev-parse', '--show-toplevel'])
      : Promise.resolve({ code: 1, stdout: '' }),
    needed('crs_branch')
      ? runGit(pi, sessionCwd, ['symbolic-ref', '--quiet', '--short', 'HEAD'])
      : Promise.resolve({ code: 1, stdout: '' }),
    needed('crs_master') ? resolveMainBranch(pi, sessionCwd) : Promise.resolve('unknown'),
    needed('crs_repo_user')
      ? runGit(pi, sessionCwd, ['config', 'user.name'])
      : Promise.resolve({ code: 1, stdout: '' }),
    needed('crs_repo_status_short')
      ? runGit(pi, sessionCwd, ['--no-optional-locks', 'status', '--short'])
      : Promise.resolve({ code: 1, stdout: '' }),
    needed('crs_repo_recent_commits')
      ? runGit(pi, sessionCwd, ['--no-pager', 'log', '-5', '--oneline'])
      : Promise.resolve({ code: 1, stdout: '' })
  ])
  if (needed('crs_workspace') && workspace.code === 0) {
    const value = trimOutput(workspace.stdout).replace(/\\/gu, '/')
    snapshot.crs_workspace = value !== '' ? value : displayCwd
  }
  if (needed('crs_branch')) {
    if (branch.code === 0) {
      const value = trimOutput(branch.stdout)
      snapshot.crs_branch = value !== '' ? value : 'unknown'
    } else {
      snapshot.crs_branch = 'detached'
    }
  }
  snapshot.crs_master = master
  if (needed('crs_repo_user')) {
    const value = trimOutput(user.stdout)
    snapshot.crs_repo_user = value !== '' ? value : 'unknown'
  }
  if (needed('crs_repo_status_short')) {
    const value = trimOutput(status.stdout, true)
    snapshot.crs_repo_status_short = value !== '' ? value : '(clean)'
  }
  if (needed('crs_repo_recent_commits')) {
    const value = trimOutput(commits.stdout, true)
    snapshot.crs_repo_recent_commits = value !== '' ? value : '(none)'
  }
  return snapshot
}

function resolvePlaceholders (text: string, values: PromptValues): string {
  return text.replace(placeholderPattern, (marker, name: string) => {
    const key = name.toLowerCase() as keyof PromptValues
    if (!Object.hasOwn(values, key)) {
      throw new Error(`Unknown prompt placeholder: ${marker}.`)
    }
    return values[key]
  })
}

export default function prependClaudeCodePrompt (pi: ExtensionAPI): void {
  const prompt = loadPromptConfig()
  const placeholders = usedPlaceholders(prompt)
  let environmentSnapshot: Promise<EnvironmentSnapshot> | undefined
  let appendedSystemPrompts: string[] = []

  pi.on('session_start', (_event, ctx) => {
    environmentSnapshot = collectEnvironmentSnapshot(pi, ctx.cwd, placeholders)
  })

  pi.on('before_agent_start', (event) => {
    appendedSystemPrompts = [
      loadHappierPromptAddition(),
      event.systemPromptOptions.appendSystemPrompt
    ].filter((text, index, values): text is string => (
      typeof text === 'string' && text.trim().length > 0 && values.indexOf(text) === index
    ))
  })

  pi.on('before_provider_request', async (event, ctx) => {
    if (
      !['omni', 'anthropic'].includes(ctx.model?.provider ?? '') ||
      !isProviderPayload(event.payload)
    ) return
    environmentSnapshot ??= collectEnvironmentSnapshot(pi, ctx.cwd, placeholders)
    const snapshot = await environmentSnapshot
    const model: { id: string, provider: string, name: string } | undefined = ctx.model
    const values: PromptValues = {
      ...snapshot,
      crs_model_short: model?.id ?? 'unknown',
      crs_model_full: `${model !== undefined
        ? ctx.modelRegistry.getProviderDisplayName(model.provider)
        : 'unknown'}/${model?.id ?? 'unknown'}`,
      crs_model_name: model?.name ?? 'unknown'
    }
    const useNativeReminderPlacement = nativeReminderModels.has(model?.id ?? '')
    if (useNativeReminderPlacement) {
      values.crs_model_name = values.crs_model_name.replace(/^Claude\s+/iu, '')
      values.crs_memory_dir += '/'
      values.crs_global_instructions = nativeInstructions(values.crs_global_instructions)
      values.crs_local_instructions = nativeInstructions(values.crs_local_instructions)
    }
    const variant = model !== undefined ? prompt.models[model.id] ?? prompt : prompt
    const reminders = variant.reminders.map((text, index) => resolvePlaceholders(
      useNativeReminderPlacement && (index === 0 || index === 2)
        ? text.replace(/\n$/u, '')
        : text,
      values
    ))
    const { tools, messages = [], ...payload } = event.payload
    const cacheSystemForOneHour = oneHourCacheModels.has(model?.id ?? '')
    const system = [
      ...variant.system.map((text) => resolvePlaceholders(
        useNativeReminderPlacement ? text.replace(/\n$/u, '') : text, values
      )),
      ...appendedSystemPrompts
    ]
    const filteredMessages = messages.filter((message) => message.role !== 'developer')
    const systemBlocks = system.map((text, index) => ({
      type: 'text',
      text,
      ...(!useNativeReminderPlacement && index < variant.system.length &&
        (index > 0 || cacheSystemForOneHour)
        ? {
            cache_control: {
              type: 'ephemeral',
              ...(cacheSystemForOneHour ? { ttl: '1h' } : {})
            }
          }
        : {})
    }))
    if (useNativeReminderPlacement) {
      const userReminders = reminders.flatMap((text, index) => {
        if (!userReminderIndexes.has(index)) return []
        const value = index === 1 && values.crs_global_instructions.trim() === ''
          ? removeEmptyGlobalInstructions(text)
          : text
        return value.trim() !== '' ? [value] : []
      })
      const nativeSystemText = reminders[0] ?? ''
      const currentEffort = effortConfig(filteredMessages.at(-1))
      const outputConfig = currentEffort !== undefined && isEffort(currentEffort.effort)
        ? {
            ...(isProviderPayload(payload.output_config) ? payload.output_config : {}),
            effort: currentEffort.effort
          }
        : payload.output_config
      // The fingerprint hook owns billing and may run before this hook.
      const previousSystem: unknown = event.payload.system
      const billing = Array.isArray(previousSystem)
        ? previousSystem.filter((block: unknown) => isProviderPayload(block) &&
          typeof block.text === 'string' &&
          block.text.startsWith('x-anthropic-billing-header:'))
        : []
      const sdkSystemMarkers: unknown[] = []
      cacheMarkers(previousSystem, sdkSystemMarkers)
      const sdkDefault = sdkSystemMarkers.some(isDefaultCache)
      const normalizedTools = normalizeSdkTools(tools, sdkDefault)
      let firstUserIndex = filteredMessages.findIndex(
        (message) => message.role === 'user'
      )
      const originalFirstUser = firstUserIndex
      const placedMessages = normalizeSdkUserMarker(
        [...filteredMessages], normalizedTools, payload, sdkDefault, firstUserIndex
      )
      const firstConversation = placedMessages.find((message) => !isEmptySystem(message))
      if (firstUserIndex === -1 || firstConversation?.role === 'assistant') {
        placedMessages.unshift({ role: 'user', content: [] })
        firstUserIndex = 0
      }
      const firstUser = placedMessages[firstUserIndex]
      placedMessages[firstUserIndex] = {
        ...firstUser, content: prependUserContent(firstUser.content, userReminders)
      }
      let inSystemSection = false
      for (const message of placedMessages) {
        if (isEmptySystem(message)) continue
        if (message.role === 'system') {
          inSystemSection = true
        } else {
          if (message.role === 'user' && inSystemSection) {
            throw new Error('Cannot preserve an invalid system reminder between users.')
          }
          inSystemSection = false
        }
      }
      // Place the environment after the entire first user group, not inside it.
      let lastUserIndex = firstUserIndex
      let existingSystemIndex = -1
      for (let index = firstUserIndex + 1; index < placedMessages.length; index++) {
        const message = placedMessages[index]
        if (message.role === 'user') {
          if (existingSystemIndex !== -1) {
            throw new Error('Cannot preserve an invalid system reminder between users.')
          }
          lastUserIndex = index
        } else if (isEmptySystem(message)) {
          continue
        } else if (message.role === 'system') {
          if (existingSystemIndex === -1) existingSystemIndex = index
        } else {
          break
        }
      }
      const firstControlIndex = lastUserIndex + 1
      const firstControl = placedMessages[firstControlIndex]
      // Automatic cache targets move; caller system sections must stay unchanged.
      const environmentInSystem = payload.cache_control != null ||
        existingSystemIndex !== -1
      const firstEffort = !environmentInSystem && lastUserIndex === firstUserIndex
        ? pureEffortConfig(firstControl)
        : undefined
      const historicalControl = firstEffort !== undefined &&
        placedMessages.slice(firstControlIndex + 1).some(
          (message) => message.role === 'assistant'
        )
      const initialTurn = originalFirstUser !== -1 &&
        placedMessages.filter((message) => message.role === 'user').length === 1 &&
        !placedMessages.some((message) => message.role === 'assistant')
      const pureCurrent = firstEffort !== undefined &&
        firstControlIndex === placedMessages.length - 1
      const hasEarlierControls = placedMessages.slice(0, -1).some((message) =>
        message.role === 'system' && isProviderPayload(message.output_config))
      let nativeConfig: Record<string, unknown> | undefined
      if (!environmentInSystem) {
        if (historicalControl || (initialTurn && pureCurrent && !hasEarlierControls)) {
          nativeConfig = firstEffort
          placedMessages.splice(firstControlIndex, 1)
        }
      }
      // Keep the initially cached environment's content shape stable on replay.
      const environment: ProviderMessage | undefined = environmentInSystem
        ? undefined
        : {
            role: 'system',
            content: sdkDefault && nativeConfig === undefined
              ? [{ type: 'text', text: nativeSystemText }]
              : nativeSystemText,
            ...(nativeConfig === undefined ? {} : { output_config: nativeConfig })
          }
      if (environment !== undefined) {
        placedMessages.splice(lastUserIndex + 1, 0, environment)
      }
      const nativeSystem = [
        ...billing, ...systemBlocks,
        ...(environmentInSystem ? [{ type: 'text', text: nativeSystemText }] : [])
      ]
      const result: ProviderPayload = {
        ...payload,
        ...(outputConfig === undefined ? {} : { output_config: outputConfig }),
        messages: placedMessages,
        system: nativeSystem,
        ...(normalizedTools === undefined ? {} : { tools: normalizedTools })
      }
      if (!validCache(result)) {
        throw new Error('Cannot preserve invalid caller cache markers.')
      }
      if (sdkSystemMarkers.length > 0) {
        // Allocate native markers only when the complete body remains valid.
        const cache = { type: 'ephemeral', ttl: '1h' }
        const systemEnd = billing.length + variant.system.length
        for (let index = billing.length; index < systemEnd; index++) {
          const block = nativeSystem[index]
          nativeSystem[index] = { ...block, cache_control: cache }
          if (!validCache(result)) nativeSystem[index] = block
        }
        if (initialTurn && environment !== undefined) {
          const previousContent = environment.content
          environment.content = typeof previousContent === 'string'
            ? [{ type: 'text', text: previousContent, cache_control: cache }]
            : Array.isArray(previousContent)
              ? [{ ...previousContent[0], cache_control: cache },
                  ...previousContent.slice(1)]
              : previousContent
          if (!validCache(result)) environment.content = previousContent
        }
      }
      return result
    }
    const combined = prompt.systemPlacement === 'before_reminders'
      ? [...system, ...reminders]
      : [...reminders, ...system]
    const result = prompt.systemPlacement === 'after_all'
      ? {
          ...payload,
          messages: [
            { role: 'user', content: reminders.map((text) => ({ type: 'text', text })) },
            ...filteredMessages
          ],
          system: systemBlocks
        }
      : {
          ...payload,
          messages: [
            { role: 'user', content: combined.map((text) => ({ type: 'text', text })) },
            ...filteredMessages
          ]
        }
    return { ...result, ...(tools === undefined ? {} : { tools }) }
  })
}
