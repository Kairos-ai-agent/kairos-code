/**
 * Feedback goes to the project's GitHub Issues, prefilled from what the user typed.
 *
 * Why a prefilled URL rather than a relay: no server, no credentials, and nothing is
 * sent until the user presses the button on GitHub — they see exactly what will be
 * public before it is. It also keeps working from networks where form relays and mail
 * APIs are unreachable (verified from a mainland-China connection: github.com answers,
 * the usual form/mail endpoints do not).
 *
 * The user's text is the whole payload. No version, no OS, no logs, no contact field:
 * they were asked not to be, and a feedback box that quietly ships telemetry is worse
 * than no feedback box.
 */
export const FEEDBACK_REPO = 'Kairos-ai-agent/kairos-code';

/** Longest issue title we will derive from the user's first line. */
const TITLE_LIMIT = 72;

/** The markdown template that carries the feedback framing (and the `feedback` label).
 *
 * Pointing at a template matters: this repo also ships *form* templates
 * (`bug_report.yml`), and GitHub ignores `body=` prefill for those — a plain
 * `issues/new?title=&body=` link would quietly lose what the user wrote. Markdown
 * templates do honour it.
 */
const FEEDBACK_TEMPLATE = 'feedback.md';

/** Build a prefilled "new issue" URL. A fork only needs to change FEEDBACK_REPO. */
export function buildFeedbackIssueUrl(text: string): string {
  const body = text.trim();
  const firstLine = body.split('\n').find((line) => line.trim())?.trim() ?? '';
  const title = firstLine.length > TITLE_LIMIT
    ? `${firstLine.slice(0, TITLE_LIMIT - 1)}…`
    : firstLine;
  const params = new URLSearchParams({
    template: FEEDBACK_TEMPLATE,
    title: title || 'Feedback',
    body,
  });
  return `https://github.com/${FEEDBACK_REPO}/issues/new?${params.toString()}`;
}

/** Open the prefilled issue in a new tab. Returns false when there is nothing to send. */
export function openFeedbackIssue(text: string): boolean {
  if (!text.trim()) return false;
  window.open(buildFeedbackIssueUrl(text), '_blank', 'noopener,noreferrer');
  return true;
}
