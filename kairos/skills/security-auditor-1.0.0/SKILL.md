---
name: "security-auditor-1.0.0"
description: "Use when reviewing code for security vulnerabilities, implementing authentication flows, auditing OWASP Top 10, configuring CORS/CSP headers, handling secrets, input validation, SQL injection preventi"
priority: 0.5
version: "1.0.0"
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\security-auditor-1.0.0\\SKILL.md"
---
# Security Auditor

Comprehensive security audit and secure coding specialist. Adapted from buildwithclaude by Dave Poon (MIT).

## Role Definition

You are a senior application security engineer specializing in secure coding practices, vulnerability detection, and OWASP compliance. You conduct thorough security reviews and provide actionable fixes.

## Audit Process

1. **Conduct comprehensive security audit** of code and architecture
2. **Identify vulnerabilities** using OWASP Top 10 framework
3. **Design secure authentication and authorization** flows
4. **Implement input validation** and encryption mechanisms
5. **Create security tests** and monitoring strategies

## Core Principles

- Apply defense in depth with multiple security layers
- Follow principle of least privilege for all access controls
- Never trust user input — validate everything rigorously
- Design systems to fail securely without information leakage
- Conduct regular dependency scanning and updates
- Focus on practical fixes over theoretical security risks

---

## OWASP Top 10 Checklist

### 1. Broken Access Control (A01:2021)

```typescript
// ❌ BAD: No authorization check
app.delete('/api/posts/:id', async (req, res) => {
  await db.post.delete({ where: { id: req.params.id } })
  res.json({ success: true })
})

// ✅ GOOD: Verify ownership
app.delete('/api/posts/:id', authenticate, async (req, res) => {
  const post = await db.post.findUnique({ where: { id: req.params.id } })
  if (!post) return res.status(404).json({ error: 'Not found' })
  if (post.authorId !== req.user.id && req.user.role !== 'admin') {
    return res.status(403).json({ error: 'Forbidden' })
  }
  await db.post.delete({ where: { id: req.params.id } })
  res.json({ success: true })
})
```

**Checks:**
- [ ] Every endpoint verifies authentication
- [ ] Every data access verifies authorization (ownership or role)
- [ ] CORS configured with specific origins (not `*` in production)
- [ ] Directory listing disabled
- [ ] Rate limiting on sensitive endpoints
- [ ] JWT tokens validated on every request

### 2. Cryptographic Failures (A02:2021)

```typescript
// ❌ BAD: Storing plaintext passwords
await db.user.create({ data: { password: req.body.password } })

// ✅ GOOD: Bcrypt with sufficient rounds
import bcrypt from 'bcryptjs'
const hashedPassword = await bcrypt.hash(req.body.password, 12)
await db.user.create({ data: { password: hashedPassword } })
```

**Checks:**
- [ ] Passwords hashed with bcrypt (12+ rounds) or argon2
- [ ] Sensitive data encrypted at rest (AES-256)
- [ ] TLS/HTTPS enforced for all connections
- [ ] No secrets in source code or logs
- [ ] API keys rotated regularly
- [ ] Sensitive fields excluded from API responses

### 3. Injection (A03:2021)

```typescript
// ❌ BAD: SQL injection vulnerable
const query = `SELECT * FROM users WHERE email = '${email}'`

// ✅ GOOD: Parameterized queries
const user = await db.query('SELECT * FROM users WHERE email = $1', [email])

// ✅ GOOD: ORM with parameterized input
const user = await prisma.user.findUnique({ where: { email } })
```

```typescript
// ❌ BAD: Command injection
const result = exec(`ls ${userInput}`)

// ✅ GOOD: Use execFile with argument array
import { execFile } from 'child_process'
execFile('ls', [sanitizedPath], callback)
```

**Checks:**
- [ ] All database queries use parameterized statements or ORM
- [ ] No string concatenation in queries
- [ ] OS command execution uses argument arrays, not shell strings
- [ ] LDAP, XPath, and NoSQL injection prevented
- [ ] User input never used in `eval()`, `Function()`, or template literals for code

### 4. Cross-Site Scripting (XSS) (A07:2021)

```typescript
// ❌ BAD: dangerouslySetInnerHTML with user input
<div dangerouslySetInnerHTML={{ __html: userComment }} />

// ✅ GOOD: Sanitize HTML
import DOMPurify from 'isomorphic-dompurify'
<div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(userComment) }} />

// ✅ BEST: Render as text (React auto-escapes)
<div>{userComment}</div>
```

**Checks:**
- [ ] React auto-escaping relied upon (avoid `dangerouslySetInnerHTML`)
- [ ] If HTML rendering needed, sanitize with DOMPurify
- [ ] CSP headers configured (see below)
- [ ] HttpOnly cookies for session tokens
- [ ] URL parameters validated before rendering

### 5. Security Misconfiguration (A05:2021)

**Checks:**
- [ ] Default credentials changed
- [ ] Error messages don't leak stack traces in production
- [ ] Unnecessary HTTP methods disabled
- [ ] Security headers configured (see below)
- [ ] Debug mode disabled in production
- [ ] Dependencies up to date (`npm audit`)

---

## Security Headers

```typescript
// next.config.js
const securityHeaders = [
  { key: 'X-DNS-Prefetch-Control', value: 'on' },
  { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains; preload' },
  { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
  {
    key: 'Content-Security-Policy',
    value: [
      "default-src 'self'",
      "script-src 'self' 'unsafe-eval' 'unsafe-inline'",  // tighten in production
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: https:",
      "font-src 'self'",
      "connect-src 'self' https://api.example.com",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join('; '),
  },
]

module.exports = {
  async headers() {
    return [{ source: '/(.*)', headers: securityHeaders }]
  },
}
```

---

## Input Validation Patterns

### Zod Validation for API/Actions

```typescript
import { z } from 'zod'

const userSchema = z.object({
  email: z.string().email().max(255),
  password: z.string().min(8).max(128),
  name: z.string().min(1).max(100).regex(/^[a-zA-Z\s'-]+$/),
  age: z.number().int().min(13).max(150).optional(),
})

// Server Action
export async function createUser(formData: FormData) {
  'use server'
  const parsed = userSchema.safeParse({
    email: formData.get('email'),
    password: formData.get('password'),
    name: formData.get('name'),
  })

  if (!parsed.success) {
    return { error: parsed.error.flatten() }
  }

  // Safe to use parsed.data
}
```

### File Upload Validation

```typescript
const ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/webp']
const MAX_SIZE = 5 * 1024 * 1024 // 5MB

export async function uploadFile(formData: FormData) {
  'use server'
  const file = formData.get('file') as File

  if (!file || file.size === 0) return { error: 'No file' }
  if (!ALLOWED_TYPES.includes(file.type)) return { error: 'Invalid file type' }
  if (file.size > MAX_SIZE) return { error: 'File too large' }

  // Read and validate magic bytes, not just extension
  const bytes = new Uint8Array(await file.arrayBuffer())
  if (!validateMagicBytes(bytes, file.type)) return { error: 'File content mismatch' }
}
```

---

## Authentication Security

### JWT Best Practices

```typescript
import { SignJWT, jwtVerify } from 'jose'

const secret = new TextEncoder().encode(process.env.JWT_SECRET) // min 256-bit

export async function createToken(payload: { userId: string; role: string }) {
  return new SignJWT(payload)
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setExpirationTime('15m')  // Short-lived access tokens
    .setAudience('your-app')
    .setIssuer('your-app')
    .sign(secret)
}

export async function verifyToken(token: string) {
  try {
    const { payload } = await jwtVerify(token, secret, {
      algorithms: ['HS256'],
      audience: 'your-app',
      issuer: 'your-app',
    })
    return payload
  } catch {
    return null
  }
}
```

### Cookie Security

```typescript
cookies().set('session', token, {
  httpOnly: true,     // No JavaScript access
  secure: true,       // HTTPS only
  sameSite: 'lax',    // CSRF protection
  maxAge: 60 * 60 * 24 * 7,
  path: '/',
})
```

### Rate Limiting

```typescript
import { Ratelimit } from '@upstash/ratelimit'
import { Redis } from '@upstash/redis'

const ratelimit = new Ratelimit({
  redis: Redis.fromEnv(),
  limiter: Ratelimit.slidingWindow(10, '10 s'),
})

// In middleware or route handler
const ip = request.headers.get('x-forwarded-for') ?? '127.0.0.1'
const { success, remaining } = await ratelimit.limit(ip)
if (!success) {
  return NextResponse.json({ error: 'Too many requests' }, { status: 429 })
}
```

---

---

## Cloudflare Workers Security (Serverless Edge)

Workers have unique constraints — no Node.js crypto, no npm bcrypt/jose, and **every project file gets base64-bundled** into the Worker script.

### 1. Password Hashing (PBKDF2 via Web Crypto API)

bcrypt/argon2 are unavailable. Use `crypto.subtle.deriveBits` with PBKDF2:

```javascript
const encoder = new TextEncoder();

async function hashPassword(password) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const pwKey = await crypto.subtle.importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveBits']);
  const hashBuf = await crypto.subtle.deriveBits({ name: 'PBKDF2', salt, iterations: 100000, hash: 'SHA-256' }, pwKey, 256);
  return 'pbkdf2:' + btoa(String.fromCharCode(...salt)) + ':' + btoa(String.fromCharCode(...new Uint8Array(hashBuf)));
}

async function verifyPassword(password, stored) {
  const parts = stored.split(':');
  if (parts[0] !== 'pbkdf2') return false; // legacy fallback
  const salt = Uint8Array.from(atob(parts[1]), c => c.charCodeAt(0));
  const storedHash = atob(parts[2]);
  const pwKey = await crypto.subtle.importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveBits']);
  const hashBuf = await crypto.subtle.deriveBits({ name: 'PBKDF2', salt, iterations: 100000, hash: 'SHA-256' }, pwKey, 256);
  return storedHash === String.fromCharCode(...new Uint8Array(hashBuf));
}
```

**Legacy upgrade:** On login, detect plaintext passwords and upgrade them to PBKDF2 hash automatically.

### 2. JWT Token Signing (HMAC-SHA256 via Web Crypto API)

No `jose`/`jsonwebtoken` package — use `crypto.subtle.sign`:

```javascript
const encoder = new TextEncoder();

async function getSigningKey(env) {
  const secret = env.AUTH_SECRET || 'fallback-dev-secret';
  const keyData = encoder.encode(secret);
  return crypto.subtle.importKey('raw', keyData, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
}

function b64url(obj) {
  return btoa(JSON.stringify(obj)).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
}

async function createToken(payload, env) {
  const header = { alg: 'HS256', typ: 'JWT' };
  const content = b64url(header) + '.' + b64url(payload);
  const key = await getSigningKey(env);
  const sig = await crypto.subtle.sign('HMAC', key, encoder.encode(content));
  const sigB64 = btoa(String.fromCharCode(...new Uint8Array(sig))).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
  return content + '.' + sigB64;
}

async function verifyToken(token, env) {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const content = parts[0] + '.' + parts[1];
    const sig = Uint8Array.from(atob(parts[2].replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
    const key = await getSigningKey(env);
    if (!await crypto.subtle.verify('HMAC', key, sig, encoder.encode(content))) return null;
    return JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
  } catch { return null; }
}
```

**Critical:** Do NOT use bare `btoa()` — it's trivially forgeable (Bug #4 pattern).

### 3. Secrets MUST use Workers Secrets (env vars)

**Every file in the project folder gets base64-encoded into `worker_deploy.js`.**  
This means config files, `.token`, `.env`, `b2_config.json` are all exposed to anyone who can access the Worker script.

```javascript
// ❌ BAD — hardcoded OR read from bundled FILES object
const TOKEN = 'cfat_...';
const cfg = JSON.parse(decode(FILES['b2_config_json']));

// ✅ GOOD — read from Workers Secrets (set via Dashboard or wrangler secret put)
const TOKEN = env.CF_API_TOKEN;
const b2KeyId = env.B2_KEY_ID;
```

**Checks:**
- [ ] No tokens/keys in source files (use `os.environ.get()` in deploy scripts)
- [ ] Exclude sensitive files from bundle: add `.token`, `.token_hex`, `.env`, `b2_config.json` to `deploy_cf.py` exclude list
- [ ] B2/third-party creds also use `env.B2_*` not bundled JSON

### 4. Invite Codes / Admin Flags — Use Env Vars Too

```javascript
// ❌ BAD — hardcoded in source
const isAdmin = data.inviteCode === 'SDF-2026-MASTER-A7K9X2';

// ✅ GOOD — configurable via Workers Secrets
const masterCode = env.MASTER_INVITE_CODE || 'fallback';
const isAdmin = data.inviteCode === masterCode ? 1 : 0;
```

### 5. SQL Pagination (D1)

```sql
-- ❌ BAD — no limit, full table scan
SELECT * FROM scripts ORDER BY created_at DESC;

-- ✅ GOOD — paginated
SELECT * FROM scripts ORDER BY created_at DESC LIMIT ? OFFSET ?;
```

---

## Reference: Cloudflare Workers Hardening

See `references/cloudflare-workers-hardening.md` for concrete patterns from a real project audit:
- Deploy script consolidation (20+ scripts → 2)
- Credential cleanup (env vars + `_secrets.json` instead of hardcoding)
- B2 bucket `allPublic` detection and fix
- API endpoint auth checklist (claims privacy, script text leakage)
- JWT expiry enforcement
- XSS patterns from `dlUrl` injection
- Upload page dead code detection

### 6. Vote Dedup (Per-User)

Add a `script_votes` table and check before incrementing:

```sql
CREATE TABLE IF NOT EXISTS script_votes (
  id TEXT PRIMARY KEY,
  script_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  created_at INTEGER DEFAULT 0
);

-- Prevent duplicate votes
const existing = await DB.prepare(
  'SELECT id FROM script_votes WHERE script_id = ? AND member_id = ?'
).bind(scriptId, userId).first();
if (existing) return json({ error: 'already_voted' }, 409);
```

Credentials are NOT the only secrets — **any file in the project becomes part of the Worker source**. Audit the deploy script's exclude list.

## Environment & Secrets

```typescript
// ❌ BAD
const API_KEY = 'sk-1234567890abcdef'

// ✅ GOOD
const API_KEY = process.env.API_KEY
if (!API_KEY) throw new Error('API_KEY not configured')
```

**Rules:**
- Never commit `.env` files (only `.env.example` with placeholder values)
- Use different secrets per environment
- Rotate secrets regularly
- Use a secrets manager (Vault, AWS SSM, Doppler) for production
- Never log secrets or include them in error responses

---

## Dependency Security

```bash
# Regular audit
npm audit
npm audit fix

# Check for known vulnerabilities
npx better-npm-audit audit

# Keep dependencies updated
npx npm-check-updates -u
```

---

## Security Audit Report Format

When conducting a review, output findings as:

```
## Security Audit Report

### Critical (Must Fix)
1. **[A03:Injection]** SQL injection in `/api/search` — user input concatenated into query
   - File: `app/api/search/route.ts:15`
   - Fix: Use parameterized query
   - Risk: Full database compromise

### High (Should Fix)
1. **[A01:Access Control]** Missing auth check on DELETE endpoint
   - File: `app/api/posts/[id]/route.ts:42`
   - Fix: Add authentication middleware and ownership check

### Medium (Recommended)
1. **[A05:Misconfiguration]** Missing security headers
   - Fix: Add CSP, HSTS, X-Frame-Options headers

### Low (Consider)
1. **[A06:Vulnerable Components]** 3 packages with known vulnerabilities
   - Run: `npm audit fix`
```

---

## Protected File Patterns

These files should be reviewed carefully before any modification:

- `.env*` — environment secrets
- `auth.ts` / `auth.config.ts` — authentication configuration
- `middleware.ts` — route protection logic
- `**/api/auth/**` — auth endpoints
- `prisma/schema.prisma` — database schema (permissions, RLS)
- `next.config.*` — security headers, redirects
- `package.json` / `package-lock.json` — dependency changes
