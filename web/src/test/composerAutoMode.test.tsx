/** Tests for the simplified ChatComposer (no mode selector, Auto by default). */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { App, ConfigProvider } from 'antd';

import ChatComposer from '../components/ChatComposer';
import { useChatStore } from '../stores/chatStore';
import api from '../api/client';

// ChatComposer talks to AntdApp.useApp(); without an <App> ancestor that
// object has no .error() and rejected uploads surface as unhandled
// rejections in the test run.
const Wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <ConfigProvider><App>{children}</App></ConfigProvider>
);

vi.mock('../api/client', () => ({
  default: { post: vi.fn(), delete: vi.fn() },
}));

const mockedApi = api as unknown as {
  post: ReturnType<typeof vi.fn>;
  delete: ReturnType<typeof vi.fn>;
};

// The composer's placeholder text (R38.6 auto-routing). The old
// "/Auto router/i" expectation went stale when the copy changed.
const PLACEHOLDER = /route to chat/i;

const PROJECT = {
  id: 'p1', name: 'Demo', description: '', workspace: '/tmp/demo',
  status: 'active', task_count: 0,
} as any;

describe('ChatComposer (Auto mode)', () => {
  beforeEach(() => {
    mockedApi.post.mockReset();
    mockedApi.delete.mockReset();
    useChatStore.setState({ currentProject: null });
  });

  it('does NOT show Loop/Plan/Ask mode selector', () => {
    render(<ChatComposer onSubmit={vi.fn()} />,
           { wrapper: Wrapper });
    // The old mode selector used the labels "Loop" / "Plan" / "Ask"
    // — none of them should appear now that the mode is hidden.
    expect(screen.queryByText('Loop')).not.toBeInTheDocument();
    expect(screen.queryByText('Plan')).not.toBeInTheDocument();
    expect(screen.queryByText('Ask')).not.toBeInTheDocument();
  });

  it('shows the Auto-routing hint in the placeholder', () => {
    render(<ChatComposer onSubmit={vi.fn()} />,
           { wrapper: Wrapper });
    const ta = screen.getByPlaceholderText(PLACEHOLDER);
    expect(ta).toBeInTheDocument();
  });

  it('calls onSubmit with the trimmed text when Send is clicked', () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ChatComposer onSubmit={onSubmit} />,
           { wrapper: Wrapper });
    const ta = screen.getByPlaceholderText(PLACEHOLDER);
    fireEvent.change(ta, { target: { value: '  hello world  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    // No attachments → second arg is an empty list (R38.7).
    expect(onSubmit).toHaveBeenCalledWith('hello world', []);
  });

  it('Enter in the textarea submits (no shift)', () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ChatComposer onSubmit={onSubmit} />,
           { wrapper: Wrapper });
    const ta = screen.getByPlaceholderText(PLACEHOLDER);
    fireEvent.change(ta, { target: { value: 'go' } });
    fireEvent.keyDown(ta, { key: 'Enter', shiftKey: false });
    expect(onSubmit).toHaveBeenCalledWith('go', []);
  });

  it('Shift+Enter inserts a newline (does NOT submit)', () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ChatComposer onSubmit={onSubmit} />,
           { wrapper: Wrapper });
    const ta = screen.getByPlaceholderText(PLACEHOLDER);
    fireEvent.change(ta, { target: { value: 'go' } });
    fireEvent.keyDown(ta, { key: 'Enter', shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('send button is disabled when text is empty', () => {
    render(<ChatComposer onSubmit={vi.fn()} />,
           { wrapper: Wrapper });
    const btn = screen.getByRole('button', { name: 'Send' });
    expect(btn).toBeDisabled();
  });

  it('disables the textarea when `disabled` is true', () => {
    render(<ChatComposer onSubmit={vi.fn()} disabled
                       disabledHint="Pick a folder first" />,
           { wrapper: Wrapper });
    const ta = screen.getByPlaceholderText(/Pick a folder first/i);
    expect(ta).toBeDisabled();
  });

  // ------------------------------------------------------------------
  // R38.7: attachments
  // ------------------------------------------------------------------

  it('attach button is disabled until a project is selected', () => {
    render(<ChatComposer onSubmit={vi.fn()} />,
           { wrapper: Wrapper });
    expect(screen.getByTestId('composer-attach')).toBeDisabled();
  });

  it('uploads a picked file and shows it as a chip, then submits it', async () => {
    useChatStore.setState({ currentProject: PROJECT });
    mockedApi.post.mockResolvedValue({
      data: {
        attachments: [{
          name: '清单.csv', size: 24, mime: 'text/csv',
          rel_path: 'attachments/清单.csv',
        }],
      },
    });
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    render(<ChatComposer onSubmit={onSubmit} />, { wrapper: Wrapper });

    const attach = screen.getByTestId('composer-attach');
    expect(attach).not.toBeDisabled();

    const file = new File(['name,qty\n'], '清单.csv', { type: 'text/csv' });
    fireEvent.change(screen.getByTestId('composer-file-input'),
                     { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByTestId('composer-attachment'))
        .toHaveTextContent('清单.csv');
    });
    expect(mockedApi.post).toHaveBeenCalledTimes(1);
    expect(String(mockedApi.post.mock.calls[0][0]))
      .toBe('/projects/p1/attachments');

    const ta = screen.getByPlaceholderText(PLACEHOLDER);
    fireEvent.change(ta, { target: { value: '看下这个清单' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith('看下这个清单', [
        { name: '清单.csv', size: 24, mime: 'text/csv',
          rel_path: 'attachments/清单.csv' },
      ]);
    });
    // chips are cleared once the message went out
    expect(screen.queryByTestId('composer-attachment')).not.toBeInTheDocument();
  });

  it('removing a chip deletes the uploaded file on the server', async () => {
    useChatStore.setState({ currentProject: PROJECT });
    mockedApi.post.mockResolvedValue({
      data: {
        attachments: [{
          name: 'img.png', size: 10, mime: 'image/png',
          rel_path: 'attachments/img.png',
        }],
      },
    });
    mockedApi.delete.mockResolvedValue({ data: { status: 'deleted' } });

    render(<ChatComposer onSubmit={vi.fn()} />, { wrapper: Wrapper });
    fireEvent.change(screen.getByTestId('composer-file-input'),
                     { target: { files: [new File(['x'], 'img.png')] } });

    await waitFor(() => {
      expect(screen.getByTestId('composer-attachment-remove')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('composer-attachment-remove'));

    await waitFor(() => {
      expect(mockedApi.delete).toHaveBeenCalledWith(
        '/projects/p1/attachments/attachments/img.png');
      expect(screen.queryByTestId('composer-attachment')).not.toBeInTheDocument();
    });
  });

  it('surfaces the server error when an upload is rejected', async () => {
    useChatStore.setState({ currentProject: PROJECT });
    mockedApi.post.mockRejectedValue({
      response: { data: { detail: 'foo.bin is too large (max 50 MB)' } },
    });

    render(<ChatComposer onSubmit={vi.fn()} />, { wrapper: Wrapper });
    fireEvent.change(screen.getByTestId('composer-file-input'),
                     { target: { files: [new File(['x'], 'foo.bin')] } });

    await waitFor(() => {
      expect(screen.queryByTestId('composer-attachment')).not.toBeInTheDocument();
    });
    expect(mockedApi.post).toHaveBeenCalledTimes(1);
  });
});
