import assert from 'node:assert/strict'
import { test } from 'node:test'
import { actionKind, enumerableKinds, isKnownActionKind } from './actions.ts'

test('an action ID resolves to the kind before its first colon', () => {
  assert.equal(actionKind('walk:0:10:5:3'), 'walk')
  assert.equal(actionKind('battle-run'), 'battle-run')
  assert.equal(actionKind('battle-item:Super Potion'), 'battle-item')
})

test('kinds outside the schema are rejected', () => {
  assert.ok(isKnownActionKind('walk'))
  assert.ok(!isKnownActionKind('fly'))
})

test('every enumerable kind is itself a known kind', () => {
  for (const kind of enumerableKinds) assert.ok(isKnownActionKind(kind))
  assert.ok(enumerableKinds.includes('walk'))
  // Character creation runs before the player has control; never offered.
  assert.ok(!enumerableKinds.includes('setup'))
})
