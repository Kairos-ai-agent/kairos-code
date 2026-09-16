---
name: "html-js-translation"
description: "Translate HTML/CSS/JS web apps to Chinese (or any language) without breaking code. Covers bulk find-replace pitfalls, identifier protection, and safe localization patterns. Use when: localizing a web"
priority: 0.5
version: "1.0.0"
imported-from: "agents"
source-path: "C:\\Users\\you\\.agents\\skills\\software-development\\html-js-translation\\SKILL.md"
---
# HTML/JS Translation — Safe Localization Patterns (Consolidated)

This is the unified skill for all web UI localization work. It combines:
- **Safe translation patterns** (from html-js-translation) — core rules, verification scripts
- **i18n code translation** (from i18n-code-translation) — identifier protection, recovery from broken translations
- **Web UI localization** (from web-ui-localization) — translation file patterns, quick-fix regex

## Core Rule

**NEVER translate inside `<script>` tags, `<style>` tags, or CSS class names.** Only translate user-visible text in HTML elements.

## The Danger Pattern

Bulk find-replace on an HTML file containing embedded `<script>` and `<style>` will break:
- JavaScript variable names (e.g. `success_rate` → `成功率_rate`)
- CSS class names (e.g. `.btn-success` → `.btn-成功率`)
- API endpoints (e.g. `api('agents')` → `api('Agent们')`)
- Function names (e.g. `new Error()` → `new 错误()`)
- HTTP headers (e.g. `'Content-Type'` → `'Content-类型'`)
- Built-in methods (e.g. `.toFixed()` on undefined because variable name changed)

## Safe Translation Procedure

### Step 1: Extract translatable strings

Identify ONLY user-visible text:
- HTML element content: `<h3>Dashboard</h3>` → `<h3>仪表盘</h3>`
- Placeholder attributes: `placeholder="Search..."` → `placeholder="搜索..."`
- Title attributes: `title="Click to save"` → `title="点击保存"`
- Button text: `<button>Send</button>` → `<button>发送</button>`
- Toast/notification messages in JS strings

### Step 2: Protect code identifiers

These must NEVER be translated:
- JavaScript: variable names, function names, class names, method names
- CSS: class names, IDs, selectors
- HTML: element IDs, data attributes, event handler values
- API: endpoint paths, query parameter names
- HTTP: header names
- SQL: column names, table names

### Step 3: Use targeted replacements, not bulk

```python
# WRONG — will break code
content.replace('success', '成功')

# RIGHT — only replace in specific contexts
content.replace('>success<', '>成功<')
content.replace("'success'", "'成功'")  # Only in toast() calls
```

### Step 4: Verify after translation

Check for these broken patterns:
```bash
# Search for translated identifiers
grep -n "_个Agent\|_个任务\|_类型_\|_状态_" file.html
grep -n "Content-类型\|new 错误\|new Error" file.html
grep -n "api('个\|api('条" file.html
```

## Translation Map Template

```python
# SAFE — only translate display text, not code
safe_replacements = {
    # HTML display text (in template literals)
    '>Dashboard<': '>仪表盘<',
    '>Agents<': '>Agent管理<',
    
    # Toast messages (in JS strings)
    "toast('Agent deleted'": "toast('Agent已删除'",
    
    # Placeholders
    'placeholder="Search..."': 'placeholder="搜索..."',
}

# NEVER include in bulk replace:
# - Variable names (success_rate, total_agents)
# - Function names (renderDashboard, sendMsg)
# - API endpoints (api/agents, api/tasks)
# - CSS classes (.btn-success, .tag-error)
# - HTTP headers (Content-Type)
# - JS keywords (Error, Array, Object)
```

## Common Pitfalls

### Pitfall 1: Translating variable names
```javascript
// BROKEN: s.total_agents → s.total_个Agent
// BROKEN: s.avg_latency_ms → s.avg_延迟_ms
// BROKEN: s.success_rate → s.成功率_rate
```

### Pitfall 2: Translating CSS classes
```css
/* BROKEN: .btn-success → .btn-成功率 */
/* BROKEN: .tag-error → .tag-错误 */
```

### Pitfall 3: Translating API paths
```javascript
// BROKEN: api('agents') → api('个Agent')
// BROKEN: api('tasks') → api('个任务')
```

### Pitfall 4: Translating HTTP headers
```javascript
// BROKEN: 'Content-Type' → 'Content-类型'
// BROKEN: new Error() → new 错误()
```

### Pitfall 5: Translating inside template literals
```javascript
// In template literals, ${} expressions contain code
// Only translate the static parts, not the expressions
`<div>${s.total_agents} agents</div>`  // Keep s.total_agents
`<div>${s.total_agents} 个Agent</div>` // Only translate "agents"
```

### Pitfall 6: Translating data-page attributes (SPA navigation)
```html
<!-- BROKEN: data-page="agents" → data-page="个Agent" -->
<!-- BROKEN: data-page="tasks" → data-page="个任务" -->
<!-- BROKEN: data-page="events" → data-page="条事件" -->
<!-- Result: Clicking nav items does nothing — JS switch cases use English keys -->
```

### Pitfall 7: Translating function names
```javascript
// BROKEN: renderTasks → render任务
// BROKEN: renderMessages → render消息
// BROKEN: showSendMsgModal → show发送MsgModal
// BROKEN: agentName() → agent名称()
// Result: "renderTasks is not defined"
```

### Pitfall 8: Translating API paths in template literals
```javascript
// BROKEN: api(`messages?channel=...`) → api(`消息?channel=...`)
// BROKEN: api(`events?limit=...`) → api(`条事件?limit=...`)
// Result: "NOT FOUND" — API endpoint /api/消息 doesn't exist
```

### Pitfall 9: Translating switch case keys
```javascript
// BROKEN: case 'messages': → case '消息':
// BROKEN: case 'events': → case '条事件':
// Result: Page never renders — page variable is 'messages' but case is '消息'
```

### Pitfall 10: Translating HTML id attributes
```html
<!-- BROKEN: id="chat-messages" → id="chat-消息" -->
<!-- BROKEN: id="msg-input" → id="消息-input" -->
<!-- Result: getElementById('chat-messages') returns null -->
```

### Pitfall 11: Translating object property names
```javascript
// BROKEN: S.messages = ... → S.消息 = ...
// BROKEN: dropdownValues['dd-receiver'] → unchanged but S.messages references break
// Result: S.messages is undefined, code that reads it fails
```

## Post-Translation Verification Script

```bash
# Run these checks after any bulk translation
grep -n "_个Agent\|_个任务\|_个技能\|_条事件\|_延迟_\|_成功率_" file.html
grep -n "render任务\|render消息\|render事件\|show发送\|agent名称" file.html
grep -n "api('个\|api('条\|api(\`个\|api(\`条\|api(\`消息" file.html
grep -n "Content-类型\|new 错误" file.html
grep -n 'data-page="个\|data-page="条' file.html
grep -n "case '个\|case '条\|case '消息" file.html
grep -n 'id=".*个\|id=".*消息' file.html
```

## Verification Checklist

After translation, verify:
- [ ] All JavaScript runs without errors (check DevTools console)
- [ ] All API calls return expected data (check Network tab)
- [ ] All CSS styles apply correctly
- [ ] All interactive elements work (click every nav item)
- [ ] No "undefined" displayed on page
- [ ] No "Cannot read properties of undefined" errors
- [ ] No "is not defined" errors
- [ ] No "NOT FOUND" API errors
- [ ] All switch case keys match their page variable values
