import assert from 'node:assert/strict'
import { test } from 'node:test'
import { JevError, choose } from './jev.ts'

const state = { game_state: 'OVERWORLD' }

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
