/**
 * ChatComposer — sticky bottom-of-thread input box (R37 update).
 *
 * R37 changes (from user feedback):
 *   1. **Run-as-task toggle** — the composer now has a small toggle
 *      next to the send button. OFF (default): the message is sent
 *      as a single-turn chat to the Coder (no loop kicked off). ON:
 *      the message kicks off the full Coder <-> Reviewer loop. This
 *      fixes the "every conversation becomes a task" issue —
 *      casual questions stay as chat; only the messages the user
 *      explicitly tags as tasks spawn a loop.
 *   2. **FolderPicker above the input** — the user wanted the
 *      project picker near the chat input instead of in the topbar.
 *      We render it as a small "switch project" pill above the
 *      input box (the topbar still has one too for quick access).
 *
 * Layout:
 *   ┌─────────────────────────────────────────────┐
 *   │  [▼ /path/to/project]   ← switch project   │  ← R37
 *   ├─────────────────────────────────────────────┤
 *   │  ┌────────────────────────────────────┐     │
 *   │  │ Describe a task — the Auto router…  │ ☐ │  ← R37: Run as task
 *   │  │                                    │   │
 *   │  └────────────────────────────────────┘ ↑  │
 *   │  Enter to send · Shift+Enter for newline  │
 *   └─────────────────────────────────────────────┘
 */
import React, { useEffect, useRef, useState } from 'react';
import { Button, Tooltip, App as AntdApp, Switch } from 'antd';
import {
  ArrowUpOutlined, PaperClipOutlined, ThunderboltOutlined,
  MessageOutlined,
} from '@ant-design/icons';

import { useThemeTokens } from '../hooks/useThemeTokens';
import FolderPicker from './FolderPicker';

interface Props {
  value?: string;
  onChange?: (v: string) => void;
  /**
   * Called when the user submits. The boolean is the new
   * ``runAsTask`` toggle state: ``true`` means the user wants
   * the full loop, ``false`` means a single-turn chat.
   */
  onSubmit: (text: string, runAsTask: boolean) => Promise<void> | void;
  placeholder?: string;
  busy?: boolean;
  disabled?: boolean;
  disabledHint?: string;
  /** Initial value for the runAsTask toggle. */
  defaultRunAsTask?: boolean;
}

const MAX_TEXTAREA_HEIGHT = 240;

const ChatComposer: React.FC<Props> = ({
  value, onChange, onSubmit, placeholder, busy, disabled, disabledHint,
  defaultRunAsTask = false,
}) => {
  const tokens = useThemeTokens();
  const [text, setText] = useState(value || '');
  const [runAsTask, setRunAsTask] = useState<boolean>(defaultRunAsTask);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  // Sync external value updates (e.g. parent resetting after submit).
  useEffect(() => {
    if (value !== undefined && value !== text) setText(value);
  }, [value]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-grow the textarea to fit content.
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const next = Math.min(MAX_TEXTAREA_HEIGHT, el.scrollHeight);
    el.style.height = `${next}px`;
  }, [text]);

  const submit = async () => {
    const trimmed = text.trim();
    if (!trimmed || disabled || busy) return;
    try {
      await onSubmit(trimmed, runAsTask);
      setText('');
    } catch {
      // Caller surfaces the error; keep the text so the user can retry.
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  // Compute the send-button color depending on mode:
  //   - chat mode (toggle off) → brand color
  //   - task mode (toggle on)  → a "thunder" color so the user can
  //     see at a glance "this is going to spin up the loop"
  const sendColor = runAsTask ? tokens.warning : tokens.labelPrimary;
  const sendTitle = runAsTask
    ? 'Send as task (start the Coder <-> Reviewer loop)'
    : 'Send as chat (single-turn, no loop)';

  return (
    <div style={{
      padding: '8px 16px 20px',
      background: 'linear-gradient(to top, ' + tokens.bgBase + ' 60%, transparent 100%)',
    }}>
      {/* R37: project picker above the input so the user can switch
          projects without scrolling back to the top. */}
      <div
        data-testid="composer-folder"
        style={{
          maxWidth: 768, margin: '0 auto 6px',
          display: 'flex', alignItems: 'center', gap: 8,
          fontSize: 12, color: tokens.labelTertiary,
        }}
      >
        <FolderPicker />
        <span style={{ opacity: 0.6 }}>
          · {runAsTask
            ? 'Tasks start the Coder ↔ Reviewer loop'
            : 'Chat is single-turn (no loop)'}
        </span>
      </div>

      <div style={{
        maxWidth: 768, margin: '0 auto',
        background: tokens.bgLay1,
        border: `1px solid ${tokens.borderStrong}`,
        borderRadius: 18,
        padding: '10px 12px',
        transition: 'border-color 0.15s, box-shadow 0.15s',
      }}>
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            onChange?.(e.target.value);
          }}
          onKeyDown={onKeyDown}
          rows={1}
          disabled={disabled || busy}
          placeholder={disabled
            ? (disabledHint || 'Pick a project or folder to start chatting')
            : (placeholder || (runAsTask
                ? 'Describe a task — the loop will run until approved.'
                : 'Ask anything — single chat reply, no loop.'))}
          style={{
            width: '100%', border: 'none', outline: 'none',
            background: 'transparent', color: tokens.labelPrimary,
            resize: 'none', fontSize: 15, lineHeight: 1.5,
            fontFamily: 'inherit',
            maxHeight: MAX_TEXTAREA_HEIGHT,
            minHeight: 24, padding: '6px 8px',
          }}
        />
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          paddingTop: 4,
        }}>
          {/* R37: Run-as-task toggle on the left of the action row */}
          <Tooltip
            title={runAsTask
              ? 'Currently: Task mode. Click to switch to Chat (no loop).'
              : 'Currently: Chat mode. Click to switch to Task (run the loop).'}
            placement="top"
          >
            <label
              data-testid="run-as-task-toggle"
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                padding: '2px 8px', borderRadius: 10,
                background: runAsTask ? tokens.warning + '22' : 'transparent',
                color: runAsTask ? tokens.warning : tokens.labelTertiary,
                fontSize: 11, cursor: 'pointer', userSelect: 'none',
                transition: 'background 0.15s, color 0.15s',
              }}
              onClick={() => setRunAsTask((v) => !v)}
              role="button"
            >
              {runAsTask
                ? <ThunderboltOutlined style={{ fontSize: 12 }} />
                : <MessageOutlined style={{ fontSize: 12 }} />}
              <span>{runAsTask ? 'Task' : 'Chat'}</span>
              <Switch
                size="small"
                checked={runAsTask}
                onChange={(checked) => setRunAsTask(checked)}
                onClick={(_, e) => e.stopPropagation()}
                style={{ marginLeft: 2 }}
              />
            </label>
          </Tooltip>
          <div style={{ flex: 1 }} />
          <Tooltip title="Attach file (coming soon)">
            <Button type="text" icon={<PaperClipOutlined />} disabled
                    style={{ color: tokens.labelTertiary }} />
          </Tooltip>
          <Tooltip title={sendTitle}>
            <Button
              type="primary"
              shape="circle"
              icon={runAsTask ? <ThunderboltOutlined /> : <ArrowUpOutlined />}
              onClick={submit}
              disabled={disabled || busy || !text.trim()}
              loading={busy}
              style={{
                background: sendColor, color: tokens.bgBase,
                border: 'none',
              }}
              data-testid="composer-send"
              aria-label="Send"
            />
          </Tooltip>
        </div>
      </div>
      <div style={{
        maxWidth: 768, margin: '6px auto 0',
        fontSize: 11, color: tokens.labelTertiary,
        textAlign: 'center',
      }}>
        Enter to send · Shift+Enter for newline
      </div>
    </div>
  );
};

export default ChatComposer;
