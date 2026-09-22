import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  PLANNER_INSTRUCTIONS,
  PLANNER_MAX_OUTPUT_TOKENS,
  validatePlannerAdvice,
} from './planner.ts'

test('planner advice is one bounded recovery nudge', () => {
  assert.equal(validatePlannerAdvice('Try the other doorway.', false), 'Try the other doorway.')
  assert.throws(
    () => validatePlannerAdvice(Array.from({ length: 41 }, () => 'word').join(' '), false),
    /40 words/,
  )
  assert.throws(() => validatePlannerAdvice('unfinished', true), /truncated/)
})

test('planner has room to reason and must correct advice contradicted by evidence', () => {
  assert.ok(PLANNER_MAX_OUTPUT_TOKENS >= 1024)
  assert.match(PLANNER_INSTRUCTIONS, /recent dialogue as authoritative evidence/i)
  assert.match(PLANNER_INSTRUCTIONS, /retract it/i)
})
