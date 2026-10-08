/**
 * Tests for the "full access" switch (settings.fullAccess) in the chat
 * composer's action row.
 *
 * What matters here:
 *   - the displayed value is the backend's, never a guess;
 *   - a failed read is visible (not a silent "off");
 *   - switching ON asks once, switching OFF does not;
 *   - a failed write rolls the display back and reports it.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { App, ConfigProvider } from 'antd';

import ChatComposer from '../components/ChatComposer';
import FullAccessToggle from '../components/FullAccessToggle';
import { useChatStore } from '../stores/chatStore';
import api from '../api/client';
import { DARK, LIGHT } from '../styles/theme';

/** Inline style colors come back normalized (hex -> rgb()), so compare like for like. */
const normalized = (color: string): string => {
  const probe = document.createElement('div');
  probe.style.color = color;
  return probe.style.color;
};

// The toggle reports through AntdApp.useApp(), which needs an <App> ancestor.
const Wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <ConfigProvider><App>{children}</App></ConfigProvider>
);

vi.mock('../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

const mockedApi = api as unknown as {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
  delete: ReturnType<typeof vi.fn>;
};

const SWITCH = 'composer-full-access-switch';
const ERROR = 'composer-full-access-error';
const READ_FAILED = /Could not read the full-access state/i;
const WRITE_FAILED = /Could not save the full-access setting/i;

/** Render the toggle with the backend reporting `fullAccess`. */
const renderToggle = async (fullAccess: boolean) => {
  mockedApi.get.mockResolvedValue({ data: { fullAccess } });
  render(<FullAccessToggle />, { wrapper: Wrapper });
  // Wait for the read to settle so every assertion sees a real value.
  await waitFor(() => expect(screen.getByTestId(SWITCH)).not.toBeDisabled());
};

describe('FullAccessToggle', () => {
  beforeEach(() => {
    mockedApi.get.mockReset();
    mockedApi.post.mockReset();
    mockedApi.delete.mockReset();
    useChatStore.setState({ currentProject: null });
  });

  it('renders the switch next to its label', async () => {
    await renderToggle(false);
    expect(screen.getByTestId('composer-full-access')).toBeInTheDocument();
    expect(screen.getByText('Full access')).toBeInTheDocument();
    expect(screen.getByTestId(SWITCH)).toBeInTheDocument();
  });

  it('takes the initial value from the backend (ON)', async () => {
    await renderToggle(true);
    expect(screen.getByTestId(SWITCH)).toBeChecked();
    expect(mockedApi.get).toHaveBeenCalledWith('/projects/settings');
  });

  it('takes the initial value from the backend (OFF)', async () => {
    await renderToggle(false);
    expect(screen.getByTestId(SWITCH)).not.toBeChecked();
  });

  it('renders without a project selected and still shows the real value', async () => {
    useChatStore.setState({ currentProject: null });
    await renderToggle(true);
    expect(screen.getByTestId(SWITCH)).toBeChecked();
    expect(screen.queryByTestId(ERROR)).not.toBeInTheDocument();
  });

  it('shows an error instead of silently reading as OFF when the read fails', async () => {
    mockedApi.get.mockRejectedValue(new Error('backend down'));
    render(<FullAccessToggle />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByTestId(ERROR)).toHaveTextContent(READ_FAILED);
    });
    // Unreadable state must not be presented as a usable "off" switch.
    expect(screen.getByTestId(SWITCH)).toBeDisabled();
    expect(screen.getByTestId(SWITCH)).not.toBeChecked();
  });

  it('switching ON asks once, then writes {fullAccess:true}', async () => {
    await renderToggle(false);
    mockedApi.post.mockResolvedValue({ data: { fullAccess: true } });

    fireEvent.click(screen.getByTestId(SWITCH));

    // The click alone must NOT write — the confirm stands in between.
    expect(mockedApi.post).not.toHaveBeenCalled();
    const ok = await screen.findByRole('button', { name: 'Confirm' });
    fireEvent.click(ok);

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith(
        '/projects/settings', { fullAccess: true });
    });
    expect(screen.getByTestId(SWITCH)).toBeChecked();
  });

  it('cancelling the confirm writes nothing and stays OFF', async () => {
    await renderToggle(false);

    fireEvent.click(screen.getByTestId(SWITCH));
    const cancel = await screen.findByRole('button', { name: 'Cancel' });
    fireEvent.click(cancel);

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Confirm' }))
        .not.toBeInTheDocument();
    });
    expect(mockedApi.post).not.toHaveBeenCalled();
    expect(screen.getByTestId(SWITCH)).not.toBeChecked();
  });

  it('switching OFF writes immediately (no confirmation)', async () => {
    await renderToggle(true);
    mockedApi.post.mockResolvedValue({ data: { fullAccess: false } });

    fireEvent.click(screen.getByTestId(SWITCH));

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith(
        '/projects/settings', { fullAccess: false });
    });
    expect(screen.queryByRole('button', { name: 'Confirm' }))
      .not.toBeInTheDocument();
    expect(screen.getByTestId(SWITCH)).not.toBeChecked();
  });

  it('rolls the display back and reports when the write fails', async () => {
    await renderToggle(false);
    mockedApi.post.mockRejectedValue(new Error('disk full'));

    fireEvent.click(screen.getByTestId(SWITCH));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }));

    await waitFor(() => {
      expect(screen.getByTestId(SWITCH)).not.toBeChecked();
      expect(screen.getByTestId(SWITCH)).not.toBeDisabled();
    });
    expect(screen.getByText(WRITE_FAILED)).toBeInTheDocument();
  });
});

describe('FullAccessToggle in the composer', () => {
  beforeEach(() => {
    mockedApi.get.mockReset();
    mockedApi.post.mockReset();
    mockedApi.delete.mockReset();
    mockedApi.get.mockResolvedValue({ data: { fullAccess: false } });
    useChatStore.setState({ currentProject: null });
  });

  it('sits in the action row to the right of the send button', async () => {
    render(<ChatComposer onSubmit={vi.fn()} />, { wrapper: Wrapper });
    await waitFor(() =>
      expect(screen.getByTestId(SWITCH)).not.toBeDisabled());

    const send = screen.getByTestId('composer-send');
    const toggle = screen.getByTestId('composer-full-access');
    // DOM order: Send, then the full-access switch after it.
    expect(send.compareDocumentPosition(toggle) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    expect(screen.getByTestId('composer-box')).toContainElement(toggle);
  });

  it('renders ON from the backend value (the knob is where antd puts aria-checked=true)', async () => {
    mockedApi.get.mockResolvedValue({ data: { fullAccess: true } });
    render(<FullAccessToggle />, { wrapper: Wrapper });
    const sw = await waitFor(() => {
      const el = screen.getByTestId(SWITCH);
      expect(el).not.toBeDisabled();
      return el;
    });
    // aria-checked is what actually moves the knob: true => slider on the right.
    expect(sw.getAttribute('aria-checked')).toBe('true');
    // A screenshot of an ON state has to be read off this, not off the pixels.
    const c = (screen.getByTestId('composer-full-access-label') as HTMLElement).style.color;
    // The hook returns whichever theme is active; assert against the level, not one palette.
    expect([normalized(LIGHT.labelPrimary), normalized(DARK.labelPrimary)]).toContain(c);
    expect([normalized(LIGHT.labelTertiary), normalized(DARK.labelTertiary)]).not.toContain(c);
  });

  it('keeps the label readable when OFF — never the tertiary hints level', async () => {
    mockedApi.get.mockResolvedValue({ data: { fullAccess: false } });
    render(<FullAccessToggle />, { wrapper: Wrapper });
    await waitFor(() => expect(screen.getByTestId(SWITCH)).not.toBeDisabled());
    expect(screen.getByTestId(SWITCH).getAttribute('aria-checked')).toBe('false');
    // Regression guard: OFF used to use labelTertiary (#8a8f96), which was
    // nearly unreadable next to the send button in the dark theme.
    const c = (screen.getByTestId('composer-full-access-label') as HTMLElement).style.color;
    expect([normalized(LIGHT.labelSecondary), normalized(DARK.labelSecondary)]).toContain(c);
    expect([normalized(LIGHT.labelTertiary), normalized(DARK.labelTertiary)]).not.toContain(c);
  });
});
