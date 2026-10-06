/**
 * The far-right scrollbar used to scroll the entire app.
 *
 * jsdom has no layout engine, so this cannot be asserted by rendering — a
 * rendered test would pass while the real window still scrolled. It is
 * therefore a source-level check on the one thing that decides it: which
 * element is allowed to have a definite height, and which element is allowed
 * to overflow. Same convention as tests/test_chat_thread_fills_its_slot.py,
 * which owns the rule for the rest of the shell.
 *
 * Sources are read with Vite's `?raw` glob (not `fs`) because the app's
 * tsconfig has no @types/node — same as i18nKeys.test.ts.
 *
 * The chain, top to bottom:
 *   <Content>            definite height (the shell owns the viewport — AppLayout)
 *   Sider[workbench]     definite height, overflow: hidden   ← was auto-sized
 *   wrapper              flex: 1, minHeight: 0               ← was flex: 0 0 auto
 *   WorkbenchPanel       height: 100%, minHeight: 0
 *   Tabs pane            height: 100% (see WORKBENCH_TABS_CSS)
 *   FilesTab             flex: 1, overflow: auto             ← the only scroller
 */
import { describe, it, expect } from 'vitest';

const RAW = import.meta.glob('../components/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

const source = (name: string): string => {
  const hit = Object.entries(RAW)
    .find(([file]) => file.endsWith(`/${name}.tsx`));
  if (!hit) throw new Error(`raw source for ${name} not found`);
  return hit[1];
};

/** The opening `<Sider ...>` of the right rail (its own comment → its testid). */
const railOpening = (src: string): string =>
  src.slice(src.indexOf('Right-side Workbench'), src.indexOf('workbench-sider'));

describe('workbench scroll ownership', () => {
  const layout = source('AppLayout');
  const panel = source('WorkbenchPanel');

  it('gives the right rail a definite height, from the shell\'s own constant', () => {
    expect(railOpening(layout)).toContain('calc(100vh - ${LAYOUT.topbarHeight}px)');
  });

  it('hands the panel a definite slot instead of a content-sized one', () => {
    // `flex: '0 0 auto'` means "as tall as my content", so every `height: 100%`
    // underneath resolved against `auto` — the column grew and the page moved.
    expect(layout).not.toContain("flex: '0 0 auto', minHeight: 0");
    expect(layout).toContain('flex: 1, minHeight: 0');
  });

  it('does not let the rail or the panel be a scroll container itself', () => {
    expect(railOpening(layout)).toContain("overflow: 'hidden'");
    // The panel fills, it does not scroll: the file list does.
    expect(panel).toContain("height: '100%', minHeight: 0");
  });

  it('makes the file list the only thing that scrolls', () => {
    const at = panel.indexOf('data-testid="files-tab"');
    expect(panel.slice(at, at + 160)).toContain("overflow: 'auto'");
    // ...which only works if the antd pane above it has a definite height.
    expect(panel).toContain(
      '.kairos-workbench-tabs .ant-tabs-tabpane{height:100%');
    expect(panel).toContain(
      '.kairos-workbench-tabs .ant-tabs-content{height:100%}');
    expect(panel).toContain(
      '.kairos-workbench-tabs .ant-tabs-content-holder{overflow:hidden');
  });
});
