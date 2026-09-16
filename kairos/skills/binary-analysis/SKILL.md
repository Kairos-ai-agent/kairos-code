---
name: "binary-analysis"
description: "Techniques for analyzing compiled Windows/Linux binaries to understand their internal logic, API structure, subscription/premium models, encryption, and authentication flow. Covers PE/ELF identificati"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\software-development\\firmware-reverse-engineering\\references\\binary-analysis\\SKILL.md"
---
# Binary Analysis

Use when the user asks to analyze, reverse engineer, or understand the internals of a compiled binary (.exe, .so, .dll, .dylib) — especially for understanding premium/subscription models, API structure, and authentication logic in desktop applications.

## Step 1: Identify the Binary

```bash
file <path/to/binary>
```

| Output | Meaning |
|--------|---------|
| `PE32+ executable for MS Windows (GUI)` | 64-bit Windows GUI app |
| `PE32 executable` | 32-bit Windows |
| `ELF 64-bit` | Linux |
| `Mach-O` | macOS |

## Step 2: Identify Framework & Architecture

List the installation directory to find framework indicators:

| File present | Framework | Business logic location |
|---|---|---|
| `flutter_windows.dll` | Flutter/Dart | `data/app.so` (AOT-compiled Dart) |
| `Newtonsoft.Json.dll` | .NET | Main .exe (decompile with dnSpy/ILSpy) |
| `python*.dll` | Python | `.py` files or PyInstaller bundle |
| No framework DLLs | Native C++ | Main .exe (IDA/Ghidra) |

For Flutter apps, also check `data/flutter_assets/` for:
- `AssetManifest.json` — all bundled assets
- `assets/` directory — config files, images
- `initial_config.json` — API URLs, server lists, update intervals

## Step 3: Extract Readable Strings

### Using `strings` (preferred):
```bash
strings -a <path/to/binary> | head -500
```

### Python fallback (when `strings` not available):
```python
with open('binary', 'rb') as f:
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

## Step 4: Filter for Targeted Business Logic

Use keyword filtering to find relevant strings. Keywords by category:

### Premium/Subscription
premium, vip, pro, license, free, trial, member, subscription, price, plan, upgrade, downgrade, basic, standard, plus, gold, silver, tier

### API Endpoints
http://, https://, /api, /auth, /login, /logout, /register, /checkout, /payment, /connect, /disconnect, /ping, /config, /coupon, /product

### User/Auth
token, login, register, auth, session, cookie, jwt, refresh_token, expire, expires, authenticate, authorization, secret

### Limits & Features
limit, traffic, bandwidth, daily, monthly, quota, max_devices, speed, server_limit, ad_free, no_ads

### Payment
stripe, paypal, payment, currency, checkout, purchase, buy, redeem, coupon, discount

### Encryption
encrypt, aes, rsa, key, cipher, decrypt, hash, sha, hmac, pointycastle

## Step 5: Examine Config Files

Check for companion config files in the installation directory:

- `*.xml`, `*.json`, `*.yaml`, `*.ini`, `*.cfg`, `*.conf`
- `initial_config.json` often contains server URLs, update URLs, CDN mirrors
- For Flutter: check `data/flutter_assets/assets/` for bundled configs
- For .NET: check `*.config` or `appsettings.json`

Look for:
- Multiple server tiers (uibase, mainbase, tierbase pattern)
- Update/mirror URLs (sometimes accessible without auth)
- Timeout/default settings

## Step 6: Reconstruct API Structure

From discovered endpoints, build an API map:

| Category | Example endpoints |
|----------|------------------|
| Auth | `/login`, `/auth`, `/logout`, `/register`, `/refresh` |
| Business | `/products`, `/coupon/check`, `/coupon/redeem`, `/checkout`, `/payment` |
| Config | `/config`, `/policy/eula`, `/update`, `/version` |
| Service | `/connect`, `/disconnect`, `/ping`, `/status` |

Check for patterns like:
- `api3/` (versioned API)
- `uibase` (UI/auth servers)
- `mainbase` (business logic servers)
- `tierbase` (pricing/tier servers)

## Step 7: Analyze the Subscription Model

From extracted strings, identify:

1. **Available tiers**: Free, Basic, Premium, Pro, VIP
2. **Feature differentiation**: servers, speed, traffic caps, device limits, ads
3. **Payment methods**: Stripe, PayPal, coupons/redemption codes
4. **Auth flow**: email/password, token-based, OAuth, guest accounts
5. **Expiry model**: fixed dates, days of access, recurring subscription
6. **Limits enforced**: daily traffic, server count, connection speed, concurrent devices

## Pitfalls

- **Pricing is NOT in the binary** — Modern apps fetch pricing from server at runtime. Don't expect hardcoded price constants.
- **Server-side validation dominates** — Modifying the client binary won't bypass server-side auth. The VPN tunnel/service itself is server-authenticated.
- **Flutter app.so is NOT standard ELF** — It's AOT-compiled Dart bytecode. Standard disassemblers (IDA, Ghidra) produce poor results. String extraction + API tracing is the primary approach.
- **Encrypted/obfuscated strings** — Some apps XOR/Base64-encode strings. Look for decode routines near string references.
- **Multiple API layers** — Apps may use separate server sets for UI rendering (uibase), business data (mainbase), and pricing/tiers (tierbase), each with different domains.
- **Captcha protection** — Sensitive operations (coupon redemption, login) may have Captcha challenges embedded in the API flow.

## References

- `references/hoxx-vpn-example.md` — Full analysis transcript of HoxxVPN.exe as a concrete example of the workflow above.
