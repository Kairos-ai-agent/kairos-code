/**
 * The Markdown renderer is small but it is the thing standing between a reply
 * and the user's eyes, so its grammar is pinned here: the subset it supports
 * works, and anything outside the subset stays visible as text instead of
 * disappearing or becoming markup.
 */
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { parseBlocks, renderInline, renderMarkdown, MONO_STACK } from '../utils/markdown';

const S = {
  text: '#111', muted: '#666', codeBackground: '#f5f5f5', codeColor: '#111',
  border: '#ddd', link: '#06f', mono: MONO_STACK,
};

const html = (node: React.ReactNode): string => {
  const { container } = render(<div>{node}</div>);
  return container.innerHTML;
};

describe('parseBlocks', () => {
  it('separates fenced code from prose and keeps its language', () => {
    const blocks = parseBlocks('before\n\n```python\nx = 1\n```\nafter');
    expect(blocks.map((b) => b.kind)).toEqual(['p', 'code', 'p']);
    expect(blocks[1].meta).toBe('python');
    expect(blocks[1].lines).toEqual(['x = 1']);
  });

  it('treats an unterminated fence as code rather than swallowing nothing', () => {
    const blocks = parseBlocks('```\nstill code');
    expect(blocks).toHaveLength(1);
    expect(blocks[0].kind).toBe('code');
    expect(blocks[0].lines).toEqual(['still code']);
  });

  it('reads headings, lists, quotes and rules', () => {
    const blocks = parseBlocks('# H1\n\n- a\n- b\n\n> quoted\n\n---');
    expect(blocks.map((b) => b.kind)).toEqual(
      ['heading', 'list', 'quote', 'hr']);
    expect(blocks[0].meta).toBe(1);
    expect(blocks[1].meta).toBe('ul');
    expect(blocks[1].lines).toEqual(['a', 'b']);
  });

  it('distinguishes ordered from unordered lists', () => {
    const blocks = parseBlocks('1. one\n2. two');
    expect(blocks[0].meta).toBe('ol');
    expect(blocks[0].lines).toEqual(['one', 'two']);
  });
});

describe('renderInline', () => {
  it('renders code, bold, italic and links', () => {
    const out = html(renderInline('a `c` **b** *i* [t](https://e.com)', S));
    expect(out).toContain('<code');
    expect(out).toContain('<strong>b</strong>');
    expect(out).toContain('<em>i</em>');
    expect(out).toContain('href="https://e.com"');
    expect(out).toContain('rel="noreferrer noopener"');
  });

  it('keeps markdown inside a code span literal', () => {
    const out = html(renderInline('use `**not bold**` here', S));
    expect(out).toContain('<code');
    expect(out).toContain('**not bold**');
    expect(out).not.toContain('<strong>');
  });

  it('never produces a javascript: link', () => {
    const out = html(renderInline('[x](javascript:alert(1))', S));
    expect(out).not.toContain('javascript:');
    expect(out).toContain('x');
  });
});

describe('renderMarkdown', () => {
  it('escapes HTML in a reply instead of executing it', () => {
    // The renderer builds React elements, so a script tag in a reply is text.
    const out = html(renderMarkdown('hello <script>alert(1)</script>', S));
    expect(out).not.toContain('<script>');
    expect(out).toContain('&lt;script&gt;');
  });

  it('renders a fenced block as <pre><code> with the language label', () => {
    const out = html(renderMarkdown('```ts\nconst x = 1\n```', S));
    expect(out).toContain('<pre');
    expect(out).toContain('<code');
    expect(out).toContain('const x = 1');
    expect(out).toContain('ts');
  });

  it('renders a list with one <li> per line', () => {
    const out = html(renderMarkdown('- one\n- two\n- three', S));
    expect((out.match(/<li/g) || []).length).toBe(3);
  });

  it('keeps blank lines inside a paragraph as line breaks, not lost text', () => {
    const out = html(renderMarkdown('first\nsecond', S));
    expect(out).toContain('first');
    expect(out).toContain('<br>');
    expect(out).toContain('second');
  });
});
