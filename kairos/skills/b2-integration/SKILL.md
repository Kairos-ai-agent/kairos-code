---
name: "b2-integration"
description: "Backblaze B2 API v3 integration patterns for Cloudflare Workers. Two-step auth, file upload with SHA1, dynamic download URLs, and env-var-based configuration."
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/devops/b2-integration/SKILL.md"
---
## When to Use

Any task involving Backblaze B2 object storage API v3, especially from Cloudflare Workers. Covers auth, upload, download, and environment variable configuration.

## B2 API v3 Two-Step Authentication

B2 v3 requires a two-step auth flow — sending Basic auth directly to resource endpoints (upload URL, download auth) returns 401.

```
Step 1: POST b2_authorize_account  (Basic auth)  →  get authorizationToken
Step 2: POST b2_get_upload_url     (token auth)  →  get uploadUrl
```

### Implementation (Cloudflare Worker)

```javascript
let _b2AuthToken = null;
let _b2ApiUrl = null;
let _b2DlUrl = null;

async function b2Auth() {
  if (_b2AuthToken) return { authToken: _b2AuthToken, apiUrl: _b2ApiUrl, downloadUrl: _b2DlUrl };
  const keyId = env.B2_KEY_ID || '';
  const appKey = env.B2_APP_KEY || '';
  if (!keyId || !appKey) throw new Error('B2 credentials not configured');
  const basicCred = btoa(keyId + ':' + appKey);
  const r = await fetch('https://api.backblazeb2.com/b2api/v3/b2_authorize_account', {
    headers: { 'Authorization': 'Basic ' + basicCred }
  });
  const data = await r.json();
  if (!data.authorizationToken) throw new Error('B2 auth failed: ' + (data.code || 'unknown'));
  _b2AuthToken = data.authorizationToken;
  _b2ApiUrl = data.apiInfo.storageApi.apiUrl;
  _b2DlUrl = data.apiInfo.storageApi.downloadUrl;
  return { authToken: _b2AuthToken, apiUrl: _b2ApiUrl, downloadUrl: _b2DlUrl };
}
```

Cache `_b2AuthToken`, `_b2ApiUrl`, `_b2DlUrl` per-request (lifetime of one Worker invocation) to avoid re-authenticating on every B2 call.

## File Upload (SHA1 Requirement)

B2 v3 requires the `X-Bz-Content-Sha1` header on file uploads. Missing this header returns `400 bad_request "Missing header: X-Bz-Content-Sha1"`.

### Frontend (Browser)

```javascript
const buf = await blob.arrayBuffer();
const sha1Buf = await crypto.subtle.digest('SHA-1', buf);
const sha1Hex = Array.from(new Uint8Array(sha1Buf))
  .map(b => b.toString(16).padStart(2, '0')).join('');

const res = await fetch(uploadUrl, {
  method: 'POST',
  headers: {
    'Authorization': authToken,
    'Content-Type': fileType,
    'X-Bz-File-Name': fileName,
    'X-Bz-Content-Sha1': sha1Hex
  },
  body: blob
});
```

`crypto.subtle.digest()` expects an `ArrayBuffer`, not a `Blob`. Always call `blob.arrayBuffer()` first.

## Download URL (Dynamic, Not Hardcoded)

The download hostname (e.g. `f006.backblazeb2.com`) is returned by `b2_authorize_account` as `data.apiInfo.storageApi.downloadUrl`. **Do not hardcode** `f002` or any specific host — it can change.

```javascript
async function b2GetDownloadUrl(fileName, validDurationSec) {
  const { downloadUrl } = await b2Auth();
  const bucketName = env.B2_BUCKET_NAME || 'default-bucket';
  const dlAuth = await fetch(apiUrl + '/b2api/v3/b2_get_download_authorization', {
    method: 'POST',
    headers: { 'Authorization': authToken, 'Content-Type': 'application/json' },
    body: JSON.stringify({ bucketId, fileNamePrefix: fileName, validDurationInSeconds: validDurationSec || 3600 })
  });
  const result = await dlAuth.json();
  if (!result.authorizationToken) throw new Error('download_auth_failed');
  return downloadUrl + '/file/' + bucketName + '/' + fileName + '?Authorization=' + result.authorizationToken;
}
```

## Environment Variables for Cloudflare Workers

Set B2 credentials as Workers Secrets (plain_text bindings) rather than bundling them into the script:

| Variable | Example Value |
|----------|---------------|
| `B2_KEY_ID` | `2d97f2ad02ac` |
| `B2_APP_KEY` | `006e02...` |
| `B2_BUCKET_ID` | `928da917...` |
| `B2_BUCKET_NAME` | `my-bucket-name` |

Deploy with env vars via multipart metadata:
```json
{
  "main_module": "worker_deploy.js",
  "bindings": [
    {"type": "d1", "name": "DB", "id": "..."},
    {"type": "plain_text", "name": "B2_KEY_ID", "text": "..."},
    {"type": "plain_text", "name": "B2_APP_KEY", "text": "..."}
  ]
}
```

## Common Pitfalls

- **Basic auth on upload URL endpoint** — B2 v3 rejects `Authorization: Basic` on `b2_get_upload_url`. Must use the token from `b2_authorize_account`.
- **Missing SHA1 header** — Easy to forget. Always compute and send `X-Bz-Content-Sha1`.
- **Hardcoded download host** — `f002.backblazeb2.com` is not guaranteed. Use the dynamic `downloadUrl` from auth response.
- **Blob vs ArrayBuffer** — `crypto.subtle.digest` needs `ArrayBuffer`. Call `blob.arrayBuffer()` first.
- **Token caching** — Cache token per Worker request to avoid calling `b2_authorize_account` on every B2 operation.
