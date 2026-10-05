/**
 * Pure helpers for the API-key create form (`ApiKeysManager`).
 *
 * Scopes: a key with any dimension or folder assignment may hold only scope-aware
 * scopes. The form keeps the admin's selection as chosen and DERIVES what will be
 * sent, so toggling assignments on and off never silently loses a selection, and
 * the form can say which scopes are being left out.
 */

/** The scopes the key will actually be minted with. */
export function effectiveScopes(
  selected: ReadonlySet<string>,
  scoped: boolean,
  scopedKeyScopes: ReadonlySet<string>,
): Set<string> {
  return scoped ? new Set([...selected].filter((s) => scopedKeyScopes.has(s))) : new Set(selected);
}

/** Selected scopes a scoped key cannot hold (sorted), for the form's note. */
export function droppedScopes(
  selected: ReadonlySet<string>,
  scoped: boolean,
  scopedKeyScopes: ReadonlySet<string>,
): string[] {
  return scoped ? [...selected].filter((s) => !scopedKeyScopes.has(s)).sort() : [];
}

const UUID_RE = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;

/**
 * A mint error with the ids it names replaced by the folder paths / dimension
 * names the admin picked them by. Ids the form does not know stay as they are.
 */
export function describeMintError(message: string, namesById: ReadonlyMap<string, string>): string {
  return message.replace(UUID_RE, (id) => namesById.get(id.toLowerCase()) ?? id);
}
