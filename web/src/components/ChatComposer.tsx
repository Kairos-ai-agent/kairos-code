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
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Tooltip, App as AntdApp } from 'antd';
import {
  ArrowUpOutlined, PaperClipOutlined, ThunderboltOutlined,
  MessageOutlined, RobotOutlined, SettingOutlined,
  CloseOutlined, FileOutlined, LoadingOutlined, AudioOutlined,
} from '@ant-design/icons';

import api from '../api/client';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { useChatStore } from '../stores/chatStore';
import {
  matchSlashCommands, slashTokenAt,
  type SlashCommand, type SlashToken,
} from '../utils/slash';
import { useSettingsStore } from '../stores/settingsStore';
import { classifyIntent } from '../utils/intent';
import { startDictation, isSpeechInputSupported, type Dictation } from '../lib/speechInput';
import FolderPicker from './FolderPicker';
import FullAccessToggle from './FullAccessToggle';
import { useT } from '../i18n';

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
   * Slash commands the backend will actually run (`GET /borrowed/{id}/slash`).
   * Empty or absent means no completion at all — the composer never offers a
   * command it cannot see a handler for.
   */
  slashCommands?: SlashCommand[];
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

/** Quiet time that ends a spoken phrase in voice mode. Long enough to think
 * mid-sentence, short enough that the reply does not feel held back. */
const VOICE_SILENCE_MS = 5000;

function humanSize(n: number): string {
  const val = Number(n) || 0;
  if (val < 1024) return `${val} B`;
  if (val < 1024 * 1024) return `${(val / 1024).toFixed(1)} KB`;
  if (val < 1024 * 1024 * 1024) return `${(val / (1024 * 1024)).toFixed(1)} MB`;
  return `${(val / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

const ChatComposer: React.FC<Props> = ({
  value, onChange, onSubmit, placeholder, busy, disabled, disabledHint,
  slashCommands,
}) => {
  const tokens = useThemeTokens();
  const t = useT();
  const { message: msgApi } = AntdApp.useApp();
  const [text, setText] = useState(value || '');
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  // Slash-command completion. Nothing here invents a command: the list is
  // whatever the backend reports, and only an exact name is ever routed to it.
  const [sug, setSug] = useState<
    { items: SlashCommand[]; index: number; token: SlashToken } | null
  >(null);
  // R38.7: chat attachments. Files are uploaded the moment they are picked
  // (progress visible in the chip row), then their project-relative paths are
  // sent with the message so the Coder can open them with file_read.
  const projectId = useChatStore((s) => s.currentProject?.id || '');
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [uploading, setUploading] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  /* Dictation uses the browser's own recogniser, not the server: sttProvider
     is "mock" and no engine is installed, while the Chromium kernel this
     desktop build ships has one built in. Nothing is uploaded by us -- the
     words arrive as text and land in the box the user is already typing in.
     Hidden entirely where there is no recogniser (Firefox, jsdom).

     With voice mode on it runs hands-free: the mic opens by itself whenever the
     agent is idle, a phrase is submitted once the speaker has been quiet for
     VOICE_SILENCE_MS, and the mic is taken away until the reply is finished --
     otherwise the recogniser would pick up the agent's own voice. */
  const voiceMode = useSettingsStore((s) => s.voice.voiceMode);
  const [dictating, setDictating] = useState(false);
  const dictationRef = useRef<Dictation | null>(null);
  const speechSupported = useRef(isSpeechInputSupported()).current;
  // A refused microphone must not be retried: the effect below would otherwise
  // reopen it on every render and turn one denial into a loop.
  const micRefusedRef = useRef(false);

  const startListening = useCallback(() => {
    if (dictationRef.current) return;
    const session = startDictation({
      language: navigator.language,
      // Hands-free ends a phrase on silence; a mic clicked without voice mode
      // is push-to-talk and has no timer -- stopping is the user's job there.
      silenceMs: voiceMode ? VOICE_SILENCE_MS : 0,
      onText: (transcript) => setText(transcript),
      onSettle: (transcript) => {
        const said = transcript.trim();
        if (!said) return; // quiet, with nothing said: stay where we are
        setText('');
        void submitRef.current(said);
      },
      onEnd: (error) => {
        dictationRef.current = null;
        setDictating(false);
        if (error) micRefusedRef.current = true;
      },
    });
    if (!session) return;
    dictationRef.current = session;
    setDictating(true);
  }, [voiceMode]);

  // The conversation cycle: listen while the agent is idle, hand the mic back
  // the moment it starts working, and reopen it when the reply is done. The
  // user never has to reach for the mouse.
  useEffect(() => {
    if (!speechSupported) return;
    const shouldListen = voiceMode && !busy && !disabled && !micRefusedRef.current;
    if (!shouldListen) {
      dictationRef.current?.stop();
      return;
    }
    if (!dictationRef.current) {
      setText(''); // every round starts from an empty box
      startListening();
    }
  }, [speechSupported, voiceMode, busy, disabled, startListening]);

  const onMicClick = () => {
    if (dictating) {
      // In voice mode the click means "I am done, send it now"; without voice
      // mode it is simply the stop button.
      if (voiceMode) dictationRef.current?.finishNow();
      else dictationRef.current?.stop();
      return;
    }
    setText('');
    startListening();
  };

  useEffect(() => () => { dictationRef.current?.stop(); }, []);
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
      msgApi.warning(t('chat.composer.needProject'));
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
        ? detail : t('chat.composer.uploadFailed'));
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

  // `override` lets the voice loop submit the phrase the recogniser just
  // finished; the box's own text is the normal source.
  const submit = async (override?: string) => {
    const trimmed = (override ?? text).trim();
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

  // The voice loop submits from a recogniser callback that is created once per
  // voice-mode change, so it reads the latest submit through a ref rather than
  // closing over a stale one.
  const submitRef = useRef(submit);
  submitRef.current = submit;

  /** Recompute the completion list for the caret's current position. */
  const refreshSug = (nextText: string, caret: number | null) => {
    const at = caret ?? nextText.length;
    const token = slashTokenAt(nextText, at);
    if (!token || !slashCommands?.length) {
      setSug(null);
      return;
    }
    const items = matchSlashCommands(slashCommands, token.query);
    setSug(items.length ? { items, index: 0, token } : null);
  };

  /** Put the highlighted command in the text, caret just after it. */
  const acceptSug = (choice?: SlashCommand) => {
    if (!sug) return;
    const pick = choice || sug.items[sug.index];
    if (!pick) return;
    const next = `${text.slice(0, sug.token.start)}/${pick.name} `
      + text.slice(sug.token.end);
    setText(next);
    onChange?.(next);
    setSug(null);
    const caret = sug.token.start + pick.name.length + 2;
    window.requestAnimationFrame(() => {
      const el = taRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(caret, caret);
      }
    });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (sug) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const step = e.key === 'ArrowDown' ? 1 : -1;
        setSug({
          ...sug,
          index: (sug.index + step + sug.items.length) % sug.items.length,
        });
        return;
      }
      if (e.key === 'Tab'
          || (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing)) {
        // Accepting the highlighted command. Enter only sends when no list is
        // open, so picking a command never sends a half-typed one instead.
        e.preventDefault();
        acceptSug();
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setSug(null);
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  // The send-button color hints at the auto-classified mode.
  // Yellow = task (loop), brand color = chat (single-turn).
  const sendColor = isTask ? tokens.warning : tokens.labelPrimary;
  const sendTitle = isTask
    ? t('chat.composer.sendAsTask')
    : t('chat.composer.sendAsChat');

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
        maxWidth: 768, margin: '0 auto', position: 'relative',
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
                  aria-label={t('chat.composer.removeAttachment', { name: a.name })}
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
                {t('chat.composer.uploading', { n: uploading })}
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
        {sug && (
          <div
            data-testid="composer-slash-list"
            role="listbox"
            style={{
              position: 'absolute', left: 0, right: 0, bottom: '100%',
              marginBottom: 6, zIndex: 30, overflow: 'hidden',
              background: tokens.bgElevated,
              border: `1px solid ${tokens.border}`,
              borderRadius: 12,
              boxShadow: '0 8px 28px rgba(0,0,0,0.20)',
            }}
          >
            {sug.items.map((c, i) => (
              <div
                key={c.name}
                role="option"
                aria-selected={i === sug.index}
                onMouseEnter={() => setSug({ ...sug, index: i })}
                onMouseDown={(e) => {
                  // mousedown, not click: the textarea must not blur first.
                  e.preventDefault();
                  acceptSug(c);
                }}
                style={{
                  padding: '7px 12px', cursor: 'pointer', fontSize: 13,
                  display: 'flex', gap: 10, alignItems: 'baseline',
                  background: i === sug.index ? tokens.bgLay2 : 'transparent',
                }}
              >
                <code style={{ color: tokens.brand, fontWeight: 600 }}>
                  /{c.name}
                </code>
                <span style={{
                  color: tokens.labelSecondary, fontSize: 12,
                  overflow: 'hidden', textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  {c.help}
                </span>
              </div>
            ))}
          </div>
        )}
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            onChange?.(e.target.value);
            refreshSug(e.target.value, e.target.selectionStart);
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
            ? (disabledHint || t('chat.composer.disabledPlaceholder'))
            : (placeholder || t('chat.composer.placeholder'))}
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
              ? t('chat.composer.intentTaskTip')
              : t('chat.composer.intentChatTip')}
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
              <span>{isTask ? t('chat.composer.intentTask') : t('chat.composer.intentChat')}</span>
            </span>
          </Tooltip>
          <div style={{ flex: 1 }} />
          {speechSupported && (
            <Tooltip
              title={dictating
                ? t('chat.composer.dictateStop')
                : t('chat.composer.dictate')}
            >
              <Button
                type="text"
                icon={<AudioOutlined />}
                onClick={onMicClick}
                disabled={disabled || busy}
                style={{ color: dictating ? tokens.danger : tokens.labelTertiary }}
                data-testid="composer-mic"
                aria-label={dictating
                  ? t('chat.composer.dictateStop')
                  : t('chat.composer.dictate')}
              />
            </Tooltip>
          )}
          <Tooltip
            title={projectId
              ? t('chat.composer.attachHint')
              : t('chat.composer.attachDisabled')}
          >
            <Button
              type="text"
              icon={<PaperClipOutlined />}
              disabled={!projectId || disabled || busy}
              onClick={() => fileInputRef.current?.click()}
              style={{ color: tokens.labelTertiary }}
              data-testid="composer-attach"
              aria-label={t('chat.composer.attach')}
            />
          </Tooltip>
          <Tooltip title={sendTitle}>
            <Button
              type="primary"
              shape="circle"
              icon={isTask ? <ThunderboltOutlined /> : <ArrowUpOutlined />}
              onClick={() => { void submit(); }}
              disabled={disabled || busy || uploading > 0
                        || (!text.trim() && attachments.length === 0)}
              loading={busy}
              style={{
                background: sendColor, color: tokens.bgBase,
                border: 'none',
              }}
              data-testid="composer-send"
              aria-label={t('chat.composer.send')}
            />
          </Tooltip>
          {/* Global "full access" switch (settings.fullAccess) — sits right of
              Send. Turning it on lifts the tool sandbox (one Popconfirm);
              the value is read from / persisted to /projects/settings. */}
          <FullAccessToggle />
          {/* R38: model ID chip on the rightmost of the action row.
              Click to open Settings → LLM Models. The chip shows
              which model the next message will use (the active
              provider's model from settings). */}
          <Tooltip
            title={
              <span>
                {t('chat.composer.modelTipPrefix')}{' '}
                <b>{activeProvider === 'openai' ? 'OpenAI' : 'Anthropic'}</b>{' '}
                · <b>{currentModel}</b>{' '}
                {t('chat.composer.modelTipSuffix')}
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
        {t('chat.composer.footerHint')}
      </div>
    </div>
  );
};

export default ChatComposer;
