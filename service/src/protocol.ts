import type { JsonObject } from './jev.ts'

/**
 * Newline-delimited JSON over stdin/stdout. One line in, one line out, matched
 * by `id`. stdout carries only protocol lines; anything human-readable goes to
 * stderr so the channel stays parseable.
 */
export type Request =
  | { id: string; type: 'ping' }
  | {
      id: string
      type: 'choose' | 'plan'
      state: JsonObject
      options: Record<string, string>
      instructions: string
      timeoutMs?: number
    }

export type Response =
  | { id: string; type: 'pong'; schemaVersion: number }
  | { id: string; type: 'plan'; hint: string; destinationActionId: string;
      location: string; avoid: string; successSignal: string; model: string; latencyMs: number;
      usage: { inputTokens: number | undefined; outputTokens: number | undefined; cachedInputTokens?: number | undefined } }
  | {
      id: string
      type: 'choice'
      choice: string
      probabilities: Record<string, number>
      confidence?: number
      usage: { inputTokens?: number; outputTokens?: number }
      latencyMs: number
    }
  | { id: string; type: 'error'; kind: 'timeout' | 'gateway' | 'invalid' | 'invalid-response'; message: string; statusCode?: number }

/** Reject a malformed line here rather than letting it reach the gateway. */
export function parseRequest(line: string): Request {
  const parsed: unknown = JSON.parse(line)
  if (typeof parsed !== 'object' || parsed === null) throw new Error('request must be an object')
  const { id, type } = parsed as Record<string, unknown>
  if (typeof id !== 'string' || id === '') throw new Error('request id must be a nonempty string')
  if (type === 'ping') return { id, type }
  if (type !== 'choose' && type !== 'plan') throw new Error(`unknown request type: ${String(type)}`)

  const { state, options, instructions, timeoutMs } = parsed as Record<string, unknown>
  if (typeof state !== 'object' || state === null || Array.isArray(state)) {
    throw new Error('state must be an object of named observations')
  }
  if (typeof instructions !== 'string') throw new Error('instructions must be a string')
  if (typeof options !== 'object' || options === null || Array.isArray(options)) {
    throw new Error('options must be an object of action ID to description')
  }
  for (const [actionId, description] of Object.entries(options)) {
    if (typeof description !== 'string') throw new Error(`description for ${actionId} must be a string`)
  }
  if (timeoutMs !== undefined && (typeof timeoutMs !== 'number' || !Number.isFinite(timeoutMs))) {
    throw new Error('timeoutMs must be a finite number when present')
  }
  return {
    id,
    type,
    state: state as JsonObject,
    options: options as Record<string, string>,
    instructions,
    ...(timeoutMs === undefined ? {} : { timeoutMs }),
  }
}
