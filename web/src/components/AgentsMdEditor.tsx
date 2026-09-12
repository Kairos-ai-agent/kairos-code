/**
 * AgentsMdEditor — the project memory editor (R38.6 §29).
 *
 * The project-scoped AGENTS.md is the single source of truth
 * for "memory that survives across sessions." The backend
 * already injects it into every agent's system_prompt (see
 * ``kairos/agents_md.py``), so this editor is purely a UX
 * layer for the user.
 *
 * The editor is a Modal that opens from a small "Memory"
 * button in the WorkbenchPanel header (next to the collapse
 * toggle). The body is a TextArea pre-filled with the current
 * AGENTS.md content (or the built-in fallback template if
 * the project hasn't created one yet). Save → PUT to the
 * backend; the next agent dispatch will see the new content.
 */
import React, { useEffect, useState, useCallback } from 'react';
import {
  Modal, Input, Button, Space, Alert, message, Spin,
  Tag, Typography,
} from 'antd';
import { BookOutlined, SaveOutlined, ReloadOutlined } from '@ant-design/icons';

import api from '../api/client';
import { formatError } from '../utils/formatError';
import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { useT } from '../i18n';

const { Text, Paragraph } = Typography;

interface AgentsMdResponse {
  project_id: string;
  path: string;
  source: 'project' | 'global' | 'fallback' | 'missing';
  content: string;
  bytes: number;
  modified: number | null;
}

interface AgentsMdEditorProps {
  /** If true, the modal is shown on mount. */
  defaultOpen?: boolean;
  /** Called when the user saves (so the parent can hide the
   * workbench-tab "agent thinking" indicator if it has one). */
  onSaved?: (bytes: number) => void;
}

const AgentsMdEditor: React.FC<AgentsMdEditorProps> = ({
  defaultOpen = false,
  onSaved,
}) => {
  const t = useT();
  const tokens = useThemeTokens();
  const currentProject = useChatStore((s) => s.currentProject);
  const [open, setOpen] = useState(defaultOpen);
  const [content, setContent] = useState('');
  const [original, setOriginal] = useState('');
  const [path, setPath] = useState('');
  const [source, setSource] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msgApi, contextHolder] = message.useMessage();

  const load = useCallback(async () => {
    if (!currentProject) return;
    setLoading(true);
    setError(null);
    try {
      const r = await api.get<AgentsMdResponse>(
        '/agents-md', { params: { project_id: currentProject.id } },
      );
      setContent(r.data.content);
      setOriginal(r.data.content);
      setPath(r.data.path);
      setSource(r.data.source);
    } catch (e: any) {
      setError(formatError(e, 'load failed'));
    } finally {
      setLoading(false);
    }
  }, [currentProject]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const save = async () => {
    if (!currentProject) return;
    setSaving(true);
    setError(null);
    try {
      const r = await api.put<{ path: string; bytes: number }>(
        '/agents-md', null,
        { params: { project_id: currentProject.id },
          data: { content } },
      );
      setOriginal(content);
      setPath(r.data.path);
      setSource('project');
      msgApi.success(
        r.data.bytes === 0
          ? 'AGENTS.md cleared (reverted to built-in fallback)'
          : `Saved AGENTS.md (${r.data.bytes} bytes)`,
      );
      onSaved?.(r.data.bytes);
    } catch (e: any) {
      setError(formatError(e, 'save failed'));
    } finally {
      setSaving(false);
    }
  };

  const reset = () => setContent(original);

  const insertTemplate = async () => {
    try {
      const r = await api.get<{ template: string; bytes: number }>(
        '/agents-md/template',
      );
      setContent(r.data.template);
    } catch (e: any) {
      msgApi.error(t('agentsMd.loadTemplateFailed'));
    }
  };

  if (!currentProject) {
    return (
      <Button
        size="small" type="text" icon={<BookOutlined />}
        disabled
        data-testid="agents-md-button"
      >
        {t('agentsMd.title')}
      </Button>
    );
  }

  const dirty = content !== original;
  const sourceLabel = source === 'project' ? 'project file'
                   : source === 'global' ? 'global ~/.kairos/AGENTS.md'
                   : source === 'fallback' ? 'built-in fallback (unsaved)'
                   : source;
  const sourceColor = source === 'project' ? 'green'
                    : source === 'global' ? 'blue'
                    : source === 'fallback' ? 'orange'
                    : 'default';

  return (
    <>
      {contextHolder}
      <Button
        size="small" type="text" icon={<BookOutlined />}
        onClick={() => setOpen(true)}
        data-testid="agents-md-button"
        title={t('agentsMd.editTitle')}
      >
        {t('agentsMd.title')}
      </Button>
      <Modal
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        width={760}
        title={
          <Space>
            <BookOutlined />
            <span>{t('agentsMd.header')}</span>
            <Tag color={sourceColor} data-testid="agents-md-source-tag">
              {sourceLabel}
            </Tag>
            {path && path !== '(built-in fallback)' && (
              <Text type="secondary" style={{ fontSize: 11 }}>
                {path}
              </Text>
            )}
          </Space>
        }
        destroyOnHidden={false}
      >
        <Paragraph style={{ fontSize: 12, color: tokens.labelSecondary,
                            marginBottom: 12 }}>
          {t('agentsMd.description')}
        </Paragraph>
        {error && (
          <Alert type="error" message={error} showIcon
                 style={{ marginBottom: 8 }} />
        )}
        {loading ? (
          <div style={{ textAlign: 'center', padding: 24 }}>
            <Spin />
          </div>
        ) : (
          <Input.TextArea
            data-testid="agents-md-textarea"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            autoSize={{ minRows: 14, maxRows: 28 }}
            style={{
              fontFamily: 'monospace', fontSize: 12,
              background: tokens.bgLay1,
            }}
          />
        )}
        <div style={{
          marginTop: 12,
          display: 'flex', alignItems: 'center', gap: 8,
          flexWrap: 'wrap',
        }}>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            onClick={save}
            loading={saving}
            disabled={!dirty || loading}
            data-testid="agents-md-save"
          >
            {dirty ? 'Save' : 'Saved'}
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={reset}
            disabled={!dirty || saving}
          >
            {t('agentsMd.discard')}
          </Button>
          <Button onClick={insertTemplate} disabled={saving || loading}
                  data-testid="agents-md-insert-template">
            {t('agentsMd.insertTemplate')}
          </Button>
          <div style={{ flex: 1 }} />
          <Text type="secondary" style={{ fontSize: 11 }}>
            {t('agentsMd.stats', { chars: content.length, bytes: new Blob([content]).size })}
          </Text>
        </div>
      </Modal>
    </>
  );
};

export default AgentsMdEditor;

