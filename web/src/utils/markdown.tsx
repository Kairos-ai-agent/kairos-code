/**
 * A very small Markdown renderer for chat replies.
 *
 * Why hand-rolled: the app ships as a frozen single binary and the frontend
 * bundle is part of it. A full Markdown library plus its sanitiser is a large
 * dependency for the subset an assistant reply actually uses — fences, inline
 * code, emphasis, lists, headings, quotes and links. This renders that subset
 * and nothing else.
 *
 * Safety: it never uses ``dangerouslySetInnerHTML``. Every node is a React
 * element, so a reply containing ``<script>`` or a ``javascript:`` URL is text,
 * not markup. Links are restricted to http(s)/mailto.
 *
 * Scope: the renderer is deliberately not CommonMark. It is the grammar an
 * agent reply is written in, and anything outside it stays literal text rather
 * than being silently dropped.
 */
import React from 'react';

/** Colours/styles are passed in so the renderer follows the active theme. */
export interface MarkdownStyle {
  text: string;
  muted: string;
  codeBackground: string;
  codeColor: string;
  border: string;
  link: string;
  mono: string;
}

export const MONO_STACK = 'SF Mono, "JetBrains Mono", Consolas, monospace';

// ------------------------------------------------------------------ inline

const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(\[[^\]\n]+\]\([^)\s]+\))|(https?:\/\/[^\s<>()]+)/g;

/**
 * Inline formatting for one line of text.
 *
 * Order matters: inline code is matched first so that ``**`` inside a backtick
 * span stays literal, which is what a user expects when they write about
 * Markdown in a reply.
 */
export function renderInline(text: string, s: MarkdownStyle): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  INLINE.lastIndex = 0;
  let key = 0;
  while ((m = INLINE.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith('`')) {
      out.push(
        <code key={key++} style={{
          fontFamily: MONO_STACK, fontSize: '0.9em',
          background: s.codeBackground, color: s.codeColor,
          padding: '1px 5px', borderRadius: 4,
        }}>{tok.slice(1, -1)}</code>,
      );
    } else if (tok.startsWith('**')) {
      out.push(<strong key={key++}>{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith('*')) {
      out.push(<em key={key++}>{tok.slice(1, -1)}</em>);
    } else if (tok.startsWith('[')) {
      const label = tok.slice(1, tok.indexOf(']'));
      const href = tok.slice(tok.indexOf('](') + 2, -1);
      out.push(href.startsWith('http') || href.startsWith('mailto:')
        ? <a key={key++} href={href} target="_blank" rel="noreferrer noopener"
             style={{ color: s.link }}>{label}</a>
        // A link with an unusable scheme stays visible as text rather than
        // becoming a clickable hazard.
        : <span key={key++}>{label}</span>);
    } else {
      out.push(<a key={key++} href={tok} target="_blank" rel="noreferrer noopener"
                  style={{ color: s.link, wordBreak: 'break-all' }}>{tok}</a>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// ------------------------------------------------------------------- block

interface Block {
  kind: 'code' | 'heading' | 'list' | 'quote' | 'hr' | 'p';
  /** Fence language, heading level, or "ul"/"ol" for lists. */
  meta?: string | number;
  lines: string[];
}

/**
 * Split a reply into block-level chunks. A line-oriented state machine is
 * enough here and keeps the code readable: fences toggle, everything else is
 * collected until a blank line or a new block starts.
 */
export function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // fenced code
    const fence = line.match(/^\s*```(\w*)\s*$/);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
      i++; // closing fence
      blocks.push({ kind: 'code', meta: fence[1] || '', lines: body });
      continue;
    }
    // horizontal rule
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push({ kind: 'hr', lines: [] });
      i++;
      continue;
    }
    // heading
    const h = line.match(/^\s*(#{1,4})\s+(.*)$/);
    if (h) {
      blocks.push({ kind: 'heading', meta: h[1].length, lines: [h[2]] });
      i++;
      continue;
    }
    // blockquote
    if (/^\s*>\s?/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        body.push(lines[i++].replace(/^\s*>\s?/, ''));
      }
      blocks.push({ kind: 'quote', lines: body });
      continue;
    }
    // list (unordered or ordered)
    const ul = /^\s*[-*+]\s+/;
    const ol = /^\s*\d+[.)]\s+/;
    if (ul.test(line) || ol.test(line)) {
      const ordered = ol.test(line);
      const body: string[] = [];
      const re = ordered ? /^\s*\d+[.)]\s+/ : ul;
      while (i < lines.length && re.test(lines[i])) {
        body.push(lines[i++].replace(re, ''));
      }
      blocks.push({ kind: 'list', meta: ordered ? 'ol' : 'ul', lines: body });
      continue;
    }
    // blank
    if (!line.trim()) { i++; continue; }
    // paragraph: until a blank line or the start of another block
    const body: string[] = [];
    while (i < lines.length && lines[i].trim()
           && !/^\s*```/.test(lines[i])
           && !/^\s*(#{1,4})\s+/.test(lines[i])
           && !/^\s*>\s?/.test(lines[i])
           && !ul.test(lines[i]) && !ol.test(lines[i])) {
      body.push(lines[i++]);
    }
    blocks.push({ kind: 'p', lines: body });
  }
  return blocks;
}

/** Render a reply. Memoise at the call site — this walks the whole string. */
export function renderMarkdown(text: string, s: MarkdownStyle): React.ReactNode {
  const blocks = parseBlocks(text);
  return blocks.map((b, idx) => {
    switch (b.kind) {
      case 'code':
        return (
          <pre key={idx} style={{
            fontFamily: MONO_STACK, fontSize: 12, lineHeight: 1.55,
            background: s.codeBackground, color: s.codeColor,
            border: `1px solid ${s.border}`,
            borderRadius: 8, padding: '10px 12px',
            margin: '8px 0', overflowX: 'auto',
            whiteSpace: 'pre',
          }}>
            {b.meta && (
              <div style={{ fontSize: 10, color: s.muted, marginBottom: 6 }}>
                {b.meta}
              </div>
            )}
            <code>{b.lines.join('\n')}</code>
          </pre>
        );
      case 'heading': {
        const level = Number(b.meta) || 1;
        const size = [17, 15.5, 14.5, 13.5][level - 1] ?? 13.5;
        return (
          <div key={idx} style={{
            fontSize: size, fontWeight: 700, color: s.text,
            margin: '12px 0 4px',
          }}>
            {renderInline(b.lines[0] || '', s)}
          </div>
        );
      }
      case 'hr':
        return <hr key={idx} style={{
          border: 'none', borderTop: `1px solid ${s.border}`, margin: '12px 0',
        }} />;
      case 'quote':
        return (
          <blockquote key={idx} style={{
            margin: '8px 0', paddingLeft: 10,
            borderLeft: `3px solid ${s.border}`, color: s.muted,
          }}>
            {renderInline(b.lines.join('\n'), s)}
          </blockquote>
        );
      case 'list': {
        const ordered = b.meta === 'ol';
        const Tag = ordered ? 'ol' : 'ul';
        return (
          <Tag key={idx} style={{ margin: '6px 0', paddingInlineStart: 20 }}>
            {b.lines.map((li, j) => (
              <li key={j} style={{ marginBottom: 2 }}>{renderInline(li, s)}</li>
            ))}
          </Tag>
        );
      }
      default:
        return (
          <div key={idx} style={{ margin: b.lines.length > 1 ? '6px 0' : '2px 0' }}>
            {b.lines.map((ln, j) => (
              <React.Fragment key={j}>
                {renderInline(ln, s)}
                {j < b.lines.length - 1 && <br />}
              </React.Fragment>
            ))}
          </div>
        );
    }
  });
}
