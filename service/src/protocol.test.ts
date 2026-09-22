import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseRequest } from './protocol.ts'

const choose = {
  id: 'r1',
  type: 'choose',
  state: { game_state: 'OVERWORLD' },
  options: { 'walk:0:10:5:3': 'Go through the doorway' },
  instructions: 'pick one',
}

test('a well formed choose request round-trips', () => {
  const request = parseRequest(JSON.stringify(choose))
  assert.equal(request.type, 'choose')
  assert.equal(request.id, 'r1')
})

test('a ping needs nothing but an id', () => {
  assert.deepEqual(parseRequest('{"id":"p1","type":"ping"}'), { id: 'p1', type: 'ping' })
})

test('a planner request stays a planner request', () => {
  assert.equal(parseRequest(JSON.stringify({ ...choose, type: 'plan' })).type, 'plan')
})

test('a malformed request is rejected before it can reach the gateway', () => {
  const rejected = [
    '[]',
    '{"type":"choose"}',
    JSON.stringify({ ...choose, type: 'teleport' }),
    JSON.stringify({ ...choose, state: [1, 2] }),
    JSON.stringify({ ...choose, instructions: 7 }),
    JSON.stringify({ ...choose, options: { 'walk:0:10:5:3': 42 } }),
    JSON.stringify({ ...choose, timeoutMs: 'soon' }),
  ]
  for (const line of rejected) assert.throws(() => parseRequest(line), Error, line)
})

test('an absent timeout stays absent rather than becoming undefined', () => {
  assert.ok(!('timeoutMs' in parseRequest(JSON.stringify(choose))))
})
