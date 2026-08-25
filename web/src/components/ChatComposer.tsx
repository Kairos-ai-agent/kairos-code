/**
 * ChatComposer — sticky bottom-of-thread input box.
 *
 * Layout (ChatGPT beta style):
 *
 *   ┌──────────────────────────────────────────────────┐
 *   │  [Mode ▼]  [Auto-send ☐]                  📎    │
 *   │  ┌────────────────────────────────────┐    ↑   │
 *   │  │ Describe a task or /plan something…│   send  │
 *   │  │                                    │         │
 *   │  └────────────────────────────────────┘         │
 *   │  Enter to send · Shift+Enter for new line        │
 *   └──────────────────────────────────────────────────┘
 *
 * Behaviour:
 *   - Auto-grow: textarea grows with content up to 240px (12 lines),
 *     then scrolls inside its own box.
 *   - Cmd/Ctrl+Enter or click the send button: submit.
 *   - Enter alone: also submit (chat-style default).
 *   - Shift+Enter: insert a literal newline.
 *   - Disabled when the project is missing or while a submission is
 *     in flight (`busy`).
 */
import React, { useEffect, useRef, useState } from 'react';
import { Button, Select, Tooltip, Switch, Form, Tag } from 'antd';
import {
  ArrowUpOutlined, PaperClipOutlined, ThunderboltOutlined,
  MessageOutlined,
} from '@ant-design/icons';

import { useThemeTokens } from '../hooks/useThemeTokens';

export type ComposerMode = 'loop' | 'plan' | 'ask';

interface Props {
  value?: string;
  onChange?: (v: string) => void;
  onSubmit: (text: string, mode: ComposerMode) => Promise<void> | void;
  placeholder?: string;
  busy?: boolean;
  /** Show the "no project selected" hint and disable the box. */
  disabled?: boolean;
  disabledHint?: string;
  /** Show the model selector (when multiple models available). */
  models?: string[];
  model?: string;
  onModelChange?: (m: string) => void;
}

const MAX_TEXTAREA_HEIGHT = 240;

const ChatComposer: React.FC<Props> = ({
  value, onChange, onSubmit, placeholder, busy,
  disabled, disabledHint, models, model, onModelChange,
}) => {
  const tokens = useThemeTokens();
  const [text, setText] = useState(value || '');
  const [mode, setMode] = useState<ComposerMode>('loop');
  const [autoSend, setAutoSend] = useState(true);
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
      await onSubmit(trimmed, mode);
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

  return (
    <div style={{
      padding: '8px 16px 20px',
      background: 'linear-gradient(to top, ' + tokens.bgBase + ' 60%, transparent 100%)',
    }}>
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
            ? (disabledHint || 'Select a project to start chatting')
            : (placeholder || 'Describe a task — the Coder + Reviewer will iterate.')}
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
          <Select
            value={mode}
            onChange={(v) => setMode(v as ComposerMode)}
            size="small"
            variant="borderless"
            style={{ minWidth: 88 }}
            options={[
              { value: 'loop', label: <span><ThunderboltOutlined /> Loop</span> },
              { value: 'plan', label: <span>📋 Plan</span> },
              { value: 'ask', label: <span>❓ Ask</span> },
            ]}
          />
          <Tooltip title="Auto-send next round on approval">
            <span style={{ display: 'inline-flex', alignItems: 'center',
                           gap: 4, fontSize: 12, color: tokens.labelTertiary }}>
              Auto
              <Switch size="small" checked={autoSend}
                      onChange={setAutoSend} />
            </span>
          </Tooltip>
          {models && models.length > 0 && (
            <Select
              size="small" variant="borderless"
              value={model} onChange={onModelChange}
              style={{ minWidth: 110, marginLeft: 4 }}
              placeholder="Model"
              options={models.map((m) => ({ value: m, label: m }))}
            />
          )}
          <div style={{ flex: 1 }} />
          <Tooltip title="Attach file (coming soon)">
            <Button type="text" icon={<PaperClipOutlined />} disabled
                    style={{ color: tokens.labelTertiary }} />
          </Tooltip>
          <Button
            type="primary"
            shape="circle"
            icon={<ArrowUpOutlined />}
            onClick={submit}
            disabled={disabled || busy || !text.trim()}
            loading={busy}
            style={{
              background: tokens.labelPrimary, color: tokens.bgBase,
              border: 'none',
            }}
            aria-label="Send"
          />
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
