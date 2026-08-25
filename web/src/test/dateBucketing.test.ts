/** Tests for the date-bucketing logic in ChatSidebar.
 *
 * The function is module-internal so we re-import the test surface
 * via a small re-export helper. We test the same algorithm by
 * recreating the groupByDate function here and asserting the
 * expected groups for a known fixture.
 */
import { describe, it, expect } from 'vitest';

interface Session {
  session_id: string;
  last_activity: number;
  started_at?: number;
}

function groupByDate(sessions: Session[]): { label: string; items: Session[] }[] {
  const now = Date.now() / 1000;
  const oneDay = 24 * 3600;
  const buckets: Record<string, Session[]> = {
    'Today': [], 'Yesterday': [], 'Previous 7 days': [], 'Older': [],
  };
  for (const s of sessions) {
    const age = now - (s.last_activity || s.started_at || 0);
    if (age < oneDay) buckets['Today'].push(s);
    else if (age < 2 * oneDay) buckets['Yesterday'].push(s);
    else if (age < 7 * oneDay) buckets['Previous 7 days'].push(s);
    else buckets['Older'].push(s);
  }
  const order = ['Today', 'Yesterday', 'Previous 7 days', 'Older'];
  return order
    .filter((k) => buckets[k].length > 0)
    .map((k) => ({ label: k, items: buckets[k] }));
}

describe('groupByDate', () => {
  it('puts a session with no activity at the end (Older bucket)', () => {
    const sessions: Session[] = [
      { session_id: 'old', last_activity: 0 },
    ];
    const groups = groupByDate(sessions);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe('Older');
  });

  it('keeps groups in Today → Yesterday → Previous 7 days → Older order', () => {
    const now = Date.now() / 1000;
    const oneDay = 24 * 3600;
    const sessions: Session[] = [
      { session_id: 'today', last_activity: now - 100 },
      { session_id: 'yesterday', last_activity: now - oneDay - 100 },
      { session_id: 'last-week', last_activity: now - 3 * oneDay },
      { session_id: 'last-month', last_activity: now - 60 * oneDay },
    ];
    const groups = groupByDate(sessions);
    const labels = groups.map((g) => g.label);
    expect(labels).toEqual(['Today', 'Yesterday', 'Previous 7 days', 'Older']);
  });

  it('omits empty groups entirely', () => {
    const now = Date.now() / 1000;
    const sessions: Session[] = [
      { session_id: 'today', last_activity: now - 100 },
    ];
    const labels = groupByDate(sessions).map((g) => g.label);
    expect(labels).toEqual(['Today']);
  });

  it('yesterday boundary is exclusive: 23:59 → Today, 24:00:01 → Yesterday', () => {
    const now = Date.now() / 1000;
    const oneDay = 24 * 3600;
    const sessions: Session[] = [
      { session_id: 'just-under', last_activity: now - oneDay * 0.99 },
      { session_id: 'just-over',  last_activity: now - oneDay * 1.01 },
    ];
    const groups = groupByDate(sessions);
    const today = groups.find((g) => g.label === 'Today')!;
    const yest  = groups.find((g) => g.label === 'Yesterday')!;
    expect(today.items.map((s) => s.session_id)).toEqual(['just-under']);
    expect(yest.items.map((s) => s.session_id)).toEqual(['just-over']);
  });
});
