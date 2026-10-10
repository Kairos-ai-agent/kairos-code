/**
 * Which project does a message-bus activity belong to?
 *
 * The chat UI shows exactly ONE project's thread at a time, but the backend's
 * WebSocket fans every project's activity out to every connected client. The
 * Chat page therefore has to decide, per event, whether it belongs to the
 * project that is currently open — otherwise another project's reply renders
 * into the thread on screen (the "回答串到不同的聊天界面" bug) and is even
 * snapshotted into that thread on the next project switch.
 *
 * The project id has two authoritative sources on the wire:
 *   1. ``metadata.project_id`` — set on skeleton / generic-lane messages.
 *   2. the ``sender``'s dot-prefix — every lane's sender is
 *      ``"<project_id>.coder"`` or ``"<project_id>.skeleton"``.
 *
 * Metadata wins; the sender prefix is the fallback. An empty return value means
 * the event carries no project information at all — e.g. the local short-form
 * senders ``"coder"`` / ``"orchestrator"`` / ``"user"`` — which the caller must
 * treat as "cannot determine" and let through rather than guess.
 */
export function projectIdOfMessage(
  msg: { sender?: unknown; metadata?: Record<string, unknown> | null } | null | undefined,
): string {
  if (!msg) return '';
  const raw = msg.metadata
    ? (msg.metadata as Record<string, unknown>).project_id
    : undefined;
  if (raw !== undefined && raw !== null) {
    const s = String(raw).trim();
    if (s) return s;
  }
  const sender = String(msg.sender ?? '');
  const dot = sender.indexOf('.');
  if (dot > 0) return sender.slice(0, dot);
  return '';
}

/**
 * True only when ``msg`` provably belongs to a project other than
 * ``activeProjectId``.
 *
 * A message with no project information (``projectIdOfMessage`` → ``''``) is
 * never foreign: we cannot place it, so the existing behaviour is preserved and
 * it flows into the open thread. A message whose resolved project differs from
 * the open one — including the case where no project is open at all — is
 * foreign and must be dropped before any append / stream / status mutation.
 */
export function isForeignProjectMessage(
  msg: { sender?: unknown; metadata?: Record<string, unknown> | null } | null | undefined,
  activeProjectId: string | null | undefined,
): boolean {
  const pid = projectIdOfMessage(msg);
  if (!pid) return false;                    // cannot determine → not foreign
  return pid !== (activeProjectId || '');    // '' active ≠ pid → foreign
}
