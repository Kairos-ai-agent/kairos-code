/**
 * FolderPicker — the "add a folder" modal + the project switcher.
 *
 * Two visible modes:
 *
 *   1. **Standalone** (no `open` / `onClose` props, used by the
 *      chat composer): renders a project switcher. If the chat
 *      store has projects, it's a Select with all of them and the
 *      current one selected. If the store has zero projects (and
 *      the user has no recents in localStorage), it renders a
 *      small icon-only "Add folder" button. The Modal inside
 *      opens when the user picks "Add new folder…" from the
 *      dropdown (or clicks the icon).
 *
 *      **Important**: this mode NEVER renders a button labeled
 *      "Folder" with a folder icon. That used to be a duplicate
 *      of the NewChatButton's "Add folder to start" CTA in the
 *      sidebar (R37 → R38 dedup).
 *
 *   2. **Controlled** (`open` + `onClose` props, used by
 *      NewChatButton): renders ONLY the Modal. The parent
 *      already provides the visible trigger ("Add folder to
 *      start" or the dropdown's "New project from folder…"),
 *      so rendering any extra button here would be a duplicate.
 *
 * R38.6 §25: the Modal is now a **browse-first** experience. The
 * user said "不是填写文档路径，而是直接点击选择本机文件夹" — they
 * want to click a folder, not type a path. The new BrowsePanel
 * component (powered by /api/fs/roots + /api/fs/list) shows a
 * navigable breadcrumb + folder list. The manual Input is
 * collapsed by default but still available as a fallback for
 * edge cases (e.g. typing a path the user just got from a
 * terminal). Recent paths from localStorage are still surfaced
 * inside the manual section for quick re-pick.
 *
 * When a folder is selected we POST `/api/projects` to create
 * a project with name=basename and work_dir=path. The new project
 * becomes the current one and the chat page picks it up.
 */
import React, { useEffect, useState } from 'react';
import { Select, Button, Modal, Input, App as AntdApp, Tooltip, Divider, Collapse } from 'antd';
import { FolderOpenOutlined, PlusOutlined, HistoryOutlined, EditOutlined } from '@ant-design/icons';

import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import api from '../api/client';
import { formatError } from '../utils/formatError';
import type { Project } from '../types';
import BrowsePanel from './BrowsePanel';
import { useT } from '../i18n';

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
   *
   * In controlled mode the component renders ONLY the Modal — no
   * visible button — because the parent already provides a trigger.
   */
  open?: boolean;
  onClose?: () => void;
}

const FolderPicker: React.FC<FolderPickerProps> = ({
  open: openProp, onClose,
}) => {
  const t = useT();
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

  const noProjects = projects.length === 0;
  const showRecents = noProjects && recent.length > 0;
  const isControlled = openProp !== undefined;

  // Computed modal state: prefer the controlled `open` prop when the
  // parent manages the picker (e.g. NewChatButton), fall back to the
  // internal state when the picker is used standalone (the composer
  // switcher).
  const manualOpen = isControlled ? openProp! : internalOpen;
  const closeModal = () => {
    if (onClose) onClose();
    else setInternalOpen(false);
  };

  // Re-evaluate recents when the project list changes. (When a
  // new project is added via the modal, the recents also update.)
  useEffect(() => { setRecent(loadRecent()); }, [projects.length]);

  // R38.6 §25: pickFolder is gone. The old version used
  // ``window.showDirectoryPicker`` which (a) only exists in
  // Chromium-based browsers and (b) CANNOT return the absolute
  // path (browser security model — the handle only gives you
  // ``.name``, not the full path). It was always broken. The
  // new approach is to render the BrowsePanel inside the Modal
  // and let the backend list directories (the backend has full
  // FS access and can return absolute paths).

  const selectFolder = async (path: string, _name: string) => {
    if (!path.trim()) {
      msgApi.warning(t('folderPicker.enterPath'));
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

  // ----- Render decision -----
  //
  // Controlled mode: render ONLY the Modal. The parent already has
  // its own visible trigger ("Add folder to start" in the sidebar or
  // the dropdown's "New project from folder…" item). Rendering any
  // extra button here would duplicate the trigger.
  //
  // Standalone mode: render a project switcher (Select) when projects
  // exist; render a small "Add folder" icon-only button when no
  // projects (with recents → a Select for quick re-pick). The Modal
  // is always present and opens via the switcher / button.

  if (isControlled) {
    return (
      <Modal
        title={t('folderPicker.addTitle')}
        open={manualOpen}
        onCancel={closeModal}
        footer={null}
        destroyOnHidden
        width={560}
      >
        {/* R38.6 §25: browse-first. The user clicks a folder to
            pick it; the manual path Input is collapsed by default
            as a fallback. */}
        <BrowsePanel
          onSelect={(path, name) => {
            setManualPath('');
            selectFolder(path, name);
          }}
          disabled={busy}
        />
        <Collapse
          ghost
          style={{ marginTop: 8 }}
          items={[{
            key: 'manual',
            label: (
              <span data-testid="folder-picker-manual-toggle"
                    style={{ fontSize: 12, color: tokens.labelSecondary }}>
                <EditOutlined /> {t('folderPicker.manualHint')}
              </span>
            ),
            children: (
              <>
                <Input
                  data-testid="folder-picker-manual-input"
                  value={manualPath}
                  onChange={(e) => setManualPath(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter'
                        && manualPath.trim()) {
                      selectFolder(manualPath, basename(manualPath));
                    }
                  }}
                  placeholder={'D:\\projects\\my-app  or  /home/me/projects/my-app'}
                />
                <Button
                  data-testid="folder-picker-manual-submit"
                  type="primary"
                  style={{ marginTop: 8 }}
                  disabled={!manualPath.trim() || busy}
                  loading={busy}
                  onClick={() => selectFolder(manualPath, basename(manualPath))}
                  block
                >
                  {t('folderPicker.usePath')}
                </Button>
                {recent.length > 0 && (
                  <div style={{ marginTop: 8, fontSize: 12,
                                color: tokens.labelTertiary }}>
                    {t('folderPicker.recent')}
                    {recent.slice(0, 5).map((p) => (
                      <a key={p}
                         style={{ marginInlineStart: 8, color: tokens.coderAccent,
                                  cursor: 'pointer' }}
                         onClick={() => setManualPath(p)}>
                        {basename(p)}
                      </a>
                    ))}
                  </div>
                )}
              </>
            ),
          }]}
        />
      </Modal>
    );
  }

  // Standalone: render the switcher / button + the Modal.
  return (
    <>
      {showRecents ? (
        // Standalone + recents + no projects: keep the recents Select
        // (it's useful on a fresh install).
        <Tooltip title={t('folderPicker.pickTitle')}>
          <Select
            value={undefined}
            placeholder={t('folderPicker.pickPlaceholder')}
            style={{ minWidth: 180 }}
            onChange={(value) => {
              if (!value) return;
              // R38.6 §25: opening the modal now starts the
              // browse-first UX, so the old "Browse…" dropdown
              // option is gone. Recent entries still go through
              // selectFolder directly (one-click resume).
              if (value === '__add__') setInternalOpen(true);
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
              { value: '__add__', label: (
                  <span style={{ color: tokens.coderAccent }}>
                    <PlusOutlined /> {t('folderPicker.browse')}
                  </span>
                ) },
            ]}
          />
        </Tooltip>
      ) : projects.length > 0 ? (
        // Standalone + projects exist: project switcher. This is the
        // R37→R38 canonical "composer" affordance.
        <Tooltip title={t('folderPicker.switchTitle')}>
          <Select
            data-testid="composer-project-switcher"
            value={currentProject?.id}
            placeholder={t('folderPicker.selectPlaceholder')}
            style={{ minWidth: 180, maxWidth: 280 }}
            onChange={(id) => {
              const p = projects.find((x) => x.id === id);
              if (p) setCurrentProject(p);
            }}
            popupMatchSelectWidth={false}
            options={[
              ...projects.map((p) => ({
                value: p.id,
                label: (
                  <span style={{
                    display: 'inline-flex', alignItems: 'center', gap: 6,
                  }}>
                    <FolderOpenOutlined style={{ color: tokens.labelTertiary }} />
                    <span style={{
                      fontWeight: p.id === currentProject?.id ? 600 : 400,
                    }}>
                      {p.name || p.id}
                    </span>
                  </span>
                ),
              })),
              { value: '__divider__', label: <Divider style={{ margin: '4px 0' }} />, disabled: true },
              { value: '__add__', label: (
                  <span style={{ color: tokens.coderAccent }}>
                    <PlusOutlined /> {t('folderPicker.addNew')}
                  </span>
                ) },
            ]}
            onSelect={(value) => {
              if (value === '__add__') {
                setInternalOpen(true);
              }
            }}
          />
        </Tooltip>
      ) : (
        // Standalone + no projects + no recents: discrete icon button.
        // No "Folder" text — that was the duplicate.
        <Tooltip title={t('folderPicker.addTitle')}>
          <Button
            data-testid="composer-add-folder"
            icon={<FolderOpenOutlined />}
            onClick={() => setInternalOpen(true)}
            type="text"
            size="small"
            style={{ color: tokens.labelSecondary }}
            aria-label={t('folderPicker.addAria')}
          />
        </Tooltip>
      )}

      <Modal
        title={t('folderPicker.addTitle')}
        open={manualOpen}
        onCancel={closeModal}
        footer={null}
        destroyOnHidden
        width={560}
      >
        {/* R38.6 §25: browse-first UX. The BrowsePanel lets the
            user click through their filesystem. The manual path
            Input is in a collapsed section as a fallback. */}
        <BrowsePanel
          onSelect={(path, name) => {
            setManualPath('');
            selectFolder(path, name);
          }}
          disabled={busy}
        />
        <Collapse
          ghost
          style={{ marginTop: 8 }}
          items={[{
            key: 'manual',
            label: (
              <span data-testid="folder-picker-manual-toggle"
                    style={{ fontSize: 12, color: tokens.labelSecondary }}>
                <EditOutlined /> {t('folderPicker.manualHint')}
              </span>
            ),
            children: (
              <>
                <Input
                  data-testid="folder-picker-manual-input"
                  value={manualPath}
                  onChange={(e) => setManualPath(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter'
                        && manualPath.trim()) {
                      selectFolder(manualPath, basename(manualPath));
                    }
                  }}
                  placeholder={'D:\\projects\\my-app  or  /home/me/projects/my-app'}
                />
                <Button
                  data-testid="folder-picker-manual-submit"
                  type="primary"
                  style={{ marginTop: 8 }}
                  disabled={!manualPath.trim() || busy}
                  loading={busy}
                  onClick={() => selectFolder(manualPath, basename(manualPath))}
                  block
                >
                  {t('folderPicker.usePath')}
                </Button>
                {recent.length > 0 && (
                  <div style={{ marginTop: 8, fontSize: 12,
                                color: tokens.labelTertiary }}>
                    {t('folderPicker.recent')}
                    {recent.slice(0, 5).map((p) => (
                      <a key={p}
                         style={{ marginInlineStart: 8, color: tokens.coderAccent,
                                  cursor: 'pointer' }}
                         onClick={() => setManualPath(p)}>
                        {basename(p)}
                      </a>
                    ))}
                  </div>
                )}
              </>
            ),
          }]}
        />
      </Modal>
    </>
  );
};

export default FolderPicker;

