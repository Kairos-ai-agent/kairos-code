/**
 * ChatComposer — sticky bottom-of-thread input box (R37 update, R38.6).
 *
 * R37 changes (from user feedback):
 *   1. ~~Run-as-task toggle~~ — R38.6 removed the manual toggle.
 *      The agent now auto-classifies the message as chat or task
 *      via `utils/intent.ts` (keyword + question-pattern + code-block
 *      heuristic). The user no longer has to choose. A live-preview
 *      label ("Chat" / "Task") sits in the action row so the user
 *      can see in advance which mode the next message will route to.
 *   2. **FolderPicker above the input** — the project picker is a
 *      small "switch project" pill above the input box.
 *
 * R38 additions:
 *   3. **Model ID chip** on the rightmost of the action row — the
 *      chip shows the active provider's model and clicks through to
 *      Settings → LLM Models.
 *
 * Layout:
 *   ┌─────────────────────────────────────────────────────┐
 *   │  [▼ /path/to/project]   ← switch project   (R37)   │
 *   ├─────────────────────────────────────────────────────┤
 *   │  ┌────────────────────────────────────┐             │
 *   │  │ Type a message — the agent will    │ 📎 ↑  │ gpt-4o ⚙ │
 *   │  │ route to chat or task ...         │ [Chat]  │             │
 *   │  │                                    │             │
 *   │  └────────────────────────────────────┘             │
 *   │  Enter to send · Shift+Enter for newline            │
 *   └─────────────────────────────────────────────────────┘
 */
import React, { useEffect, useRef, useState } from 'react';
import { Button, Tooltip, App as AntdApp } from 'antd';
import {
  ArrowUpOutlined, PaperClipOutlined, ThunderboltOutlined,
  MessageOutlined, RobotOutlined, SettingOutlined,
} from '@ant-design/icons';

import { useThemeTokens } from '../hooks/useThemeTokens';
import { useSettingsStore } from '../stores/settingsStore';
import { classifyIntent } from '../utils/intent';
import FolderPicker from './FolderPicker';

interface Props {
  value?: string;
  onChange?: (v: string) => void;
  /**
   * Called when the user submits. The intent is auto-classified
   * by the composer (no manual Chat/Task toggle since R38.6).
   */
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
  // R38: read the active provider's model from the settings store so
  // we can show it on the action row. The provider switches the
  // model in real-time (the user can change it in Settings → LLM
  // Models without reloading the composer).
  const activeProvider = useSettingsStore((s) => s.provider.active);
  const openaiModel = useSettingsStore((s) => s.provider.openai.model);
  const anthropicModel = useSettingsStore((s) => s.provider.anthropic.model);
  const openSettings = useSettingsStore((s) => s.openDrawer);
  const currentModel = (activeProvider === 'openai' ? openaiModel : anthropicModel)
    || (activeProvider === 'openai' ? 'gpt-4o' : 'claude-3-5-sonnet-latest');

  // R38.6: live-preview the intent classification in the action row
  // hint so the user can see (in advance) which mode their message
  // will route to. Updates on every keystroke.
  const intent = classifyIntent(text);
  const isTask = intent === 'task';

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

  // The send-button color hints at the auto-classified mode.
  // Yellow = task (loop), brand color = chat (single-turn).
  const sendColor = isTask ? tokens.warning : tokens.labelPrimary;
  const sendTitle = isTask
    ? 'Send as task (full Coder ↔ Reviewer loop)'
    : 'Send as chat (single-turn reply)';

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
          · {isTask
            ? 'This message will start the Coder ↔ Reviewer loop'
            : 'This message is a single-turn reply'}
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
            : (placeholder || 'Type a message — the agent will route to '
               + 'chat (single reply) or task (full loop) automatically.')}
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
          {/* R38.6: live-preview of the auto-classified intent. We
              show a small label (with the matching icon) so the
              user can see in advance which mode the next message
              will route to. No toggle — the heuristic decides. */}
          <Tooltip
            title={isTask
              ? 'Auto-classified as task — full Coder ↔ Reviewer loop'
              : 'Auto-classified as chat — single-turn reply'}
            placement="top"
          >
            <span
              data-testid="composer-intent-preview"
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                padding: '2px 8px', borderRadius: 10,
                background: isTask
                  ? tokens.warning + '22' : tokens.bgLay2,
                color: isTask
                  ? tokens.warning : tokens.labelTertiary,
                fontSize: 11, userSelect: 'none',
                transition: 'background 0.15s, color 0.15s',
              }}
            >
              {isTask
                ? <ThunderboltOutlined style={{ fontSize: 12 }} />
                : <MessageOutlined style={{ fontSize: 12 }} />}
              <span>{isTask ? 'Task' : 'Chat'}</span>
            </span>
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
              icon={isTask ? <ThunderboltOutlined /> : <ArrowUpOutlined />}
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
          {/* R38: model ID chip on the rightmost of the action row.
              Click to open Settings → LLM Models. The chip shows
              which model the next message will use (the active
              provider's model from settings). */}
          <Tooltip
            title={
              <span>
                Using <b>{activeProvider === 'openai' ? 'OpenAI' : 'Anthropic'}</b>{' '}
                · <b>{currentModel}</b> — click to change in Settings.
              </span>
            }
            placement="top"
          >
            <button
              type="button"
              data-testid="composer-model-chip"
              onClick={openSettings}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                padding: '3px 8px', borderRadius: 10,
                background: tokens.bgLay2, color: tokens.labelSecondary,
                fontSize: 11, fontWeight: 500,
                border: `1px solid ${tokens.border}`,
                cursor: 'pointer', userSelect: 'none',
                maxWidth: 200, overflow: 'hidden',
                whiteSpace: 'nowrap', textOverflow: 'ellipsis',
                transition: 'background 0.12s, color 0.12s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = tokens.bgLay1;
                e.currentTarget.style.color = tokens.labelPrimary;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = tokens.bgLay2;
                e.currentTarget.style.color = tokens.labelSecondary;
              }}
            >
              <RobotOutlined style={{ fontSize: 11 }} />
              <span style={{
                overflow: 'hidden', textOverflow: 'ellipsis',
                whiteSpace: 'nowrap', minWidth: 0,
              }}>
                {currentModel}
              </span>
              <SettingOutlined style={{ fontSize: 10, opacity: 0.6 }} />
            </button>
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
