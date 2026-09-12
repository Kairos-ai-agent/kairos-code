/**
 * i18n completeness checker — the guard against "部分切换".
 *
 * Parses every frontend source file with the TypeScript compiler API (grep
 * cannot tell a user-visible string from a className) and reports:
 *
 *   1. literals left in user-visible positions:
 *        - JSX text nodes with letters (English or CJK)
 *        - JSX props: placeholder/title/label/description/help/emptyText/
 *          okText/cancelText/aria-label with a literal value
 *        - object fields with the same names (Tabs items, table columns, …)
 *        - message.success/error/warning/info/open, notification.*,
 *          Modal.confirm() first argument
 *   2. `t('key')` calls whose key is missing from the dictionary
 *      (src/i18n/parts/*.json)
 *   3. dictionary keys that no code uses any more (dead copy)
 *
 * Usage (from web/):  node ../scripts/check_i18n.mjs [--fix-hint]
 * Exit code 1 when anything is found.
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..');

// `typescript` lives in web/node_modules (this script sits at the repo root).
const require = createRequire(path.join(REPO, 'web', 'package.json'));
const ts = require('typescript');
const SRC = path.join(REPO, 'web', 'src');
const PARTS = path.join(SRC, 'i18n', 'parts');

const UI_PROPS = new Set([
  'placeholder', 'title', 'label', 'description', 'help', 'emptyText',
  'okText', 'cancelText', 'aria-label', 'header', 'subTitle', 'extra',
  'tooltip', 'message', 'content', 'note', 'hint', 'alt',
]);
const TOAST_CALLS = /\b(message|notification|Modal)\.(success|error|warning|info|loading|confirm|open)\s*$/;
// Strings that are legitimately not translated.
const ALLOW_LITERALS = new Set([
  '', '-', '—', '·', '/', '|', ',', '.', ':', '×', '⌘', '+', '%', '#',
  '…', '↑', '↓', '→', '←', '✓', '✕', '⚠️', '▶', '⏸', '⏹',
]);
const ALLOW_PROP_VALUES = new Set(['small', 'middle', 'large', 'top', 'right', 'left', 'bottom', 'primary', 'dashed', 'text', 'link', 'default', 'circle', 'horizontal', 'vertical', 'ellipsis', 'row', 'column']);
// Brand / protocol / product names that are correct in every language.
const PROPER_NOUNS = new Set([
  'Kairos', 'OpenAI', 'Anthropic', 'DeepSeek', 'Claude', 'GPT', 'Ollama',
  'MiniMax', 'GitHub', 'GitLab', 'Docker', 'Node', 'Python', 'React',
  'TypeScript', 'JavaScript', 'Vite', 'Windows', 'macOS', 'Linux', 'Redis',
  'Postgres', 'MySQL', 'SQLite', 'JSON', 'YAML', 'TOML', 'HTML', 'CSS',
  'SVG', 'PNG', 'JPG', 'JPEG', 'PDF', 'CSV', 'Markdown', 'MIME', 'HTTP',
  'HTTPS', 'WebSocket', 'SSE', 'MCP', 'OAuth', 'API', 'APIs', 'URL', 'URLs',
  'ID', 'UUID', 'CPU', 'RAM', 'GPU', 'LLM', 'LLMs', 'S3', 'AWS', 'GCP',
  'Azure', 'Cloudflare', 'Upwork', 'Stripe', 'Linear', 'Notion', 'Figma',
  'VSCode', 'Cursor', 'Bash', 'PowerShell', 'PowerShell', 'Git',
]);
const isProperNoun = (text) => {
  if (PROPER_NOUNS.has(text)) return true;
  // e.g. "v1.2.3", "gpt-4o", "deepseek-chat", "claude-3-5-sonnet-latest"
  if (/^[A-Za-z]+[\w.+-]*[\w]$/.test(text) && !/\s/.test(text)
      && /^[a-z0-9.+-]/.test(text) && !/^[a-z]+$/.test(text)) return true;
  // camelCase / ALL-CAPS tokens (CoderMode, MCP, SSE) read the same everywhere
  if (/^[A-Za-z0-9]+$/.test(text) && /[A-Z]/.test(text)) return true;
  return false;
};

const hasLetters = (s) => /[A-Za-z\u4e00-\u9fff]/.test(s);
const hasCJK = (s) => /[\u4e00-\u9fff]/.test(s);

function walkFiles(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === 'test' || entry.name === 'i18n') continue;
      walkFiles(full, out);
    } else if (/\.(tsx?|jsx?)$/.test(entry.name) && !/\.(test|spec)\./.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

function loadDictionary() {
  const dict = new Map();
  for (const name of fs.readdirSync(PARTS).filter((f) => f.endsWith('.json'))) {
    const data = JSON.parse(fs.readFileSync(path.join(PARTS, name), 'utf8'));
    for (const key of Object.keys(data)) dict.set(key, name);
  }
  return dict;
}

const problems = [];
const usedKeys = new Set();
const report = (file, node, kind, text) => {
  const { line } = ts.getLineAndCharacterOfPosition(
    node.getSourceFile(), node.getStart());
  problems.push(`${path.relative(REPO, file)}:${line + 1}  [${kind}] ${JSON.stringify(text)}`);
};

const dict = loadDictionary();

for (const file of walkFiles(SRC)) {
  const source = fs.readFileSync(file, 'utf8');
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true,
                                file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);

  const visit = (node) => {
    // 1a. JSX text
    if (ts.isJsxText(node)) {
      const text = node.text.replace(/\s+/g, ' ').trim();
      // one-character labels (avatar letters, 'R' for round) are not copy
      if (text.length > 1 && text && hasLetters(text) && !ALLOW_LITERALS.has(text)
          && !isProperNoun(text)) {
        report(file, node, 'jsx-text', text);
      }
    }
    // 1b. JSX props with literal values
    if (ts.isJsxAttribute(node) && node.initializer
        && ts.isStringLiteral(node.initializer)
        && UI_PROPS.has(node.name.getText(sf))) {
      const value = node.initializer.text.trim();
      if (hasLetters(value) && !ALLOW_PROP_VALUES.has(value)
          && !isProperNoun(value)) {
        report(file, node, `prop:${node.name.getText(sf)}`, value);
      }
    }
    // 1c. object fields with the same names (AntD Tabs/columns config)
    if (ts.isPropertyAssignment(node) && node.initializer
        && ts.isStringLiteral(node.initializer)) {
      const name = node.name.getText(sf).replace(/['"]/g, '');
      const value = node.initializer.text.trim();
      if (UI_PROPS.has(name) && hasLetters(value) && !ALLOW_PROP_VALUES.has(value)
          && !isProperNoun(value)) {
        report(file, node, `field:${name}`, value);
      }
    }
    // 1d. toasts / modals
    if (ts.isCallExpression(node) && node.arguments.length) {
      const callee = node.expression.getText(sf);
      if (TOAST_CALLS.test(callee)) {
        const first = node.arguments[0];
        const literal = ts.isStringLiteral(first) ? first
          : (ts.isObjectLiteralExpression(first)
            ? first.properties.find((p) => ts.isPropertyAssignment(p)
                && ['content', 'title', 'message'].includes(p.name.getText(sf))
                && ts.isStringLiteral(p.initializer))?.initializer
            : undefined);
        if (literal && ts.isStringLiteral(literal)
            && hasLetters(literal.text) && !ALLOW_LITERALS.has(literal.text.trim())
            && !isProperNoun(literal.text.trim())) {
          report(file, node, 'toast', literal.text);
        }
      }
    }
    // 1e. user-visible strings handed to state setters / toast helpers
    //     (``setModelFetchError('请先填写 endpoint URL')``, ``msgApi.error('...')``).
    //     Blind spot of the JSX-only rules: these never reach the screen through
    //     JSX text, so a "fully switched" UI can still leak raw literals here.
    if (ts.isCallExpression(node) && node.arguments.length
        && ts.isStringLiteral(node.arguments[0])) {
      const callee2 = node.expression.getText(sf);
      const skip = /^(console|logger)\.|^Error$|^Object\./.test(callee2);
      const looksUserFacing = /\.(error|success|warning|info|open|loading)$/.test(callee2)
        || /^alert$/.test(callee2)
        || /^set[A-Z][A-Za-z0-9_]*(Error|Err|Message|Msg|Notice|Hint|Warn|Warning|Info)$/.test(callee2);
      const ctext = node.arguments[0].text.trim();
      if (!skip && looksUserFacing && hasLetters(ctext)
          && (hasCJK(ctext) || /\s/.test(ctext) || ctext.length > 12)
          && !ALLOW_LITERALS.has(ctext) && !isProperNoun(ctext)) {
        report(file, node, 'call-arg', ctext);
      }
    }
    // 2. t()/tGlobal() keys
    if (ts.isCallExpression(node) && node.arguments.length
        && /^(t|tGlobal)$/.test(node.expression.getText(sf))
        && ts.isStringLiteral(node.arguments[0])) {
      const key = node.arguments[0].text;
      usedKeys.add(key);
      if (key.includes('.') && !dict.has(key)) {
        report(file, node, 'missing-key', key);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
}

// 3. dead copy: dictionary keys nothing references any more (informational,
//    not a failure — a shared key may be referenced by a screen that is
//    temporarily unmounted).
const dead = [...dict.keys()].filter((k) => !usedKeys.has(k));

console.log(`files scanned: ${walkFiles(SRC).length}`);
console.log(`dictionary keys: ${dict.size} | keys used in code: ${usedKeys.size}`);
if (problems.length) {
  console.log(`\n${problems.length} problem(s):`);
  for (const p of problems) console.log('  ' + p);
} else {
  console.log('\nno hardcoded user-visible strings, no missing keys');
}
if (dead.length) {
  console.log(`\n${dead.length} unused key(s) (informational):`);
  for (const k of dead.slice(0, 40)) console.log('  ' + k);
  if (dead.length > 40) console.log(`  … and ${dead.length - 40} more`);
}
process.exit(problems.length ? 1 : 0);
