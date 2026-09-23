import schema from '../../schema/actions.json' with { type: 'json' }

/**
 * The action vocabulary, read from the schema Python's executors are tested
 * against. Offering an ID whose kind is absent here means offering something
 * no executor can run, so the enumerator and the planner both go through this.
 */
export type ActionKind = keyof typeof schema.kinds

const kinds = new Set(Object.keys(schema.kinds))

/** The part of an action ID before its first colon. */
export function actionKind(id: string): string {
  const colon = id.indexOf(':')
  return colon === -1 ? id : id.slice(0, colon)
}

export function isKnownActionKind(kind: string): kind is ActionKind {
  return kinds.has(kind)
}

/** Kinds the decision service may offer; the rest are Python-only scripted steps. */
export const enumerableKinds: ActionKind[] = Object.entries(schema.kinds)
  .filter(([, entry]) => entry.enumerated)
  .map(([kind]) => kind as ActionKind)

export const schemaVersion = schema.version
