/** Tests for the simplified ChatComposer (no mode selector, Auto by default). */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConfigProvider } from 'antd';

import ChatComposer from '../components/ChatComposer';

describe('ChatComposer (Auto mode)', () => {
  it('does NOT show Loop/Plan/Ask mode selector', () => {
    render(<ChatComposer onSubmit={vi.fn()} />,
           { wrapper: ConfigProvider });
    // The old mode selector used the labels "Loop" / "Plan" / "Ask"
    // — none of them should appear now that the mode is hidden.
    expect(screen.queryByText('Loop')).not.toBeInTheDocument();
    expect(screen.queryByText('Plan')).not.toBeInTheDocument();
    expect(screen.queryByText('Ask')).not.toBeInTheDocument();
  });

  it('shows the Auto-routing hint in the placeholder', () => {
    render(<ChatComposer onSubmit={vi.fn()} />,
           { wrapper: ConfigProvider });
    const ta = screen.getByPlaceholderText(/Auto router/i);
    expect(ta).toBeInTheDocument();
  });

  it('calls onSubmit with the trimmed text when Send is clicked', () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ChatComposer onSubmit={onSubmit} />,
           { wrapper: ConfigProvider });
    const ta = screen.getByPlaceholderText(/Auto router/i);
    fireEvent.change(ta, { target: { value: '  hello world  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(onSubmit).toHaveBeenCalledWith('hello world');
  });

  it('Enter in the textarea submits (no shift)', () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ChatComposer onSubmit={onSubmit} />,
           { wrapper: ConfigProvider });
    const ta = screen.getByPlaceholderText(/Auto router/i);
    fireEvent.change(ta, { target: { value: 'go' } });
    fireEvent.keyDown(ta, { key: 'Enter', shiftKey: false });
    expect(onSubmit).toHaveBeenCalledWith('go');
  });

  it('Shift+Enter inserts a newline (does NOT submit)', () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ChatComposer onSubmit={onSubmit} />,
           { wrapper: ConfigProvider });
    const ta = screen.getByPlaceholderText(/Auto router/i);
    fireEvent.change(ta, { target: { value: 'go' } });
    fireEvent.keyDown(ta, { key: 'Enter', shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('send button is disabled when text is empty', () => {
    render(<ChatComposer onSubmit={vi.fn()} />,
           { wrapper: ConfigProvider });
    const btn = screen.getByRole('button', { name: 'Send' });
    expect(btn).toBeDisabled();
  });

  it('disables the textarea when `disabled` is true', () => {
    render(<ChatComposer onSubmit={vi.fn()} disabled
                       disabledHint="Pick a folder first" />,
           { wrapper: ConfigProvider });
    const ta = screen.getByPlaceholderText(/Pick a folder first/i);
    expect(ta).toBeDisabled();
  });
});
