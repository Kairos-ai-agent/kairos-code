/**
 * ChatArtifacts — the files an agent turn produced, handed to the user inside
 * the reply it came with.
 *
 * Where this sits: `ChatThread`'s `AssistantBubble` renders
 * `<ChatArtifacts artifacts={message.metadata.artifacts} />` under the reply
 * text. `Chat.tsx` also writes the same array onto the message it appends from
 * the `POST /chat` response, so a card shows the moment the turn returns and is
 * still there after a refresh (the history carries `metadata.artifacts`).
 *
 * Every card is: file name, human-readable size, a download link (the frozen
 * endpoint, URL-encoded — see utils/artifacts.ts), and — for text/markdown
 * only — an inline preview that fails soft.
 *
 * Visual language is the thread's own: the same shell as the sub-agent card
 * (a bordered box with an accent edge), the same neutral tokens, so it reads
 * as part of the conversation in both the dark and the light theme.
 */
import React, { useMemo, useState } from 'react';
import { Button } from 'antd';
import {
  FileOutlined, DownloadOutlined, EyeOutlined, LoadingOutlined,
} from '@ant-design/icons';

import api from '../api/client';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { useT } from '../i18n';
import { useChatStore } from '../stores/chatStore';
import { MONO_STACK } from '../utils/markdown';
import {
  normalizeArtifacts, artifactDownloadUrl, isPreviewable,
  type ChatArtifact,
} from '../utils/artifacts';

/** The preview is a glance, not a reader: clip it to keep the thread light. */
const PREVIEW_MAX_CHARS = 20000;

/** `2.0 KB` — same scale as the composer's attachment chips. */
function humanSize(n: number): string {
  const val = Number(n) || 0;
  if (val < 1024) return `${val} B`;
  if (val < 1024 * 1024) return `${(val / 1024).toFixed(1)} KB`;
  if (val < 1024 * 1024 * 1024) return `${(val / (1024 * 1024)).toFixed(1)} MB`;
  return `${(val / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

const ChatArtifacts: React.FC<{ artifacts?: unknown }> = ({ artifacts }) => {
  const tokens = useThemeTokens();
  const t = useT();
  // The download endpoint is project-scoped and the card needs its id. Read
  // from the store (the same way the Ask banner does) so a bubble does not
  // have to be threaded a projectId through the whole thread.
  const projectId = useChatStore((s) => s.currentProject?.id || '');
  const items = useMemo(() => normalizeArtifacts(artifacts), [artifacts]);

  // Nothing produced (or nothing usable arrived): render nothing at all.
  if (items.length === 0) return null;

  return (
    <div
      data-testid="chat-artifacts"
      style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}
    >
      <div style={{ fontSize: 11, color: tokens.labelTertiary }}>
        {t('chat.artifacts.title', { n: items.length })}
      </div>
      {items.map((a, i) => (
        <ArtifactCard key={`${a.path}-${i}`} artifact={a} projectId={projectId} />
      ))}
    </div>
  );
};

export default ChatArtifacts;

// --------------------------------------------------------------------- card

const ArtifactCard: React.FC<{
  artifact: ChatArtifact;
  /** Empty when no project is selected: the card still shows, without actions. */
  projectId: string;
}> = ({ artifact, projectId }) => {
  const tokens = useThemeTokens();
  const t = useT();
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState('');
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const previewable = isPreviewable(artifact);
  const url = projectId ? artifactDownloadUrl(projectId, artifact.path) : '';

  const toggle = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (state === 'ready' || state === 'loading') return;
    setState('loading');
    try {
      const r = await api.get<string>(
        `/projects/${encodeURIComponent(projectId)}/artifacts/download`,
        {
          params: { path: artifact.path },
          responseType: 'text',
          // The file is not necessarily JSON — hand axios the raw text.
          transformResponse: [(d: unknown) => d],
        },
      );
      setBody(typeof r.data === 'string' ? r.data : String(r.data ?? ''));
      setState('ready');
    } catch {
      // A preview is a convenience; the download link above is the contract.
      // Fail into a short hint, never an error toast or a thrown render.
      setBody('');
      setState('error');
    }
  };

  return (
    <div
      data-testid="chat-artifact"
      data-path={artifact.path}
      style={{
        padding: '6px 9px',
        border: `1px solid ${tokens.border}`,
        borderInlineStart: `3px solid ${tokens.coderAccent ?? tokens.brand}`,
        borderRadius: 8,
        background: tokens.bgLay1,
        fontSize: 12,
        minWidth: 0,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
        <FileOutlined style={{ fontSize: 12, color: tokens.labelTertiary, flexShrink: 0 }} />
        <span
          data-testid="chat-artifact-name"
          title={artifact.path}
          style={{
            fontWeight: 600, color: tokens.labelPrimary, minWidth: 0,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}
        >
          {artifact.name}
        </span>
        <span
          data-testid="chat-artifact-size"
          style={{ color: tokens.labelTertiary, flexShrink: 0 }}
        >
          {humanSize(artifact.size)}
        </span>
        <span style={{ marginInlineStart: 'auto', display: 'inline-flex',
                       alignItems: 'center', gap: 4, flexShrink: 0 }}>
          {previewable && (
            <Button
              type="text"
              size="small"
              data-testid="chat-artifact-preview"
              icon={<EyeOutlined />}
              onClick={() => { void toggle(); }}
              aria-label={open ? t('chat.artifacts.hidePreview') : t('chat.artifacts.preview')}
              style={{ color: tokens.labelTertiary, fontSize: 12 }}
            >
              {open ? t('chat.artifacts.hidePreview') : t('chat.artifacts.preview')}
            </Button>
          )}
          {url && (
            <a
              data-testid="chat-artifact-download"
              href={url}
              download={artifact.name}
              aria-label={t('chat.artifacts.downloadName', { name: artifact.name })}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                padding: '2px 8px', borderRadius: 10,
                background: tokens.bgLay2, color: tokens.labelSecondary,
                border: `1px solid ${tokens.border}`,
                fontSize: 12, textDecoration: 'none', whiteSpace: 'nowrap',
              }}
            >
              <DownloadOutlined style={{ fontSize: 12 }} />
              {t('chat.artifacts.download')}
            </a>
          )}
        </span>
      </div>

      {open && (
        <div data-testid="chat-artifact-preview-body" style={{ marginTop: 6 }}>
          {state === 'loading' && (
            <span style={{ color: tokens.labelTertiary, fontSize: 11 }}>
              <LoadingOutlined spin style={{ marginInlineEnd: 6 }} />
              {t('chat.artifacts.previewLoading')}
            </span>
          )}
          {state === 'error' && (
            <span
              data-testid="chat-artifact-preview-error"
              style={{ color: tokens.labelTertiary, fontSize: 11 }}
            >
              {t('chat.artifacts.previewFailed')}
            </span>
          )}
          {state === 'ready' && (
            <pre
              data-testid="chat-artifact-preview-text"
              style={{
                margin: 0, padding: '6px 8px', borderRadius: 6,
                background: tokens.toolBubble, color: tokens.labelSecondary,
                fontSize: 11, lineHeight: 1.5, fontFamily: MONO_STACK,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                maxHeight: 240, overflow: 'auto',
              }}
            >
              {body.length > PREVIEW_MAX_CHARS
                ? `${body.slice(0, PREVIEW_MAX_CHARS)}…`
                : body}
            </pre>
          )}
        </div>
      )}
    </div>
  );
};
