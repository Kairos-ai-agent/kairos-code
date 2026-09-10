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
  CloseOutlined, FileOutlined, LoadingOutlined,
} from '@ant-design/icons';

import api from '../api/client';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { useChatStore } from '../stores/chatStore';
import { useSettingsStore } from '../stores/settingsStore';
import { classifyIntent } from '../utils/intent';
import FolderPicker from './FolderPicker';

/** One uploaded chat attachment (shape returned by the upload endpoint). */
export interface ChatAttachment {
  name: string;
  size: number;
  mime: string;
  /** Path relative to the project root — what the Coder's file tools open. */
  rel_path: string;
}

interface Props {
  value?: string;
  onChange?: (v: string) => void;
  /**
   * Called when the user submits. The intent is auto-classified
   * by the composer (no manual Chat/Task toggle since R38.6).
   * `attachments` carries the files uploaded for this message (R38.7).
   */
  onSubmit: (text: string, attachments: ChatAttachment[]) => Promise<void> | void;
  placeholder?: string;
  busy?: boolean;
  disabled?: boolean;
  disabledHint?: string;
}

const MAX_TEXTAREA_HEIGHT = 240;

function humanSize(n: number): string {
  const val = Number(n) || 0;
  if (val < 1024) return `${val} B`;
  if (val < 1024 * 1024) return `${(val / 1024).toFixed(1)} KB`;
  if (val < 1024 * 1024 * 1024) return `${(val / (1024 * 1024)).toFixed(1)} MB`;
  return `${(val / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

const ChatComposer: React.FC<Props> = ({
  value, onChange, onSubmit, placeholder, busy, disabled, disabledHint,
}) => {
  const tokens = useThemeTokens();
  const { message: msgApi } = AntdApp.useApp();
  const [text, setText] = useState(value || '');
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  // R38.7: chat attachments. Files are uploaded the moment they are picked
  // (progress visible in the chip row), then their project-relative paths are
  // sent with the message so the Coder can open them with file_read.
  const projectId = useChatStore((s) => s.currentProject?.id || '');
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [uploading, setUploading] = useState(0);
  const [dragOver, setDragOver] = useState(false);
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

  // Upload the picked/dropped/pasted files immediately (any format). The
  // backend writes them into <project root>/attachments/ and echoes back the
  // relative path used by the message and by the Coder's file tools.
  const uploadFiles = async (list: FileList | File[] | null | undefined) => {
    const files = Array.from(list || []);
    if (files.length === 0) return;
    if (!projectId) {
      msgApi.warning('先选一个项目或文件夹，再上传附件。');
      return;
    }
    const form = new FormData();
    files.forEach((f) => form.append('files', f, f.name));
    setUploading((n) => n + files.length);
    try {
      const r = await api.post<{ attachments: ChatAttachment[] }>(
        `/projects/${projectId}/attachments`, form);
      const added = r.data?.attachments || [];
      setAttachments((prev) => [...prev, ...added]);
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      msgApi.error(typeof detail === 'string' && detail.trim()
        ? detail : '附件上传失败，请重试。');
    } finally {
      setUploading((n) => Math.max(0, n - files.length));
    }
  };

  const removeAttachment = async (att: ChatAttachment) => {
    const next = attachments.filter((a) => a.rel_path !== att.rel_path);
    setAttachments(next);
    try {
      await api.delete(`/projects/${projectId}/attachments/${att.rel_path}`);
    } catch {
      // Best effort: a stale file on disk is harmless, the chip is gone.
    }
  };

  const submit = async () => {
    const trimmed = text.trim();
    if ((!trimmed && attachments.length === 0) || disabled || busy
        || uploading > 0) return;
    try {
      await onSubmit(trimmed, attachments);
      setText('');
      setAttachments([]);
    } catch {
      // Caller surfaces the error; keep text + attachments so the user can retry.
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
      <div
        data-testid="composer-box"
        onDragOver={(e) => {
          if (e.dataTransfer?.types?.includes('Files')) {
            e.preventDefault();
            setDragOver(true);
          }
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          if (e.dataTransfer?.files?.length) {
            e.preventDefault();
            setDragOver(false);
            uploadFiles(e.dataTransfer.files);
          }
        }}
        style={{
        maxWidth: 768, margin: '0 auto',
        background: tokens.bgLay1,
        border: `1px solid ${dragOver ? tokens.labelPrimary : tokens.borderStrong}`,
        borderRadius: 18,
        padding: '10px 12px',
        transition: 'border-color 0.15s, box-shadow 0.15s',
      }}>
        {/* R38.7: attachment chips — uploaded files (any format) waiting to
            ride along with the next message. */}
        {(attachments.length > 0 || uploading > 0) && (
          <div
            data-testid="composer-attachments"
            style={{
              display: 'flex', flexWrap: 'wrap', gap: 6,
              padding: '2px 4px 8px',
            }}
          >
            {attachments.map((a) => (
              <span
                key={a.rel_path}
                data-testid="composer-attachment"
                title={`${a.rel_path} · ${a.mime}`}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  padding: '2px 8px', borderRadius: 10,
                  background: tokens.bgLay2, color: tokens.labelSecondary,
                  fontSize: 12, maxWidth: 260,
                  border: `1px solid ${tokens.border}`,
                }}
              >
                <FileOutlined style={{ fontSize: 12 }} />
                <span style={{
                  overflow: 'hidden', textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>{a.name}</span>
                <span style={{ opacity: 0.6, flexShrink: 0 }}>
                  {humanSize(a.size)}
                </span>
                <CloseOutlined
                  data-testid="composer-attachment-remove"
                  aria-label={`Remove ${a.name}`}
                  onClick={() => removeAttachment(a)}
                  style={{ fontSize: 10, cursor: 'pointer', flexShrink: 0 }}
                />
              </span>
            ))}
            {uploading > 0 && (
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '2px 8px', borderRadius: 10,
                background: tokens.bgLay2, color: tokens.labelTertiary,
                fontSize: 12,
              }}>
                <LoadingOutlined style={{ fontSize: 12 }} />
                上传中… ({uploading})
              </span>
            )}
          </div>
        )}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          hidden
          data-testid="composer-file-input"
          onChange={(e) => {
            uploadFiles(e.target.files);
            // Reset so picking the same file twice fires onChange again.
            e.target.value = '';
          }}
        />
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            onChange?.(e.target.value);
          }}
          onPaste={(e) => {
            // R38.7: pasting a file (e.g. a screenshot) attaches it instead
            // of dumping "[object File]" into the message.
            const files = Array.from(e.clipboardData?.files || []);
            if (files.length > 0) {
              e.preventDefault();
              uploadFiles(files);
            }
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
          {/* R38.6.4: project picker moved from above the input
              (where it crowded the action row and pushed the
              intent hint into a separate line) to the bottom-left
              of the chat box. The intent preview sits right next
              to it; attach / send / model-chip keep the right
              side. */}
          <div data-testid="composer-folder">
            <FolderPicker />
          </div>
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
          <Tooltip
            title={projectId
              ? 'Attach files — any format. Saved to the project and readable by the Coder.'
              : 'Pick a project or folder first'}
          >
            <Button
              type="text"
              icon={<PaperClipOutlined />}
              disabled={!projectId || disabled || busy}
              onClick={() => fileInputRef.current?.click()}
              style={{ color: tokens.labelTertiary }}
              data-testid="composer-attach"
              aria-label="Attach file"
            />
          </Tooltip>
          <Tooltip title={sendTitle}>
            <Button
              type="primary"
              shape="circle"
              icon={isTask ? <ThunderboltOutlined /> : <ArrowUpOutlined />}
              onClick={submit}
              disabled={disabled || busy || uploading > 0
                        || (!text.trim() && attachments.length === 0)}
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
        Enter to send · Shift+Enter for newline · 📎 拖拽 / 粘贴 / 点回形针上传任意格式文件
      </div>
    </div>
  );
};

export default ChatComposer;
