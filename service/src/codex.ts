import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { JevError } from './jev.ts'

const MAX_OUTPUT_BYTES = 1_048_576
const children = new Set<ChildProcess>()
const plans = new Set<Promise<unknown>>()
let shuttingDown = false

/** Executable entry points opt in; importing this module never changes signal behavior. */
export function installCodexShutdownHandlers(): void {
  for (const signal of ['SIGTERM', 'SIGINT'] as const) {
    process.once(signal, () => {
      shuttingDown = true
      const closed = [...children].map((child) => new Promise<void>((resolve) => {
        child.once('close', () => resolve())
        child.kill('SIGKILL')
      }))
      // Plans settle only after their finally blocks remove temporary workspaces.
      void Promise.allSettled([...closed, ...plans]).then(() => process.exit(signal === 'SIGTERM' ? 143 : 130))
    })
  }
}

/** Only login/runtime environment reaches Codex; gateway and API keys stay here. */
function codexEnvironment(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {}
  for (const key of ['PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'CODEX_HOME', 'LANG', 'LC_ALL', 'SYSTEMROOT']) {
    if (process.env[key] !== undefined) env[key] = process.env[key]
  }
  return env
}

/** Spawn without a shell; wait for termination before the caller removes its workspace. */
export function runCodexProcess(binary: string, args: string[], cwd: string, input: string, timeoutMs: number): Promise<string> {
  if (shuttingDown) return Promise.reject(new JevError('Codex planner is shutting down', 'invalid'))
  return new Promise((resolve, reject) => {
    const child = spawn(binary, args, { cwd, env: codexEnvironment(), stdio: ['pipe', 'pipe', 'pipe'] })
    children.add(child)
    let output = ''
    let bytes = 0
    let failure: JevError | undefined
    const stop = (error: JevError) => {
      failure ??= error
      child.kill('SIGKILL')
    }
    const timer = setTimeout(() => stop(new JevError('Codex planner timed out', 'timeout')), timeoutMs)
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (chunk: string) => {
      bytes += Buffer.byteLength(chunk)
      if (bytes > MAX_OUTPUT_BYTES) stop(new JevError('Codex planner exceeded output limit', 'invalid'))
      else output += chunk
    })
    // Login status is written to stderr. Never include arbitrary CLI diagnostics in errors.
    child.stderr.on('data', (chunk: string) => {
      bytes += Buffer.byteLength(chunk)
      if (bytes > MAX_OUTPUT_BYTES) stop(new JevError('Codex planner exceeded output limit', 'invalid'))
      else if (args[0] === 'login') output += chunk
    })
    child.on('error', () => { failure ??= new JevError('Cannot start Codex CLI; install codex and run codex login', 'invalid') })
    child.stdin.on('error', () => { /* Early exit is handled by close. */ })
    child.on('close', (code) => {
      children.delete(child)
      clearTimeout(timer)
      if (failure) reject(failure)
      else if (code !== 0) reject(new JevError(`Codex CLI exited with code ${code}; check ChatGPT login and model access`, 'invalid'))
      else resolve(output)
    })
    child.stdin.end(input)
  })
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function tokenCount(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined
}

/** Only successful, complete turns count. Usage includes Codex's own prompt overhead. */
export function parseCodexOutput(output: string) {
  let text: string | undefined
  let usage: { inputTokens: number | undefined; outputTokens: number | undefined; cachedInputTokens: number | undefined } | undefined
  for (const line of output.split('\n').filter((line) => line.trim())) {
    let event: unknown
    try { event = JSON.parse(line) } catch { throw new JevError('Codex emitted invalid JSONL', 'invalid') }
    if (!object(event)) throw new JevError('Codex emitted an invalid event', 'invalid')
    if (event.type === 'error' || event.type === 'turn.failed') throw new JevError('Codex planning failed; check ChatGPT quota and model access', 'invalid')
    if (event.type === 'item.completed' && object(event.item) && event.item.type === 'agent_message' && typeof event.item.text === 'string') text = event.item.text
    if (event.type === 'turn.completed' && object(event.usage)) {
      usage = {
        inputTokens: tokenCount(event.usage.input_tokens),
        outputTokens: tokenCount(event.usage.output_tokens),
        cachedInputTokens: tokenCount(event.usage.cached_input_tokens),
      }
    }
  }
  if (!text || !usage) throw new JevError('Codex returned no completed planning turn', 'invalid')
  return { text, usage }
}

type CodexRequest = { prompt: string; model: string; schema: Record<string, unknown>; timeoutMs: number }

export function runCodexJson(request: CodexRequest) {
  const plan = executeCodexJson(request)
  plans.add(plan)
  void plan.then(() => plans.delete(plan), () => plans.delete(plan))
  return plan
}

async function executeCodexJson({ prompt, model, schema, timeoutMs }: CodexRequest) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new JevError('Codex timeout must be positive', 'invalid')
  const started = performance.now()
  const directory = await mkdtemp(join(tmpdir(), 'jev-luna-'))
  try {
    const status = await runCodexProcess('codex', ['login', 'status'], directory, '', Math.min(timeoutMs, 10_000))
    if (!/^Logged in using ChatGPT\s*$/m.test(status)) {
      throw new JevError('Codex planner requires ChatGPT login; API-key authentication is not allowed. Run codex login.', 'invalid')
    }
    const schemaPath = join(directory, 'response.schema.json')
    await writeFile(schemaPath, JSON.stringify(schema))
    const instructionsPath = join(directory, 'instructions.md')
    await writeFile(instructionsPath, 'You are a Pokemon Emerald game decision assistant. Answer the supplied game task using only its evidence and legal actions. Do not use tools. Return only the requested JSON.')
    const remaining = timeoutMs - (performance.now() - started)
    if (remaining <= 0) throw new JevError('Codex planner timed out', 'timeout')
    const output = await runCodexProcess('codex', [
      'exec', '--json', '--ephemeral', '--ignore-user-config', '--ignore-rules',
      '--sandbox', 'read-only', '--skip-git-repo-check', '--model', model,
      '--output-schema', schemaPath,
      '-c', `model_instructions_file=${JSON.stringify(instructionsPath)}`,
      '-c', 'skills.max_context_tokens=1', '-c', 'tools.view_image=false',
      '-c', 'forced_login_method="chatgpt"', '-c', 'approval_policy="never"',
      '-c', 'web_search="disabled"', '-c', 'features.shell_tool=false',
      '-c', 'features.unified_exec=false', '-c', 'features.apps=false',
      '-c', 'features.multi_agent=false', '-c', 'features.skill_search=false',
      '-c', 'features.skip_host_skill_discovery=true', '-c', 'project_doc_max_bytes=0',
      '-c', 'model_reasoning_effort="low"',
      '-',
    ], directory, prompt, remaining)
    return parseCodexOutput(output)
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
}
