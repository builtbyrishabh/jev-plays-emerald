import { gateway } from '@ai-sdk/gateway'
// Exported as experimental: the evaluation-model API can still change shape.
import { experimental_evaluate as evaluate } from 'ai'
import { actionKind, isKnownActionKind } from './actions.ts'

export const JEV_MODEL = 'typesafe-ai/jev'
export const DEFAULT_TIMEOUT_MS = 30_000

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | JsonObject

export type JsonObject = { [key: string]: JsonValue }

export type ChoiceRequest = {
  /** The observation, always an object: the model needs named facts, not a scalar. */
  state: JsonObject
  /** Action ID to the description Jev reads. */
  options: Record<string, string>
  instructions: string
  timeoutMs?: number
}

export type ChoiceResult = {
  choice: string
  probabilities: Record<string, number>
  confidence: number | undefined
  usage: { inputTokens: number | undefined; outputTokens: number | undefined }
  latencyMs: number
}

export class JevError extends Error {
  /** "timeout" and 429/5xx are the cases the mode is allowed to retry. */
  readonly kind: 'timeout' | 'gateway' | 'invalid'
  readonly statusCode: number | undefined

  constructor(message: string, kind: JevError['kind'], statusCode?: number) {
    super(message)
    this.name = 'JevError'
    this.kind = kind
    this.statusCode = statusCode
  }
}

/**
 * Ask Jev to pick one of the offered actions.
 *
 * Deliberately has no retry loop: the Python mode owns retry and pause policy,
 * and one failure retried at two layers is one failure charged twice.
 */
export async function choose({
  state,
  options,
  instructions,
  timeoutMs = DEFAULT_TIMEOUT_MS,
}: ChoiceRequest): Promise<ChoiceResult> {
  const ids = Object.keys(options)
  if (ids.length === 0) throw new JevError('at least one choice option is required', 'invalid')

  const unrunnable = ids.filter((id) => !isKnownActionKind(actionKind(id)))
  if (unrunnable.length > 0) {
    throw new JevError(`no executor for action kind: ${unrunnable.join(', ')}`, 'invalid')
  }

  const started = performance.now()
  try {
    const { answers, usage, providerMetadata } = await evaluate({
      model: gateway.evaluationModel(JEV_MODEL),
      state,
      questions: { action: { type: 'choice', instructions, criteria: options } },
      maxRetries: 0,
      abortSignal: AbortSignal.timeout(timeoutMs),
    })

    return {
      choice: answers.action.choice,
      probabilities: answers.action.probabilities ?? {},
      confidence: readConfidence(providerMetadata),
      usage: { inputTokens: usage.inputTokens, outputTokens: usage.outputTokens },
      latencyMs: performance.now() - started,
    }
  } catch (error) {
    throw asJevError(error, timeoutMs)
  }
}

function readConfidence(metadata: unknown): number | undefined {
  const confidence = read(read(read(metadata, 'typesafe'), 'confidence'), 'action')
  return typeof confidence === 'number' && Number.isFinite(confidence) ? confidence : undefined
}

function read(value: unknown, key: string): unknown {
  return typeof value === 'object' && value !== null && key in value
    ? (value as Record<string, unknown>)[key]
    : undefined
}

function asJevError(error: unknown, timeoutMs: number): JevError {
  if (error instanceof JevError) return error
  if (error instanceof Error && (error.name === 'TimeoutError' || error.name === 'AbortError')) {
    return new JevError(`Jev request timed out after ${timeoutMs} ms`, 'timeout')
  }
  const statusCode = read(error, 'statusCode')
  const message = error instanceof Error ? error.message : String(error)
  return typeof statusCode === 'number'
    ? new JevError(message, 'gateway', statusCode)
    : new JevError(message, 'gateway')
}
