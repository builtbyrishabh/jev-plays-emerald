import assert from 'node:assert/strict'
import { test } from 'node:test'
import { InvalidResponseDataError } from 'ai'
import { JevError, choose, asJevError } from './jev.ts'

const state = { game_state: 'OVERWORLD' }

test('malformed provider answers remain distinct from invalid requests', () => {
  const error = new InvalidResponseDataError({
    message: 'Question "action" did not select a highest-probability option.',
    data: { action: { choice: 'talk:1', probabilities: { 'talk:1': 0.1, 'talk:2': 0.9 } } },
  })
  assert.equal(asJevError(error, 1000).kind, 'invalid-response')
  assert.equal(asJevError(new JevError('bad menu', 'invalid'), 1000).kind, 'invalid')
})

test('an empty menu is refused rather than sent', async () => {
  await assert.rejects(
    choose({ state, options: {}, instructions: 'pick one' }),
    (error: unknown) => error instanceof JevError && error.kind === 'invalid',
  )
})

test('an action no executor can run never reaches the gateway', async () => {
  await assert.rejects(
    choose({ state, options: { 'fly:0:10': 'Fly to Oldale' }, instructions: 'pick one' }),
    (error: unknown) => error instanceof JevError && /no executor/.test(error.message),
  )
})
