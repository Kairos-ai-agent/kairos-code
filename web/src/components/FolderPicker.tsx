/**
 * FolderPicker — topbar control for "chat about a folder without
 * creating a project up front".
 *
 * Three ways to pick a folder:
 *   1. Native directory picker (Chromium-based browsers via the
 *      File System Access API — `window.showDirectoryPicker`). On
 *      pick we get a real absolute path.
 *   2. Manual path entry (a Modal with an Input). Fallback for
 *      browsers without the FS Access API.
 *   3. Recent paths dropdown — last 5 paths the user picked, kept
 *      in localStorage.
 *
 * When a folder is selected we POST `/api/projects` to create a
 * project with name=basename and work_dir=path. The new project
 * becomes the current one and the chat page picks it up.
 */
import React, { useEffect, useState } from 'react';
import { Select, Button, Modal, Input, App as AntdApp, Tooltip } from 'antd';
import { FolderOpenOutlined, PlusOutlined, HistoryOutlined } from '@ant-design/icons';

import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import api from '../api/client';
import type { Project } from '../types';

const RECENT_KEY = 'kairos:recent-folders';
const MAX_RECENT = 5;

function loadRecent(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const list = JSON.parse(raw);
    return Array.isArray(list) ? list.filter((x) => typeof x === 'string') : [];
  } catch { return []; }
}
function saveRecent(list: string[]): void {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, MAX_RECENT))); }
  catch { /* ignore */ }
}

function basename(p: string): string {
  // Strip trailing slashes, then take the last segment. Works for
  // both Windows (C:\foo\bar) and POSIX (/foo/bar) paths.
  return p.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || p;
}

interface FolderPickerProps {
  /**
   * If set, the picker is fully controlled: `open` toggles the modal,
   * and `onClose` is called when the user dismisses it. Used by
   * the "New chat" button in the sidebar (see NewChatButton).
   * If undefined, the picker manages its own internal state.
   */
  open?: boolean;
  onClose?: () => void;
}

const FolderPicker: React.FC<FolderPickerProps> = ({ open: openProp, onClose }) => {
  const tokens = useThemeTokens();
  const { message: msgApi } = AntdApp.useApp();
  const projects = useChatStore((s) => s.projects);
  const setProjects = useChatStore((s) => s.setProjects);
  const setCurrentProject = useChatStore((s) => s.setCurrentProject);
  const currentProject = useChatStore((s) => s.currentProject);
  const [recent, setRecent] = useState<string[]>(loadRecent);
  const [internalOpen, setInternalOpen] = useState(false);
  const [manualPath, setManualPath] = useState('');
  const [busy, setBusy] = useState(false);

  // Auto-show the recent list if the project list is empty AND we
  // have any recents. Helps the user resume work.
  const noProjects = projects.length === 0;
  const showRecents = noProjects && recent.length > 0;

  // Computed modal state: prefer the controlled `open` prop when the
  // parent manages the picker (e.g. NewChatButton), fall back to the
  // internal state when the picker is used standalone (the topbar
  // button).
  const manualOpen = openProp !== undefined ? openProp : internalOpen;
  const closeModal = () => {
    if (onClose) onClose();
    else setInternalOpen(false);
  };

  const pickFolder = async () => {
    // Try the FS Access API first; fall back to the manual modal.
    const w = window as any;
    if (typeof w.showDirectoryPicker === 'function') {
      try {
        const handle = await w.showDirectoryPicker();
        await selectFolder(handle.name ? basename(handle.name) : basename(handle.name)
                          || 'folder', '');
        // The handle doesn't give us a path on most browsers
        // (privacy), so we ask the user to type/paste the absolute
        // path. The dialog title and the user's mental model is
        // enough; the chat composer needs an absolute path to mount
        // the work_dir on disk.
        // (We could also POST a tiny probe to /api/ping to confirm
        // the path exists, but the backend will 404 anyway if it
        // doesn't.)
        setManualPath('');
        if (onClose) onClose();
        else setInternalOpen(true);
        return;
      } catch (e: any) {
        if (e?.name === 'AbortError') return;  // user cancelled
      }
    }
    setManualPath('');
    if (onClose) onClose();
    else setInternalOpen(true);
  };

  const selectFolder = async (path: string, _name: string) => {
    if (!path.trim()) {
      msgApi.warning('Enter a folder path.');
      return;
    }
    setBusy(true);
    try {
      const r = await api.post<Project>('/projects', {
        name: basename(path),
        description: path,
        work_dir: path,
      });
      // Update the project list. The new project has the basename
      // as its name; switch to it immediately.
      setProjects([...projects, r.data]);
      setCurrentProject(r.data);
      // Update recent.
      const next = [path, ...recent.filter((p) => p !== path)].slice(0, MAX_RECENT);
      setRecent(next);
      saveRecent(next);
      closeModal();
      msgApi.success(`Folder "${basename(path)}" added as project.`);
    } catch (e: any) {
      const detail = e?.response?.data?.detail || 'Failed to add folder';
      msgApi.error(detail);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {showRecents ? (
        <Tooltip title="Pick a recent folder or browse">
          <Select
            value={currentProject?.id ? '__has_project__' : undefined}
            placeholder="Pick a folder to start…"
            style={{ minWidth: 180 }}
            onChange={(value) => {
              if (value === '__browse__') pickFolder();
              else if (value === '__manual__') {
                if (onClose) onClose();
                else setInternalOpen(true);
              }
              else selectFolder(value, basename(value));
            }}
            options={[
              ...recent.map((p) => ({
                value: p, label: <span style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                }}>
                  <HistoryOutlined style={{ color: tokens.labelTertiary }} />
                  {basename(p)} <span style={{ color: tokens.labelTertiary,
                                                 fontSize: 11 }}>· {p}</span>
                </span>,
              })),
              { value: '__manual__', label: <span><Input size="small" />Type a path…</span> },
              { value: '__browse__', label: <span><PlusOutlined /> Browse…</span> },
            ]}
          />
        </Tooltip>
      ) : (
        <Tooltip title="Add a folder workspace">
          <Button
            icon={<FolderOpenOutlined />}
            onClick={pickFolder}
            type="text"
            style={{ color: tokens.labelSecondary }}
          >
            Folder
          </Button>
        </Tooltip>
      )}

      <Modal
        title="Add a folder workspace"
        open={manualOpen}
        onCancel={closeModal}
        onOk={() => selectFolder(manualPath, basename(manualPath))}
        confirmLoading={busy}
        okText="Add"
        destroyOnClose
      >
        <p style={{ color: tokens.labelSecondary }}>
          Enter an absolute path. Kairos will create a project with this
          folder as the work directory so the Coder can read / write
          files inside it.
        </p>
        <Input
          autoFocus
          value={manualPath}
          onChange={(e) => setManualPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') selectFolder(manualPath, basename(manualPath));
          }}
          placeholder={'D:\\projects\\my-app  or  /home/me/projects/my-app'}
        />
        {recent.length > 0 && (
          <div style={{ marginTop: 8, fontSize: 12,
                        color: tokens.labelTertiary }}>
            Recent:
            {recent.slice(0, 5).map((p) => (
              <a key={p} style={{ marginLeft: 8, color: tokens.coderAccent,
                                  cursor: 'pointer' }}
                 onClick={() => setManualPath(p)}>
                {basename(p)}
              </a>
            ))}
          </div>
        )}
      </Modal>
    </>
  );
};

export default FolderPicker;
