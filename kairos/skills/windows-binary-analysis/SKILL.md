---
name: "windows-binary-analysis"
description: "Reverse-engineer Windows PE executables to understand business logic, membership/pricing systems, API endpoints, and service architecture. Covers Flutter/Dart AOT apps, .NET apps, and native PE binari"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/.archive/windows-binary-analysis/SKILL.md"
---
# Windows Binary Analysis

Use when analyzing a compiled Windows executable (.exe) without source code — understanding pricing models, API endpoints, membership validation, encryption, or service architecture.

## Step 1: Identify the Executable

```bash
file "/path/to/target.exe"
```

Look for:
- **PE32/PE32+**: Standard Windows executable
- **Flutter/Dart**: Look for `flutter_windows.dll` in the same directory → logic is in `data/app.so` (Dart AOT)
- **.NET**: Presence of mscoree.dll references or .NET assemblies
- **Native C++**: Standard PE with no managed runtime dependencies

## Step 2: Survey the Directory

```bash
ls -la "/path/to/install/dir/"
```

Key files to spot:
- `*.xml`, `*.json`, `*.config` — config files (read first)
- `flutter_windows.dll` — confirms Flutter/Dart app
- `data/app.so` — Dart AOT compiled binary (8-15MB)
- `*.dll` list — helps identify framework (Newtonsoft.Json = .NET, etc.)

Check `data/flutter_assets/` for asset files:
- `assets/initial_config.json` — often contains API domain lists
- `AssetManifest.json` — asset listing

## Step 3: Extract Strings from Binary

For Flutter/Dart apps, the business logic is in `data/app.so`:

```python
with open('app.so', 'rb') as f:
    data = f.read()
strings = []
current = []
for b in data:
    if 32 <= b < 127:
        current.append(chr(b))
    else:
        if len(current) >= 4:
            strings.append(''.join(current))
        current = []
if len(current) >= 4:
    strings.append(''.join(current))
```

For native PE, use `strings` command.

## Step 4: Keyword-Filtered Analysis

Search for API endpoints, pricing, membership, limits:

```python
keywords = ['premium', 'license', 'vip', 'free', 'trial', 'subscription',
            'price', 'plan', 'limit', 'bandwidth', 'api/', 'https://', 'http://',
            'coupon', 'redeem', 'checkout', 'payment', 'stripe',
            'month', 'year', 'expire', 'login', 'token', 'auth']
```

Also search for Dart package paths to reveal app structure.

## Step 5: API Endpoint Discovery

From extracted strings, build API map:
- Auth endpoints (`/login`, `/auth`, `/register`)
- Coupon/payment endpoints (`/coupon/check`, `/coupon/redeem`, `/checkout`)
- Config endpoints (`/api2/px/wu/...`)
- CDN mirrors for config distribution
- Server architecture (uibase, mainbase, tierbase separation)

## Step 6: Membership/Pricing Model

Look for these signals to reconstruct the tier system:

| Signal | Meaning |
|--------|---------|
| `Free Servers` / `Premium Servers` | Tier separation |
| `YOU HAVE REACHED YOUR DAILY TRAFFIC LIMIT` | Free tier cap |
| `user_premium` flag | Boolean premium status |
| `expires`, `expiration`, `expireat` | Expiry tracking |
| `Coupon Redeemed Successfully` | Coupon system |
| `days of premium access` | Duration-based pricing |
| `ProductModel` with fields | Product/pricing model |

## Step 7: Server-Side Validation Check

Determine if premium is client-side or server-side:
- Look for `isAuthenticated`, `refresh_token_required`, `token`
- Check if VPN tunnel auth requires server validation
- Look for encryption libraries (`pointycastle` = AES in Dart)
- Probe API endpoints (be aware of Cloudflare Anti-Bot)

## Pitfalls

- **Cloudflare Anti-Bot**: Many VPN apps protect APIs with fingerprint.js
- **No hardcoded prices**: Pricing fetched from server at runtime
- **Flutter AOT is compiled**: String extraction is the main technique
- **Rate limiting**: Look for `bs_consequtive_fails` counters
- **CAPTCHA**: Coupon flows usually have captcha protection
- **GFW**: Many foreign domains blocked from China — use alternate domains from config

## Verification Checklist

- [ ] Executable type identified (native/.NET/Flutter)
- [ ] Config files read
- [ ] Strings extracted and keyword-filtered
- [ ] API endpoints mapped
- [ ] Membership tier structure understood
- [ ] Server-side vs client-side validation determined
