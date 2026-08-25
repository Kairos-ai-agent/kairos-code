/** Tests for the Tools page (Teams / Cloud / Voice / Computer-use). */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConfigProvider, App as AntdApp } from 'antd';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

// Mock axios to avoid real HTTP. The Tools page calls api.post on
// the Teams and Cloud panels.
vi.mock('../api/client', () => {
  return {
    default: {
      get: vi.fn().mockResolvedValue({ data: { sessions: [], team_id: 't1',
                                              project_id: 'p1', task_count: 0 } }),
      post: vi.fn().mockResolvedValue({ data: { team_id: 't1', project_id: 'p1',
                                                task_count: 3 } }),
    },
    connectWebSocket: vi.fn(),
  };
});

import Tools from '../pages/Tools';

const renderWithRouter = (initialPath: string) => {
  return render(
    <ConfigProvider>
      <AntdApp>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/tools" element={<Tools />} />
            <Route path="/tools/:tool" element={<Tools />} />
          </Routes>
        </MemoryRouter>
      </AntdApp>
    </ConfigProvider>
  );
};

describe('Tools page', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('shows the four tool tiles on the index', () => {
    renderWithRouter('/tools');
    expect(screen.getByText('Agent Teams')).toBeInTheDocument();
    expect(screen.getByText('Cloud Delegation')).toBeInTheDocument();
    expect(screen.getByText('Voice Mode')).toBeInTheDocument();
    expect(screen.getByText('Computer Use')).toBeInTheDocument();
  });

  it('shows the back button on a tool detail', () => {
    renderWithRouter('/tools/teams');
    expect(screen.getByText('Back to tools')).toBeInTheDocument();
    expect(screen.getByText('Agent Teams')).toBeInTheDocument();
  });

  it('teams detail shows the create-team form', () => {
    renderWithRouter('/tools/teams');
    expect(screen.getByText('Tasks (one per line)')).toBeInTheDocument();
    expect(screen.getByText('Max workers')).toBeInTheDocument();
    expect(screen.getByText(/Create team & dispatch/)).toBeInTheDocument();
  });

  it('cloud detail warns about the 503 expected when env is missing', () => {
    renderWithRouter('/tools/cloud');
    expect(screen.getByText(/KAIROS_CLOUD_URL/)).toBeInTheDocument();
  });

  it('voice detail shows the STT/TTS provider cards', () => {
    renderWithRouter('/tools/voice');
    expect(screen.getByText(/STT \(speech → text\)/)).toBeInTheDocument();
    expect(screen.getByText(/TTS \(text → speech\)/)).toBeInTheDocument();
    expect(screen.getByText(/MockSTTProvider/)).toBeInTheDocument();
  });

  it('computer detail flags the auto-confirm safety switch', () => {
    renderWithRouter('/tools/computer');
    expect(screen.getByText(/Mock backend/)).toBeInTheDocument();
    expect(screen.getByText(/Auto-confirm/)).toBeInTheDocument();
  });
});
