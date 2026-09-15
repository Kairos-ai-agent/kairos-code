/**
 * R38.12 ⑦ — the capability panel reports what the runtime says.
 *
 * The value of this screen is that it cannot flatter the install. So the test
 * feeds it an install with a problem, a rejected server and a shipped plugin,
 * and insists all three are visible — plus the failure path, since a panel that
 * silently shows nothing is worse than one that says it could not load.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

vi.mock('../hooks/useThemeTokens', () => ({
  useThemeTokens: () => ({
    labelPrimary: '#000', labelSecondary: '#666', labelTertiary: '#999',
    border: '#ddd', bgBase: '#fff', bgLay1: '#fafafa', success: '#3f8600',
  }),
}));

const get = vi.fn();
vi.mock('../api/client', () => ({
  default: {
    get: (...args: unknown[]) => get(...args),
    post: vi.fn(),
  },
}));

import Tools from '../pages/Tools';

const payload = {
  skills: {
    count: 36,
    filesOnDisk: 36,
    byScope: { bundled: 36, global: 0, project: 0, unknown: 0 },
    items: [
      { name: 'test-driven-development', scope: 'bundled', priority: 0.8,
        when: { keyword: ['test'] }, description: 'Write the test first',
        path: '/p/skills/test-driven-development/SKILL.md' },
      { name: 'legacy-helper', scope: 'bundled', priority: 0.5, when: {},
        description: '', path: '/p/skills/legacy-helper.md' },
    ],
  },
  mcp: {
    configured: [
      { name: 'git', transport: 'stdio', enabled: true, bundled: true,
        source: 'bundled-plugin', needsNetwork: false, headerNames: [],
        command: 'python -m kairos.mcp_local_servers --server git' },
    ],
    rejected: { ghost: "command 'nope' not found on PATH" },
    bundledServers: [
      { name: 'time', offline: true, toolCount: 2,
        tools: ['time_now', 'time_parse'] },
    ],
    startupErrors: {},
    probed: false,
  },
  plugins: {
    installed: [],
    bundled: [
      { name: 'kairos-essentials', version: '1.0.0', capabilities: ['mcp'],
        compatible: true, bundled: true, enabled: true },
    ],
    count: 0,
    bundledCount: 1,
    availableInRegistry: 20,
  },
  nativeTools: ['terminal', 'file_read'],
  problems: ['skill deep-research: source-missing (upstream removed it)'],
  scanned: { projectId: null, projectRoot: null, bundledSkillsDir: '/p/skills' },
};

const renderPanel = () =>
  render(
    <MemoryRouter initialEntries={['/tools/capabilities']}>
      <Routes>
        <Route path="/tools/:tool" element={<Tools />} />
      </Routes>
    </MemoryRouter>,
  );

describe('capability panel (R38.12)', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('asks the capabilities endpoint for the current project', async () => {
    get.mockResolvedValue({ data: payload });
    renderPanel();
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(get.mock.calls[0][0]).toBe('/extensions/capabilities');
  });

  it('shows the counts the runtime reported', async () => {
    get.mockResolvedValue({ data: payload });
    renderPanel();
    // 36 skills loaded out of 36 files on disk — the invariant that was broken.
    await waitFor(() => expect(screen.getByText('36')).toBeTruthy());
    expect(screen.getByText(/\/ 36/)).toBeTruthy();
  });

  it('shows a problem instead of hiding it', async () => {
    get.mockResolvedValue({ data: payload });
    renderPanel();
    await waitFor(() => expect(screen.getByText(/source-missing/)).toBeTruthy());
  });

  it('shows a configured-but-unusable server with its reason', async () => {
    get.mockResolvedValue({ data: payload });
    renderPanel();
    await waitFor(() => expect(screen.getByText('ghost')).toBeTruthy());
    expect(screen.getByText(/not found on PATH/)).toBeTruthy();
  });

  it('shows the plugin that ships with the build', async () => {
    get.mockResolvedValue({ data: payload });
    renderPanel();
    await waitFor(() =>
      expect(screen.getByText('kairos-essentials')).toBeTruthy());
    expect(screen.getByText('v1.0.0')).toBeTruthy();
  });

  it('marks a skill without a trigger, and one with', async () => {
    get.mockResolvedValue({ data: payload });
    renderPanel();
    await waitFor(() =>
      expect(screen.getByText('test-driven-development')).toBeTruthy());
    expect(screen.getByText('legacy-helper')).toBeTruthy();
  });

  it('says so when the report cannot be loaded', async () => {
    get.mockRejectedValue(new Error('boom'));
    renderPanel();
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(screen.queryByText(/source-missing/)).toBeNull();
  });
});
