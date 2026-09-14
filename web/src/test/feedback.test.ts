import { describe, expect, it } from 'vitest';

import { buildFeedbackIssueUrl, FEEDBACK_REPO, openFeedbackIssue } from '../utils/feedback';

/**
 * Feedback must (a) land in the project's issue tracker pre-filled, and (b) carry the
 * user's text and *nothing else* — no version, no OS, no logs, no contact details. The
 * privacy part is a feature: the user was told the box sends only what they write.
 */
describe('feedback issue URL', () => {
  it('prefills the project issue form', () => {
    const url = new URL(buildFeedbackIssueUrl('the drawer loses my settings'));
    expect(url.origin + url.pathname)
      .toBe(`https://github.com/${FEEDBACK_REPO}/issues/new`);
    expect(url.searchParams.get('body')).toBe('the drawer loses my settings');
  });

  it('derives the title from the first line of the text', () => {
    const url = new URL(buildFeedbackIssueUrl('\n\n  Crash on start  \nmore detail here'));
    expect(url.searchParams.get('title')).toBe('Crash on start');
    // The body still holds everything the user wrote, verbatim.
    expect(url.searchParams.get('body')).toContain('more detail here');
  });

  it('truncates a very long first line for the title only', () => {
    const long = 'x'.repeat(300);
    const url = new URL(buildFeedbackIssueUrl(long));
    const title = url.searchParams.get('title') ?? '';
    expect(title.length).toBeLessThanOrEqual(72);
    expect(url.searchParams.get('body')).toBe(long);
  });

  it('points at the markdown template, so the prefill survives the template chooser', () => {
    const url = new URL(buildFeedbackIssueUrl('hi'));
    // Form templates (*.yml) make GitHub ignore `body=`; a markdown template does not.
    expect(url.searchParams.get('template')).toBe('feedback.md');
  });

  it('sends nothing but the text the user typed', () => {
    const url = new URL(buildFeedbackIssueUrl('just my words'));
    expect([...url.searchParams.keys()].sort()).toEqual(['body', 'template', 'title']);
    // no version / OS / log / contact metadata anywhere in the payload
    const payload = decodeURIComponent(url.search).toLowerCase();
    for (const leak of ['version', 'platform', 'useragent', 'email', 'log']) {
      expect(payload).not.toContain(leak);
    }
  });

  it('refuses to open anything for empty input', () => {
    expect(openFeedbackIssue('   ')).toBe(false);
    expect(openFeedbackIssue('')).toBe(false);
  });
});
