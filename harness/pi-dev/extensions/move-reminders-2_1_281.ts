// cspell:ignore oneline
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
const promptConfigPath = resolve(extensionDir, '../reminders/cc-2_1_281-p1/move.json')
const placeholderPattern = /\{\{(crs_[^}]*)\}\}/gi

function isProviderPayload (payload: unknown): payload is ProviderPayload {
  return typeof payload === 'object' && payload !== null && !Array.isArray(payload)
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
    const path: string = (isAbsolute(value.$ref) as boolean)
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
    crs_os_ver: `${osType() as string} ${release() as string}`,
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
    crs_memory_dir: needed('crs_memory_dir') ? memoryDir(sessionCwd) : ''
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
  const deferred = new Set(['crs_global_instructions', 'crs_local_instructions'])
  const withoutInstructions = text.replace(placeholderPattern, (marker, name: string) => {
    const key = name.toLowerCase() as keyof PromptValues
    return deferred.has(key) ? marker : values[key] ?? marker
  })
  const unresolved = withoutInstructions.match(/\{\{crs_[^}]*\}\}/gi)
  if (unresolved?.some((marker) => (
    !deferred.has(marker.slice(2, -2).toLowerCase())
  )) === true) {
    throw new Error(`Unknown prompt placeholder: ${unresolved.join(', ')}.`)
  }
  return withoutInstructions
    .replace(/\{\{crs_global_instructions\}\}/gi, values.crs_global_instructions)
    .replace(/\{\{crs_local_instructions\}\}/gi, values.crs_local_instructions)
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
        ? ctx.modelRegistry.getProviderDisplayName(model.provider) as string
        : 'unknown'}/${model?.id ?? 'unknown'}`,
      crs_model_name: model?.name ?? 'unknown'
    }
    const variant = model !== undefined ? prompt.models[model.id] ?? prompt : prompt
    const reminders = variant.reminders.map((text) => resolvePlaceholders(text, values))
    const { tools, messages = [], ...payload } = event.payload
    const system = [
      ...variant.system.map((text) => resolvePlaceholders(text, values)),
      ...appendedSystemPrompts
    ]
    const filteredMessages = messages.filter((message) => message.role !== 'developer')
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
          system: system.map((text, index) => ({
            type: 'text',
            text,
            ...(index > 0 && index < variant.system.length
              ? { cache_control: { type: 'ephemeral' } }
              : {})
          }))
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
