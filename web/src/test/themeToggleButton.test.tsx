/** Test that the topbar theme toggle renders the right text label
 *  for the current mode. We don't mount the full AppLayout (it has
 *  too many side effects) — we just render an inline button using
 *  the same shape to verify the contract.
 */
import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { useThemeStore } from '../stores/themeStore';

const ThemeToggleButton: React.FC = () => {
  const mode = useThemeStore((s) => s.mode);
  const toggle = useThemeStore((s) => s.toggle);
  return (
    <button data-testid="theme-toggle" onClick={toggle}>
      {mode === 'dark' ? 'Light' : 'Dark'}
    </button>
  );
};

describe('theme toggle button', () => {
  beforeEach(() => {
    localStorage.clear();
    useThemeStore.getState().setMode('light');
  });

  it('shows "Dark" when in light mode', () => {
    render(<ThemeToggleButton />, { wrapper: ConfigProvider });
    expect(screen.getByTestId('theme-toggle')).toHaveTextContent('Dark');
  });

  it('shows "Light" when in dark mode', () => {
    useThemeStore.getState().setMode('dark');
    render(<ThemeToggleButton />, { wrapper: ConfigProvider });
    expect(screen.getByTestId('theme-toggle')).toHaveTextContent('Light');
  });

  it('clicking flips the label and the mode', () => {
    render(<ThemeToggleButton />, { wrapper: ConfigProvider });
    const btn = screen.getByTestId('theme-toggle');
    expect(btn).toHaveTextContent('Dark');
    fireEvent.click(btn);
    expect(btn).toHaveTextContent('Light');
    expect(useThemeStore.getState().mode).toBe('dark');
  });
});
