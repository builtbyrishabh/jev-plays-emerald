import { createInterface } from 'node:readline'
import { schemaVersion } from './actions.ts'
import { JevError, choose } from './jev.ts'
import { plan } from './planner.ts'
import { type Request, type Response, parseRequest } from './protocol.ts'

/**
 * The decision service. Reads one request per line on stdin and answers on
 * stdout; the Python launcher owns this process's lifetime.
 */
function send(response: Response): void {
  process.stdout.write(`${JSON.stringify(response)}\n`)
}

async function handle(request: Request): Promise<Response> {
  if (request.type === 'ping') return { id: request.id, type: 'pong', schemaVersion }
  if (request.type === 'plan') return { id: request.id, type: 'plan', ...await plan(request) }

  const result = await choose({
    state: request.state,
    options: request.options,
    instructions: request.instructions,
    ...(request.timeoutMs === undefined ? {} : { timeoutMs: request.timeoutMs }),
  })
  return {
    id: request.id,
    type: 'choice',
    choice: result.choice,
    probabilities: result.probabilities,
    ...(result.confidence === undefined ? {} : { confidence: result.confidence }),
    usage: {
      ...(result.usage.inputTokens === undefined ? {} : { inputTokens: result.usage.inputTokens }),
      ...(result.usage.outputTokens === undefined ? {} : { outputTokens: result.usage.outputTokens }),
    },
    latencyMs: result.latencyMs,
  }
}

function errorResponse(id: string, error: unknown): Response {
  if (error instanceof JevError) {
    return {
      id,
      type: 'error',
      kind: error.kind,
      message: error.message,
      ...(error.statusCode === undefined ? {} : { statusCode: error.statusCode }),
    }
  }
  return { id, type: 'error', kind: 'invalid', message: error instanceof Error ? error.message : String(error) }
}

const lines = createInterface({ input: process.stdin })
for await (const line of lines) {
  if (line.trim() === '') continue
  let request: Request
  try {
    request = parseRequest(line)
  } catch (error) {
    // No usable id, so this answers nothing; the caller's request will time out.
    process.stderr.write(`jev-service: unparseable request (${String(error)})\n`)
    continue
  }
  // Not awaited: a slow decision must not block a later ping or shutdown.
  void handle(request).then(send, (error: unknown) => send(errorResponse(request.id, error)))
}
