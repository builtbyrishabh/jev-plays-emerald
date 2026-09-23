import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  PLANNER_INSTRUCTIONS,
  PLANNER_MAX_OUTPUT_TOKENS,
  validatePlannerAdvice,
} from './planner.ts'

const options = { 'walk:0:9:14:8': "Enter May's House at (14, 8)" }

test('planner advice must name an offered destination', () => {
  const valid = validatePlannerAdvice(JSON.stringify({
    hint: 'Meet May upstairs.',
    destinationActionId: 'walk:0:9:14:8',
    location: "May's House entrance at (14, 8)",
    avoid: 'Do not retry Route 101.',
    successSignal: 'rival_house_state changes',
  }), false, options)

  assert.equal(valid.destinationActionId, 'walk:0:9:14:8')
  assert.throws(
    () => validatePlannerAdvice(JSON.stringify({
      ...valid,
      destinationActionId: 'walk:invented',
    }), false, options),
    /offered legal action/,
  )
})

test('planner advice requires bounded JSON fields', () => {
  const valid = {
    hint: 'Meet May upstairs.',
    destinationActionId: 'walk:0:9:14:8',
    location: "May's House entrance at (14, 8)",
    avoid: 'Do not retry Route 101.',
    successSignal: 'rival_house_state changes',
  }

  assert.throws(() => validatePlannerAdvice('not JSON', false, options), /valid JSON/)
  assert.throws(() => validatePlannerAdvice(JSON.stringify({ ...valid, hint: '' }), false, options), /hint/)
  assert.throws(() => validatePlannerAdvice(JSON.stringify({
    ...valid,
    avoid: Array.from({ length: 41 }, () => 'word').join(' '),
  }), false, options), /40 words/)
  assert.throws(() => validatePlannerAdvice(JSON.stringify(valid), true, options), /truncated/)
})

test('planner may use Emerald knowledge and gives a concrete recovery milestone', () => {
  assert.ok(PLANNER_MAX_OUTPUT_TOKENS >= 1024)
  assert.match(PLANNER_INSTRUCTIONS, /liveState as authority/i)
  assert.match(PLANNER_INSTRUCTIONS, /use (?:your own )?Emerald knowledge/i)
  assert.match(PLANNER_INSTRUCTIONS, /currentObjective/i)
  assert.match(PLANNER_INSTRUCTIONS, /rivalName/i)
  assert.match(PLANNER_INSTRUCTIONS, /Do not substitute\s+a different person/i)
  assert.match(PLANNER_INSTRUCTIONS, /immediate reachable milestone/i)
  assert.match(PLANNER_INSTRUCTIONS, /places, people, or objects by name/i)
  assert.doesNotMatch(PLANNER_INSTRUCTIONS, /walkthroughKnowledge/i)
  assert.match(PLANNER_INSTRUCTIONS, /Return JSON only/i)
})
