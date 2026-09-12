/** Layout contract for the bottom-left sidebar footer.
 *
 * The markup it replaced was measurably messy, which is why these assertions
 * exist (numbers are from the running app, not from reading the code):
 *
 *   row 1  运行 / 历史 / 设置   3 buttons, 85px each, centred
 *   row 2  高级                263px wide, LEFT aligned
 *   row 3-4 (expanded)         3 buttons of 83px at x=14, then FOUR buttons of
 *                              61px -> 「全部项目」 wrapped onto two lines and
 *                              the rows stopped sharing column edges
 *   row 5  深色                263px wide, CENTRED
 *
 * i.e. three alignment modes, three button widths, two left edges and a
 * wrapped label. The replacement is one 3-column grid for every destination
 * row plus two full-width outlined utility bars. These tests pin that
 * structure so it cannot quietly drift back.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../api/client', () => ({
  default: {
    get: vi.fn(() => Promise.resolve({ data: {} })),
    post: vi.fn(() => Promise.resolve({ data: {} })),
  },
  connectWebSocket: vi.fn(),
}));

import { SidebarFooter } from '../components/ChatSidebar';

const DESTINATIONS = [
  'footer-run', 'footer-history', 'footer-settings',
  'footer-today', 'footer-tools', 'footer-loop',
  'footer-trace', 'footer-projects', 'footer-dashboard',
];

const BARS = ['footer-advanced', 'footer-theme'];

const renderFooter = () => render(
  <ConfigProvider>
    <MemoryRouter initialEntries={['/run']}>
      <SidebarFooter />
    </MemoryRouter>
  </ConfigProvider>,
);

describe('SidebarFooter layout', () => {
  it('renders every destination plus the two utility bars', () => {
    renderFooter();
    for (const id of [...DESTINATIONS, ...BARS]) {
      expect(screen.getByTestId(id), `missing ${id}`).toBeInTheDocument();
    }
  });

  it('keeps the advanced group hidden until the bar is clicked', () => {
    renderFooter();
    const group = screen.getByTestId('footer-advanced-group');
    expect(group.style.display).toBe('none');
    fireEvent.click(screen.getByTestId('footer-advanced'));
    expect(group.style.display).toBe('grid');
    fireEvent.click(screen.getByTestId('footer-advanced'));
    expect(group.style.display).toBe('none');
  });

  it('does not duplicate the new-chat action', () => {
    // 「新建对话」used to sit here too, next to the NewChatButton at the top of
    // the sidebar — the same action in two places. Its removal also leaves the
    // group with six entries, i.e. two full rows of the 3-column grid.
    renderFooter();
    expect(screen.queryByTestId('footer-chat')).toBeNull();
    fireEvent.click(screen.getByTestId('footer-advanced'));  // expand: hidden
    // rows are pruned from the accessibility tree
    const group = screen.getByTestId('footer-advanced-group');
    expect(within(group).getAllByRole('button')).toHaveLength(6);
  });

  it('lays every destination row out on the same 3-column grid', () => {
    renderFooter();
    const footer = screen.getByTestId('sidebar-footer');
    const primaryRow = footer.firstElementChild as HTMLElement;
    // The three primaries and the expanded group must share the identical
    // track definition, otherwise their columns stop lining up (that was the
    // 85px vs 83px vs 61px problem).
    expect(primaryRow.style.gridTemplateColumns).toBe('repeat(3, minmax(0, 1fr))');
    const group = screen.getByTestId('footer-advanced-group');
    expect(group.style.gridTemplateColumns).toBe('repeat(3, minmax(0, 1fr))');
  });

  it('never lets a destination label wrap or stretch', () => {
    renderFooter();
    for (const id of DESTINATIONS) {
      const btn = screen.getByTestId(id);
      const label = within(btn).getByText(/.+/);
      expect(label.style.whiteSpace, `${id} may wrap`).toBe('nowrap');
      expect(label.style.textOverflow, `${id} has no ellipsis guard`).toBe('ellipsis');
      // The old markup set flex:1 on the buttons inside a flex row, which is
      // what produced 61px vs 85px columns.
      expect(btn.style.flex, `${id} still stretches`).toBe('');
      expect(btn.style.justifyContent).toBe('center');
    }
  });

  it('renders the two utility bars as a different control class', () => {
    renderFooter();
    for (const id of BARS) {
      const bar = screen.getByTestId(id);
      expect(bar.style.justifyContent, `${id} should be left aligned`).toBe('flex-start');
      expect(bar.style.border, `${id} should be outlined`).toContain('1px solid');
    }
    // ...and destinations stay borderless / centred.
    for (const id of DESTINATIONS) {
      expect(screen.getByTestId(id).style.border).toBe('');
    }
  });
});
