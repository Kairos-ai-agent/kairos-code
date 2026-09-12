/**
 * PlanHistoryPanel — timeline of plan diffs across rounds.
 *
 * Reads the session's ``history`` (each entry may carry a
 * ``plan`` snapshot from R12.2). Renders one row per round with:
 *   - round number + completion bar
 *   - the diff (added / removed / status changed / kept)
 *   - the full todo list at that round
 *
 * Compact by default — the user expands a row to see the
 * per-round details.
 */
import React, { useMemo, useState } from 'react';
import { Tag, Collapse, Progress, Tooltip } from 'antd';
import {
  PlusOutlined, MinusOutlined, ArrowRightOutlined, CheckOutlined,
  CaretRightOutlined, ClockCircleOutlined,
} from '@ant-design/icons';

import { useThemeTokens } from '../hooks/useThemeTokens';
import { tGlobal, useT } from '../i18n';

export interface TodoItem {
  status: 'pending' | 'in_progress' | 'completed';
  content: string;
  activeForm?: string;
}

export interface HistoryEntry {
  round: number;
  plan?: { todos: TodoItem[]; updated_at?: number };
  [key: string]: any;
}

interface Props {
  history: HistoryEntry[];
}

// Local port of plan_diff — keeps the React component
// self-contained (no extra dep on the backend). The logic
// matches kairos.loop.plan.plan_diff.
function planDiff(before: TodoItem[], after: TodoItem[]) {
  const beforeByContent = new Map(before.map((t) => [t.content, t]));
  const afterByContent = new Map(after.map((t) => [t.content, t]));
  const out: Array<Record<string, string>> = [];
  for (const [content, t] of afterByContent) {
    if (!beforeByContent.has(content)) {
      out.push({ op: 'add', content, status: t.status });
    }
  }
  for (const [content, t] of beforeByContent) {
    if (!afterByContent.has(content)) {
      out.push({ op: 'remove', content, status: t.status });
    }
  }
  for (const [content, afterT] of afterByContent) {
    const beforeT = beforeByContent.get(content);
    if (beforeT && beforeT.status !== afterT.status) {
      out.push({
        op: 'status', content,
        from: beforeT.status, to: afterT.status,
      });
    } else if (beforeT) {
      out.push({ op: 'keep', content, status: afterT.status });
    }
  }
  return out;
}

function buildTimeline(history: HistoryEntry[]) {
  const timeline: Array<{
    round: number; todos: TodoItem[]; completion: number;
    diff: Array<Record<string, string>>;
  }> = [];
  let prev: TodoItem[] = [];
  for (const h of history) {
    const plan = h.plan;
    if (!plan || !Array.isArray(plan.todos)) continue;
    const todos: TodoItem[] = plan.todos;
    if (todos.length === 0) { prev = todos; continue; }
    const completed = todos.filter((t) => t.status === 'completed').length;
    timeline.push({
      round: h.round ?? 0,
      todos,
      completion: todos.length === 0 ? 0 : completed / todos.length,
      diff: planDiff(prev, todos),
    });
    prev = todos;
  }
  return timeline;
}

function diffIcon(op: string) {
  if (op === 'add') return <PlusOutlined style={{ color: '#52c41a' }} />;
  if (op === 'remove') return <MinusOutlined style={{ color: '#cf1322' }} />;
  if (op === 'status') return <ArrowRightOutlined style={{ color: '#1677ff' }} />;
  return <CheckOutlined style={{ color: '#bfbfbf' }} />;
}

function diffLabel(d: Record<string, string>): React.ReactNode {
  if (d.op === 'add') return <>{tGlobal('planHistory.diffAdd')} <b>{d.content}</b> <Tag color="default" style={{ marginInlineStart: 4 }}>{d.status}</Tag></>;
  if (d.op === 'remove') return <>{tGlobal('planHistory.diffRemove')} <b style={{ textDecoration: 'line-through' }}>{d.content}</b></>;
  if (d.op === 'status') return <><b>{d.content}</b>: <Tag>{d.from}</Tag> → <Tag color="blue">{d.to}</Tag></>;
  return <span style={{ opacity: 0.6 }}>{d.content} ({d.status})</span>;
}

const PlanHistoryPanel: React.FC<Props> = ({ history }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const timeline = useMemo(() => buildTimeline(history), [history]);
  const [collapsed, setCollapsed] = useState(false);

  if (timeline.length === 0) {
    return (
      <div
        style={{
          padding: '6px 10px',
          border: `1px dashed ${tokens.border}`,
          borderRadius: 6,
          color: tokens.labelSecondary,
          fontSize: 12,
        }}
      >
        {t('planHistory.empty')}
      </div>
    );
  }

  if (collapsed) {
    return (
      <Tooltip title={t('planHistory.expandTitle')}>
        <Tag
          color="cyan"
          onClick={() => setCollapsed(false)}
          style={{ cursor: 'pointer' }}
        >
          <ClockCircleOutlined /> {t('planHistory.label')} {timeline.length} {t('planHistory.roundUnit')}
          {timeline.length === 1 ? '' : 's'}
        </Tag>
      </Tooltip>
    );
  }

  return (
    <div
      style={{
        border: `1px solid ${tokens.border}`,
        borderRadius: 6,
        background: tokens.bgLay1,
        padding: '8px 10px',
        fontSize: 12,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 6,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <ClockCircleOutlined style={{ color: tokens.brand }} />
          <span style={{ fontWeight: 600, color: tokens.labelPrimary }}>
            {t('planHistory.title')}
          </span>
          <Tag color="cyan">{timeline.length}</Tag>
        </div>
        <a
          onClick={() => setCollapsed(true)}
          style={{
            cursor: 'pointer', color: tokens.labelSecondary,
            fontSize: 11, userSelect: 'none',
          }}
        >
          {t('planHistory.compactToggle')}
        </a>
      </div>
      <Collapse
        ghost
        size="small"
        expandIcon={({ isActive }) => (
          <CaretRightOutlined rotate={isActive ? 90 : 0} />
        )}
        items={timeline.map((entry) => ({
          key: `r-${entry.round}`,
          label: (
            <span>
              <Tag color="blue">R{entry.round}</Tag>
              <span style={{ marginInlineEnd: 8 }}>
                {t('planHistory.percentDone', { p: Math.round(entry.completion * 100) })}
              </span>
              <Progress
                percent={Math.round(entry.completion * 100)}
                size="small"
                showInfo={false}
                style={{ width: 100, display: 'inline-block' }}
                strokeColor={tokens.brand}
              />
              <span style={{ marginInlineStart: 8, color: tokens.labelSecondary }}>
                ({entry.todos.length} {t('planHistory.todoUnit')}{entry.todos.length === 1 ? '' : 's'})
              </span>
            </span>
          ),
          children: (
            <div style={{ paddingInlineStart: 8 }}>
              {entry.diff.length === 0 ? (
                <div style={{ color: tokens.labelSecondary, fontStyle: 'italic' }}>
                  {t('planHistory.emptyRound')}
                </div>
              ) : (
                <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                  {entry.diff.map((d, i) => (
                    <li
                      key={i}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        padding: '2px 0',
                      }}
                    >
                      {diffIcon(d.op)}
                      <span>{diffLabel(d)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ),
        }))}
      />
    </div>
  );
};

export default PlanHistoryPanel;
