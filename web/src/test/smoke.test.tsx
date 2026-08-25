/** Smoke test — verifies Vitest + RTL + jsdom are wired correctly. */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';

const Probe: React.FC<{ text: string }> = ({ text }) => (
  <div data-testid="probe">{text}</div>
);

describe('vitest setup', () => {
  it('renders a component with @testing-library/react', () => {
    render(<Probe text="hello" />, { wrapper: ConfigProvider });
    expect(screen.getByTestId('probe')).toHaveTextContent('hello');
    expect(screen.getByTestId('probe')).toBeInTheDocument();
  });

  it('jsdom has window.matchMedia polyfill (from setup.ts)', () => {
    expect(window.matchMedia).toBeDefined();
    expect(window.matchMedia('(prefers-color-scheme: dark)').matches).toBe(false);
  });
});
