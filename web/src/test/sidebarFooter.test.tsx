/**
 * Layout contract for the bottom-left sidebar footer.
 *
 * The markup this replaced was measurably messy, which is why these assertions
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
 * wrapped label — and, after all of that, no way to tell which page you were
 * on. The replacement answers the two questions the rail is for: everything is
 * visible under a labelled group, every group shares one grid, and the current
 * route is marked.
 *
 * These tests pin that structure so it cannot quietly drift back.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
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
  'footer-run', 'footer-history', 'footer-chat',
  'footer-tools', 'footer-marketplace', 'footer-trace',
  'footer-today', 'footer-projects', 'footer-loop', 'footer-dashboard',
  'footer-settings', 'footer-theme',
];

const renderFooter = (route = '/run') => render(
  <ConfigProvider>
    <MemoryRouter initialEntries={[route]}>
      <SidebarFooter />
    </MemoryRouter>
  </ConfigProvider>,
);

describe('SidebarFooter layout', () => {
  it('renders every destination', () => {
    renderFooter();
    for (const id of DESTINATIONS) {
      expect(screen.getByTestId(id), `missing ${id}`).toBeInTheDocument();
    }
  });

  it('shows the destinations under labelled groups, with nothing hidden', () => {
    renderFooter();
    // Four groups: 导航 / 扩展 / 系统 / 偏好.
    expect(screen.getAllByTestId('footer-group-label')).toHaveLength(4);
    // The old footer hid six entries behind a disclosure; there is no such
    // control any more, so every cell is reachable in one click.
    expect(screen.queryByTestId('footer-advanced')).toBeNull();
    expect(screen.queryByTestId('footer-advanced-group')).toBeNull();
  });

  it('lays every group out on one shared grid, wide enough for the labels', () => {
    renderFooter();
    const groups = screen.getAllByTestId('footer-group-label')
      .map((label) => label.nextElementSibling as HTMLElement);
    expect(groups.length).toBe(4);
    const template = groups[0].style.gridTemplateColumns;
    // One definition for every group, or the column edges stop lining up (that
    // was the 85px vs 83px vs 61px problem).
    for (const grid of groups) {
      expect(grid.style.gridTemplateColumns).toBe(template);
    }
    // The track count has to come from the longest label, not from what looks
    // tidy: at four columns a cell in this sidebar is ~52px, and the rail
    // truncated its own words (a screenshot check caught "全…" for "全部项目"
    // and would have caught "Marketplace" in English).
    expect(template).toBe('repeat(3, minmax(0, 1fr))');
  });

  it('stacks the icon over the label so the label owns the full cell', () => {
    // Side by side, icon + label compete for ~72px and the label loses; stacked,
    // only the label's own length matters.
    renderFooter();
    for (const id of DESTINATIONS) {
      expect(screen.getByTestId(id).style.flexDirection, `${id} must stack`).toBe('column');
    }
  });

  it('marks the current route, and only that one', () => {
    renderFooter('/run');
    expect(screen.getByTestId('footer-run').getAttribute('aria-current')).toBe('page');
    for (const id of DESTINATIONS.filter((d) => d !== 'footer-run')) {
      expect(screen.getByTestId(id).getAttribute('aria-current'),
             `${id} must not claim to be current`).toBeNull();
    }
  });

  it('keeps a child route lit (trace with a project selected)', () => {
    renderFooter('/trace/proj-1');
    expect(screen.getByTestId('footer-trace').getAttribute('aria-current')).toBe('page');
    expect(screen.getByTestId('footer-chat').getAttribute('aria-current')).toBeNull();
  });

  it('lights the marketplace route', () => {
    renderFooter('/marketplace');
    expect(screen.getByTestId('footer-marketplace').getAttribute('aria-current'))
      .toBe('page');
  });

  it('never lets a destination label wrap or stretch', () => {
    renderFooter();
    for (const id of DESTINATIONS) {
      const btn = screen.getByTestId(id);
      const label = within(btn).getByText(/.+/);
      expect(label.style.whiteSpace, `${id} may wrap`).toBe('nowrap');
      expect(label.style.textOverflow, `${id} has no ellipsis guard`).toBe('ellipsis');
      // The old markup set flex:1 on buttons inside a flex row, which is what
      // produced 61px vs 85px columns.
      expect(btn.style.flex, `${id} still stretches`).toBe('');
      expect(btn.style.justifyContent).toBe('center');
    }
  });

  it('gives settings and the theme switch the same cell as a destination', () => {
    // They are one click, so they get the same grid — the outlined "bar" chrome
    // was part of what made the footer look uneven.
    renderFooter();
    for (const id of ['footer-settings', 'footer-theme']) {
      const btn = screen.getByTestId(id);
      expect(btn.style.border, `${id} should not be outlined`).toBe('');
      expect(btn.style.justifyContent).toBe('center');
    }
  });
});
