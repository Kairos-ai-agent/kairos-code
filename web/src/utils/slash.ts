/**
 * Slash-command matching for the composer.
 *
 * The commands themselves belong to the backend (`GET /borrowed/{id}/slash`) —
 * this file only decides what to offer while the user types: which token the
 * caret sits in, which commands start with it, and whether a finished message
 * is a command at all. Kept pure so the composer's keyboard handling stays
 * thin and this stays testable.
 */
export interface SlashCommand {
  name: string;
  help?: string;
}

export interface SlashToken {
  /** What the user has typed after the slash (may be empty). */
  query: string;
  /** Index of the slash itself. */
  start: number;
  /** Index just past the token, for replacing it. */
  end: number;
}

/**
 * The `/word` token at the caret, when there is one.
 *
 * Only at the start of the input or after whitespace, so a URL being typed
 * (`https://…`) never opens the list.
 */
export function slashTokenAt(text: string, caret: number): SlashToken | null {
  const before = text.slice(0, caret);
  const m = /(^|\s)\/(\w*)$/.exec(before);
  if (!m) return null;
  const query = m[2];
  return { query, start: caret - query.length - 1, end: caret };
}

/** Commands whose name starts with `query`, case-insensitively. */
export function matchSlashCommands(
  commands: SlashCommand[],
  query: string,
  limit = 8,
): SlashCommand[] {
  const q = query.toLowerCase();
  return commands
    .filter((c) => c.name.toLowerCase().startsWith(q))
    .slice(0, limit);
}

/**
 * Is this message a command the backend will actually run?
 *
 * Only an exact command name counts, so a path like `/etc/hosts` is left alone
 * and sent as an ordinary message.
 */
export function knownSlashCommand(
  text: string,
  commands: SlashCommand[],
): { name: string; arg: string } | null {
  const m = /^\/(\w+)(?:\s+([\s\S]*))?$/.exec(text.trim());
  if (!m) return null;
  const name = m[1].toLowerCase();
  if (!commands.some((c) => c.name.toLowerCase() === name)) return null;
  return { name, arg: (m[2] || '').trim() };
}
