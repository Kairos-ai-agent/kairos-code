/**
 * PlanPanel — vitest + RTL tests.
 *
 * The panel renders a live TodoWrite-style checklist from the
 * ``plan.updated`` events on the WebSocket. We mock those
 * messages and assert the rendered output.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

// Mock the theme hook so the component doesn't depend on the
// real antd theme provider.
vi.mock('../hooks/useThemeTokens', () => ({
  useThemeTokens: () => ({
    brand: '#1677ff',
    border: '#d9d9d9',
    labelPrimary: '#000',
    labelSecondary: '#666',
    bgLay1: '#fafafa',
  }),
}));

import PlanPanel from '../components/PlanPanel';
import type { Message } from '../types';

function makePlanMsg(
  todos: Array<{
    status: 'pending' | 'in_progress' | 'completed';
    content: string;
    activeForm?: string;
  }>,
  topic = 'plan.updated',
): Message {
  return {
    id: `m-${todos.length}-${todos[0]?.content ?? 'empty'}`,
    sender: 'coder',
    receiver: 'ui',
    topic,
    content: '',
    msg_type: 'text',
    timestamp: 1,
    metadata: { plan: { todos, updated_at: 1 } },
  };
}

describe('PlanPanel', () => {
  it('renders empty state when no plan is present', () => {
    render(<PlanPanel messages={[]} />);
    expect(screen.getByText(/No plan yet/i)).toBeTruthy();
  });

  it('renders all todos with the right status markers', () => {
    const msgs = [
      makePlanMsg([
        { status: 'completed', content: 'Read README' },
        { status: 'in_progress', content: 'Add CSV reader',
          activeForm: 'Adding CSV reader' },
        { status: 'pending', content: 'Run tests' },
      ]),
    ];
    render(<PlanPanel messages={msgs} />);
    // All 3 contents appear
    expect(screen.getByText('Read README')).toBeTruthy();
    expect(screen.getByText('Adding CSV reader')).toBeTruthy();
    expect(screen.getByText('Run tests')).toBeTruthy();
    // Completion tag shows 1/3
    expect(screen.getByText('1/3')).toBeTruthy();
  });

  it('uses the most recent plan when multiple plan events exist', () => {
    const msgs = [
      makePlanMsg([{ status: 'pending', content: 'old todo' }]),
      makePlanMsg([{ status: 'completed', content: 'newest todo' }]),
    ];
    render(<PlanPanel messages={msgs} />);
    // The newest wins
    expect(screen.getByText('newest todo')).toBeTruthy();
    expect(screen.queryByText('old todo')).toBeNull();
  });

  it('ignores non-plan messages', () => {
    const msgs: Message[] = [
      {
        id: '1', sender: 'coder', receiver: 'ui', topic: 'tool.call',
        content: 'doing something', msg_type: 'text', timestamp: 1,
        metadata: { plan: { todos: [{ status: 'pending', content: 'should not show' }] } },
      },
      makePlanMsg([{ status: 'pending', content: 'should show' }]),
    ];
    render(<PlanPanel messages={msgs} />);
    expect(screen.getByText('should show')).toBeTruthy();
    expect(screen.queryByText('should not show')).toBeNull();
  });

  it('compact mode shows a tag with completion count', () => {
    const msgs = [
      makePlanMsg([
        { status: 'completed', content: 'A' },
        { status: 'completed', content: 'B' },
        { status: 'pending', content: 'C' },
      ]),
    ];
    render(<PlanPanel messages={msgs} />);
    // The compact link is rendered as an <a> (clickable).
    const compactLink = screen.getByText('compact');
    fireEvent.click(compactLink);
    // After click, the rendered output collapses to a Tag.
    expect(screen.getByText(/Plan: 2\/3/)).toBeTruthy();
  });
});
