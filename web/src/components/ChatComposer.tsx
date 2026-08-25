/**
 * ChatComposer — sticky bottom-of-thread input box.
 *
 * Layout (ChatGPT beta style, post-Auto-mode):
 *
 *   ┌──────────────────────────────────────────────────┐
 *   │  ┌────────────────────────────────────┐         │
 *   │  │ Describe a task — the Auto router…  │    ↑   │
 *   │  │                                    │  send  │
 *   │  └────────────────────────────────────┘         │
 *   │  Enter to send · Shift+Enter for new line        │
 *   └──────────────────────────────────────────────────┘
 *
 * Behaviour:
 *   - The Loop / Plan / Ask mode selector is **hidden**. Every
 *     submission goes through the Auto router which the chat page
 *     implements (it decides whether the input triggers a fresh loop
 *     or an ask-answer based on backend state — see Chat.tsx).
 *   - Auto-grow textarea (up to 240px / 12 lines).
 *   - Cmd/Ctrl+Enter or click the send button: submit.
 *   - Enter alone: also submit (chat-style default).
 *   - Shift+Enter: insert a literal newline.
 *   - Disabled when no project/folder is selected, or while a
 *     submission is in flight (`busy`).
 *
 * Rationale for the simplification: the user shouldn't have to pick
 * between Loop / Plan / Ask — the loop already exposes a PlanBanner
 * (the Coder's draft, with approve/reject) and an AskBanner (the
 * Reviewer's question, with an answer input). Both are surfaced in
 * the chat thread at the right moment, so the user never has to
 * declare a mode up front.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Button, Tooltip, App as AntdApp } from 'antd';
import {
  ArrowUpOutlined, PaperClipOutlined,
} from '@ant-design/icons';

import { useThemeTokens } from '../hooks/useThemeTokens';

interface Props {
  value?: string;
  onChange?: (v: string) => void;
  onSubmit: (text: string) => Promise<void> | void;
  placeholder?: string;
  busy?: boolean;
  disabled?: boolean;
  disabledHint?: string;
}

const MAX_TEXTAREA_HEIGHT = 240;

const ChatComposer: React.FC<Props> = ({
  value, onChange, onSubmit, placeholder, busy, disabled, disabledHint,
}) => {
  const tokens = useThemeTokens();
  const [text, setText] = useState(value || '');
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
      await onSubmit(trimmed);
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
            ? (disabledHint || 'Pick a project or folder to start chatting')
            : (placeholder || 'Describe a task — the Auto router picks the right mode.')}
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
