/** Tests for the FolderPicker topbar control.
 *
 * BrowsePanel is stubbed: it is not what these tests are about, and mounting it
 * inside the modal kept the vitest worker alive after the assertions passed.
 *
 * Rewritten to assert on testids and on the API call itself instead of on
 * English copy: the copy now comes from the 63-language dictionary (and is
 * rendered inside split elements), so text queries were brittle. The behaviour
 * under test is unchanged: the trigger opens the modal, the manual path can be
 * submitted with the button or with Enter, and the API is actually called.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConfigProvider, App as AntdApp } from 'antd';

import api from '../api/client';
import FolderPicker from '../components/FolderPicker';
import { useChatStore } from '../stores/chatStore';

// The modal embeds the directory browser; these tests only exercise the manual
// path input, and mounting the browser keeps the worker alive (see the module
// docstring of this test file).
vi.mock('../components/BrowsePanel', () => ({
  default: () => <div data-testid="browse-panel-stub" />,
}));

vi.mock('../api/client', () => ({
  default: {
    // BrowsePanel (mounted inside the modal) lists the filesystem roots on
    // mount. NOTE: the implementation is passed at creation time on purpose —
    // src/test/setup.ts calls vi.restoreAllMocks() after every test, which
    // wipes implementations added later through .mockResolvedValue().
    get: vi.fn(async () => ({ data: [] })),
    post: vi.fn(async (url: string, body: any) => {
      // Simulate the backend creating a project from a folder path.
      if (url === '/projects') {
        return {
          data: {
            id: 'p-folder',
            name: body.name,
            description: body.description,
            workspace: body.work_dir,
            work_dir: body.work_dir,
            status: 'active',
            task_count: 0, agent_count: 0, created_at: 1.0,
          },
        };
      }
      return { data: {} };
    }),
  },
}));

const renderWithAntd = () => {
  return render(
    <ConfigProvider>
      <AntdApp>
        <FolderPicker />
      </AntdApp>
    </ConfigProvider>,
  );
};

describe('FolderPicker', () => {
  beforeEach(() => {
    localStorage.clear();
    // The zustand store is module-level: a previous test that created a project
    // would leave it selected here, which switches the trigger from the icon
    // button to the project switcher.
    useChatStore.getState().reset();
    vi.mocked(api.post).mockClear();
  });

  it('renders the folder trigger button', () => {
    renderWithAntd();
    expect(screen.getByTestId('composer-add-folder')).toBeInTheDocument();
  });

  it('clicking the trigger opens the folder modal', async () => {
    renderWithAntd();
    fireEvent.click(screen.getByTestId('composer-add-folder'));
    // Browse-first: the modal opens on the directory browser, and the manual
    // path input is behind a collapsed disclosure.
    expect(await screen.findByTestId('browse-panel-stub')).toBeInTheDocument();
    expect(screen.getByTestId('folder-picker-manual-toggle')).toBeInTheDocument();
    expect(screen.queryByTestId('folder-picker-manual-input')).toBeNull();
  });

  it('expanding the disclosure reveals the manual path input', async () => {
    renderWithAntd();
    fireEvent.click(screen.getByTestId('composer-add-folder'));
    fireEvent.click(await screen.findByTestId('folder-picker-manual-toggle'));
    expect(await screen.findByTestId('folder-picker-manual-input'))
      .toBeInTheDocument();
    expect(screen.getByTestId('folder-picker-manual-submit')).toBeInTheDocument();
  });

  it('typing a path and clicking Add creates the project', async () => {
    renderWithAntd();
    fireEvent.click(screen.getByTestId('composer-add-folder'));
    fireEvent.click(await screen.findByTestId('folder-picker-manual-toggle'));
    const input = await screen.findByTestId('folder-picker-manual-input');
    fireEvent.change(input, { target: { value: 'D:\\my-app' } });
    fireEvent.click(screen.getByTestId('folder-picker-manual-submit'));
    await waitFor(() => {
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/projects', expect.objectContaining({ work_dir: 'D:\\my-app' }),
      );
    });
  });

  it('Enter in the modal input creates the project', async () => {
    renderWithAntd();
    fireEvent.click(screen.getByTestId('composer-add-folder'));
    fireEvent.click(await screen.findByTestId('folder-picker-manual-toggle'));
    const input = await screen.findByTestId('folder-picker-manual-input');
    fireEvent.change(input, { target: { value: '/tmp/enter-app' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => {
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/projects', expect.objectContaining({ work_dir: '/tmp/enter-app' }),
      );
    });
  });

  it('offers the recent folders when there are no projects', () => {
    localStorage.setItem('kairos:recent-folders',
                         JSON.stringify(['/tmp/foo', 'D:\\bar']));
    renderWithAntd();
    // No projects + recents → a Select of the recent folders replaces the
    // plain icon button.
    expect(screen.getByRole('combobox')).toBeInTheDocument();
    expect(screen.queryByTestId('composer-add-folder')).not.toBeInTheDocument();
  });
});
