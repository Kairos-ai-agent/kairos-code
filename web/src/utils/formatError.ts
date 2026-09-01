/**
 * formatError — single source of truth for axios error → user
 * message. Used by ALL catch blocks so the user always sees
 * the real upstream error (status + body) instead of
 * axios's default "Request failed with status code N".
 *
 * R38.6.3: handles 3 cases
 *   1. JSON response with {detail, error_id, message}
 *   2. Non-JSON / empty body (HTML 502 page, gateway timeout)
 *   3. No response (CORS preflight fail, network down)
 *
 * Usage:
 *   catch (e: any) { setErr(formatError(e, 'load failed')); }
 */
export function formatError(e: any, fallback: string = 'request failed'): string {
  const status = e?.response?.status;
  const resp = e?.response;
  let detail = '';

  // Case 1: response body is a JSON object
  if (resp?.data && typeof resp.data === 'object') {
    detail = resp.data.detail
          || resp.data.error_id
          || resp.data.message
          || JSON.stringify(resp.data).slice(0, 200);
  }
  // Case 2: response body is a string (HTML, plain text, empty)
  else if (typeof resp?.data === 'string' && resp.data.length > 0) {
    detail = resp.data.slice(0, 300);
  }

  // Announce non-JSON upstream (CDN / gateway) clearly
  const ct = String(resp?.headers?.['content-type'] || '');
  if (ct && !ct.includes('json') && detail.length > 0 && status && status >= 500) {
    detail = `[upstream returned ${ct.split(';')[0]}, not JSON] ${detail}`;
  }

  if (!detail) detail = e?.message || fallback;

  return status ? `[HTTP ${status}] ${detail}` : detail;
}
