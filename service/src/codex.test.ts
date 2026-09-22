import assert from 'node:assert/strict'
import { test } from 'node:test'
import { tmpdir } from 'node:os'
import { parseCodexOutput, runCodexProcess } from './codex.ts'

test('Codex parses complete final response and real usage including cache', () => {
  const result = parseCodexOutput([
    JSON.stringify({ type: 'item.completed', item: { type: 'agent_message', text: '{"hint":"Go north"}' } }),
    JSON.stringify({ type: 'turn.completed', usage: { input_tokens: 120, output_tokens: 30, cached_input_tokens: 80 } }),
  ].join('\n'))
  assert.deepEqual(result, { text: '{"hint":"Go north"}', usage: { inputTokens: 120, outputTokens: 30, cachedInputTokens: 80 } })
})

test('Codex rejects partial, malformed and failed turns', () => {
  assert.throws(() => parseCodexOutput('{oops'), /invalid JSONL/)
  assert.throws(() => parseCodexOutput('{"type":"turn.failed"}'), /planning failed/)
  assert.throws(() => parseCodexOutput('{"type":"item.completed","item":{"type":"agent_message","text":"{}"}}'), /no completed/)
})

test('Codex child consumes stdin literally and excludes API key environment', async () => {
  const oldKey = process.env.OPENAI_API_KEY
  process.env.OPENAI_API_KEY = 'test-secret'
  try {
    const result = await runCodexProcess(process.execPath, ['-e', 'process.stdin.pipe(process.stdout); process.stdout.write(String(process.env.OPENAI_API_KEY))'], tmpdir(), '$(echo unsafe)', 5_000)
    assert.equal(result, 'undefined$(echo unsafe)')
  } finally {
    if (oldKey === undefined) delete process.env.OPENAI_API_KEY
    else process.env.OPENAI_API_KEY = oldKey
  }
})

test('Codex child is terminated on timeout and excessive output', async () => {
  await assert.rejects(runCodexProcess(process.execPath, ['-e', 'setInterval(()=>{}, 1000)'], tmpdir(), '', 50), /timed out/)
  await assert.rejects(runCodexProcess(process.execPath, ['-e', 'process.stdout.write("x".repeat(2_000_000))'], tmpdir(), '', 5_000), /output limit/)
})

test('service SIGTERM kills active Codex and removes its workspace before exiting', { timeout: 10_000 }, async () => {
  const { spawn } = await import('node:child_process')
  const { mkdtemp, writeFile, readFile, rm, access } = await import('node:fs/promises')
  const { join } = await import('node:path')
  const { fileURLToPath } = await import('node:url')
  const directory = await mkdtemp(join(tmpdir(), 'jev-codex-shutdown-test-'))
  const marker = join(directory, 'child.json')
  await writeFile(join(directory, 'codex'), `#!${process.execPath}\nconst fs = require('node:fs');
if (process.argv[2] === 'login') { console.error('Logged in using ChatGPT'); process.exit(0); }
fs.writeFileSync(${JSON.stringify(marker)}, JSON.stringify({pid: process.pid, cwd: process.cwd()}));
setInterval(() => {}, 1000);
`, { mode: 0o700 })
  const service = spawn(process.execPath, [fileURLToPath(new URL('./index.ts', import.meta.url))], {
    env: { ...process.env, PATH: `${directory}:${process.env.PATH}`, JEV_PLANNER_BACKEND: 'codex', JEV_PLANNER_MODEL: 'test-model' },
    stdio: ['pipe', 'ignore', 'ignore'],
  })
  let codexPid: number | undefined
  try {
    service.stdin.end(JSON.stringify({ id: 'shutdown-test', type: 'plan', state: {}, options: { 'walk:test': 'Walk' }, instructions: 'Test', timeoutMs: 120_000 }) + '\n')
    let child: { pid: number; cwd: string } | undefined
    const deadline = Date.now() + 5_000
    while (!child && Date.now() < deadline) {
      try { child = JSON.parse(await readFile(marker, 'utf8')) } catch { await new Promise((resolve) => setTimeout(resolve, 20)) }
    }
    assert.ok(child, 'fake Codex must start')
    codexPid = child.pid
    const closed = new Promise<number | null>((resolve) => service.once('close', (code) => resolve(code)))
    const started = performance.now()
    service.kill('SIGTERM')
    assert.equal(await closed, 143)
    assert.ok(performance.now() - started < 4_000, 'must finish before Python close escalates at five seconds')
    assert.throws(() => process.kill(child.pid, 0), { code: 'ESRCH' })
    await assert.rejects(access(child.cwd), { code: 'ENOENT' })
  } finally {
    service.kill('SIGKILL')
    if (codexPid !== undefined) {
      try { process.kill(codexPid, 'SIGKILL') } catch { /* Already terminated. */ }
    }
    await rm(directory, { recursive: true, force: true })
  }
})
