/**
 * Keep the previous value when a polled payload has not actually changed.
 *
 * The chat page polls pending plan/ask state every two seconds and handed React
 * a freshly parsed object each time, so the whole page — every turn, every
 * piece of rendered markdown in the thread — re-rendered twice a second for a
 * payload that was identical. Returning `prev` keeps the object identity, and
 * React skips the update.
 *
 * Comparison is by JSON text: these payloads are small, flat and come straight
 * from the API, so it is exact for their shape and costs nothing worth
 * measuring. Anything that cannot be stringified counts as changed.
 */
export function keepIfSame<T>(prev: T, next: T): T {
  if (prev === next) return prev;
  if (prev == null || next == null) return next;
  try {
    return JSON.stringify(prev) === JSON.stringify(next) ? prev : next;
  } catch {
    return next;
  }
}
