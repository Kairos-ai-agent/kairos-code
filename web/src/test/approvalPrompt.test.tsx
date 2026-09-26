/**
 * R2 — the approval prompt: the gate's question, where a person can answer it.
 *
 * `kairos/sentinel.py`'s ASK verdict was resolved by policy until R2, because
 * there was nothing to ask through. Now a tool call waits on a future for up to
 * two minutes. The two ways this screen could fail are opposite and both bad:
 * promising a question when no channel is wired, or swallowing the answer.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConfigProvider, App as AntdApp } from 'antd';

const get = vi.fn();
const post = vi.fn();
vi.mock('../api/client', () => ({
  default: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
  },
}));

import ApprovalPrompt from '../components/ApprovalPrompt';

const renderPrompt = () => render(
  <ConfigProvider>
    <AntdApp>
      <ApprovalPrompt />
    </AntdApp>
  </ConfigProvider>,
);

const pending = {
  wired: true,
  pending: [{
    id: 'req-1',
    tool: 'terminal',
    resource: 'git push origin master',
    reason: 'no standing rule covers this call',
    project_id: 'p1',
    status: 'pending',
  }],
};

describe('ApprovalPrompt (R2)', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it('shows nothing at all when no channel is wired', async () => {
    get.mockResolvedValue({ data: { wired: false, pending: [] } });
    renderPrompt();

    await waitFor(() => expect(get).toHaveBeenCalledWith('/approvals'));
    expect(screen.queryByTestId('approval-prompt')).toBeNull();
  });

  it('shows nothing when there is nothing to answer', async () => {
    get.mockResolvedValue({ data: { wired: true, pending: [] } });
    renderPrompt();

    await waitFor(() => expect(get).toHaveBeenCalledWith('/approvals'));
    expect(screen.queryByTestId('approval-prompt')).toBeNull();
  });

  it('shows the tool and its target, and allows it', async () => {
    get.mockResolvedValue({ data: pending });
    post.mockResolvedValue({ data: { ok: true } });
    renderPrompt();

    await waitFor(() => expect(screen.getByTestId('approval-prompt')).toBeInTheDocument());
    expect(screen.getByTestId('approval-prompt')).toHaveTextContent('terminal');
    expect(screen.getByText('git push origin master')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('approval-allow'));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      '/approvals/req-1', { allow: true, remember: false }));
  });

  it('denies without remembering', async () => {
    get.mockResolvedValue({ data: pending });
    post.mockResolvedValue({ data: { ok: true } });
    renderPrompt();

    await waitFor(() => expect(screen.getByTestId('approval-deny')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('approval-deny'));

    await waitFor(() => expect(post).toHaveBeenCalledWith(
      '/approvals/req-1', { allow: false, remember: false }));
  });

  it('remembers only when the box is ticked and the answer is yes', async () => {
    get.mockResolvedValue({ data: pending });
    post.mockResolvedValue({ data: { ok: true } });
    renderPrompt();

    await waitFor(() => expect(screen.getByTestId('approval-remember')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('approval-remember'));
    fireEvent.click(screen.getByTestId('approval-allow'));

    await waitFor(() => expect(post).toHaveBeenCalledWith(
      '/approvals/req-1', { allow: true, remember: true }));
  });
});
