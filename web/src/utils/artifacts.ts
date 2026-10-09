/**
 * artifacts — the files an agent turn produced, as the backend hands them over.
 *
 * Frozen shape (one element):
 *   { path: string, name: string, size: number, mime: string }
 *
 * It arrives from two places, and both go through this module:
 *   - the `POST /projects/{id}/chat` response body (`artifacts`, always
 *     present — an empty array when the turn produced nothing);
 *   - the assistant reply message's `metadata.artifacts`, which is what a
 *     refresh restores from `GET /projects/{id}/chat-messages?chat_only=true`.
 *
 * The second source is historical data out of the DB, written by whatever
 * backend build was live at the time. So everything here is deliberately
 * tolerant: a missing, non-array or half-shaped `artifacts` value degrades to
 * "no cards" — it must never throw, and it must never put a broken object on
 * screen.
 */

/** One produced file, exactly the four fields the backend guarantees. */
export interface ChatArtifact {
  /** Path relative to the project root — what the download endpoint takes. */
  path: string;
  /** File name shown on the card. */
  name: string;
  /** Size in bytes. */
  size: number;
  /** MIME type, used only to decide whether a preview is offered. */
  mime: string;
}

const asText = (v: unknown): string => (typeof v === 'string' ? v : '');

/** Last path segment — the fallback name when the backend sent a blank one. */
function baseName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : path;
}

/**
 * Coerce whatever arrived into a list of usable artifacts.
 *
 * Drops (silently): a non-array, a non-object entry, an entry with no usable
 * `path` (the one field the download endpoint cannot work without). A missing
 * or non-numeric `size` becomes 0 and a missing `mime` becomes '' — a card
 * without a size is still a card, and neither is worth an exception.
 */
export function normalizeArtifacts(raw: unknown): ChatArtifact[] {
  if (!Array.isArray(raw)) return [];
  const out: ChatArtifact[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const rec = item as Record<string, unknown>;
    const path = asText(rec.path).trim();
    if (!path) continue;
    const sizeRaw = rec.size;
    const size = typeof sizeRaw === 'number' && Number.isFinite(sizeRaw) && sizeRaw >= 0
      ? sizeRaw
      : 0;
    out.push({
      path,
      name: asText(rec.name).trim() || baseName(path),
      size,
      mime: asText(rec.mime).trim(),
    });
  }
  return out;
}

/**
 * The frozen download endpoint.
 *
 * Both halves are URL-encoded: the project id goes in a path segment, and the
 * *whole* relative path is the query value (`artifacts/download?path=…`), so a
 * path with slashes, spaces, `#` or non-ASCII characters has to survive intact.
 */
export function artifactDownloadUrl(projectId: string, path: string): string {
  return `/api/projects/${encodeURIComponent(projectId)}/artifacts/download`
    + `?path=${encodeURIComponent(path)}`;
}

/** Extensions we will try to preview even when the server sent no mime. */
const PREVIEW_EXT = /\.(md|markdown)$/i;

/**
 * Whether a preview is worth offering: the mime says text, or it says markdown,
 * or (a server that left mime blank) the file is a markdown document. Anything
 * else — images, archives, binaries — is download-only, because rendering it as
 * text would be a lie.
 */
export function isPreviewable(artifact: ChatArtifact): boolean {
  const mime = (artifact.mime || '').toLowerCase();
  if (mime.startsWith('text/')) return true;
  if (mime.includes('markdown')) return true;
  return PREVIEW_EXT.test(artifact.path);
}
