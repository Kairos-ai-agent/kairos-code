---
name: "feishu-doc-1.2.7"
description: "Fetch content from Feishu (Lark) Wiki, Docs, Sheets, and Bitable. Automatically resolves Wiki URLs to real entities and converts content to Markdown."
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\feishu-doc-1.2.7\\SKILL.md"
---
# Feishu Doc Skill

Fetch content from Feishu (Lark) Wiki, Docs, Sheets, and Bitable. Write and update documents.

## Prerequisites

- Install `feishu-common` first.
- This skill depends on `../feishu-common/index.js` for token and API auth.

## Capabilities

- **Read**: Fetch content from Docs, Sheets, Bitable, and Wiki.
- **Create**: Create new blank documents.
- **Write**: Overwrite document content with Markdown.
- **Append**: Append Markdown content to the end of a document.
- **Blocks**: List, get, update, and delete specific blocks.

## Long Document Handling (Unlimited Length)

To generate long documents (exceeding LLM output limits of ~2000-4000 tokens):
1. **Create** the document first to get a `doc_token`.
2. **Chunk** the content into logical sections (e.g., Introduction, Chapter 1, Chapter 2).
3. **Append** each chunk sequentially using `feishu_doc_append`.
4. Do NOT try to write the entire document in one `feishu_doc_write` call if it is very long; use the append loop pattern.

## Usage

```bash
# Read
node index.js --action read --token <doc_token>

# Create
node index.js --action create --title "My Doc"

# Write (Overwrite)
node index.js --action write --token <doc_token> --content "# Title\nHello world"

# Append
node index.js --action append --token <doc_token> --content "## Section 2\nMore text"
```

## Configuration

Create a `config.json` file in the root of the skill or set environment variables:

```json
{
  "app_id": "YOUR_APP_ID",
  "app_secret": "YOUR_APP_SECRET"
}
```

Environment variables:
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

## Browser Fallback — Read Wiki Content Without API Token

When the API path fails (missing `../feishu-common/index.js`, no `FEISHU_APP_ID`/`FEISHU_APP_SECRET`, or document not accessible to your app), use the browser tool to extract content directly. Wiki pages are SPA-rendered and lazy-loaded — naive extraction returns truncated text. Use this recipe:

### Step 1 — Resolve the anchor map
Wiki headings carry `<a href="#XXXXX">` anchors in the sidebar (`.catalogue__item-title`). Extract them once:
```js
Array.from(document.querySelectorAll('.catalogue__item-title'))
  .map(a => ({text: a.innerText.trim(), href: a.getAttribute('href')}))
```

### Step 2 — Navigate by hash anchor to force section rendering
The page only renders ~1 `docx-page-block` on initial load; the rest is virtualized. Jumping to a section's hash unlocks its content. Either:
- `browser_navigate(url + "#" + anchorId)` and wait ~1.5s
- Or click the sidebar link directly

### Step 3 — Extract from `.render-unit-wrapper`, NOT `textContent`
Both `textContent` and `innerText` return only the visible portion (e.g., 956 chars out of a 50K HTML block). The structured blocks live in `.render-unit-wrapper` divs:
```js
const blocks = document.querySelectorAll('.render-unit-wrapper');
const items = blocks.map((b, i) => ({
  i,
  text: b.textContent.replace(/\s+/g, ' ').trim()
}));
```
Returns ~47 blocks per section. Each block has a `[data-string="true"]` element for individual string segments if finer granularity is needed.

### Pitfalls
- **Don't trust `textContent` length** — it can stay at ~800 chars even when 50K of HTML exists in `.docx-page-block`. Always enumerate wrapper blocks.
- **`innerText` and `textContent` are not interchangeable here** — both ignore the data-string structure. Use wrapper enumeration.
- **Hash navigation is idempotent** — if the URL already has `#anchor`, navigating to the same anchor is a no-op. Use a different anchor or force a fresh `browser_navigate` with the full URL.
- **CORS blocks fetch from page context** — `fetch('/open-apis/wiki/v2/...')` returns "Failed to fetch" even with `credentials: 'include'`. Use the API path only when you have a token; otherwise stick to browser DOM extraction.
- **Tables render lazily** — `.docx-table-block` exists but only the first row may have populated text. Scroll through the section after navigating to fill more rows.

### When to use which
| Situation | Path |
|---|---|
| Have `FEISHU_APP_ID` + `FEISHU_APP_SECRET` | `node index.js --action read --token ...` |
| Token missing OR document not shared with app | Browser fallback (this section) |
| Need full Markdown export | API path — browser DOM is structured but needs custom conversion |
