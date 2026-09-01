/**
 * BrowsePanel — the "click to pick a folder" UX for the FolderPicker
 * modal.
 *
 * R38.6 §25: the user asked for a click-first experience, not a
 * "type the path" one. The browser's ``window.showDirectoryPicker``
 * can't return the absolute path (browser security model), so the
 * real way to deliver this is to ask the *backend* (which has full
 * FS access) to list directories. The frontend renders the result
 * as a navigable breadcrumb + folder list.
 *
 * Layout:
 *
 *   ┌─ Breadcrumb: ~ › projects › my-app ──────────────────┐
 *   ├──────────────────────────────────────────────────────┤
 *   │  📁 subfolder-1                                       │
 *   │  📁 subfolder-2                              >        │
 *   │  📁 another-folder                           >        │
 *   │  ...                                                  │
 *   ├──────────────────────────────────────────────────────┤
 *   │                       [ Use this folder ]            │
 *   └──────────────────────────────────────────────────────┘
 *
 * Click a row to navigate into it. Click "Use this folder" to
 * commit the current path. The roots (home, workspace_dir, drives
 * on Windows) are loaded once on mount and shown as the initial
 * breadcrumb destination.
 */
import React, { useEffect, useState } from 'react';
import { Breadcrumb, Button, List, Spin, Alert, Empty } from 'antd';
import { FolderOpenOutlined, RightOutlined, HomeOutlined } from '@ant-design/icons';

import api from '../api/client';
import { formatError } from '../utils/formatError';
import { useThemeTokens } from '../hooks/useThemeTokens';

export interface FsEntry {
  name: string;
  path: string;
  is_dir: boolean;
  has_children?: boolean;
}

interface BrowsePanelProps {
  /** Called when the user picks a folder. ``path`` is the absolute
   * path; ``name`` is the basename (used as the project name). */
  onSelect: (path: string, name: string) => void;
  /** Disable navigation while the parent is busy adding the
   * project (so the user can't double-submit). */
  disabled?: boolean;
}

function basename(p: string): string {
  return p.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || p;
}

const BrowsePanel: React.FC<BrowsePanelProps> = ({ onSelect, disabled }) => {
  const tokens = useThemeTokens();
  const [roots, setRoots] = useState<FsEntry[]>([]);
  const [current, setCurrent] = useState<string>('');
  const [entries, setEntries] = useState<FsEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingRoots, setLoadingRoots] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ----- Load roots on mount -----
  useEffect(() => {
    let cancelled = false;
    setLoadingRoots(true);
    api.get<FsEntry[]>('/fs/roots').then((r) => {
      if (cancelled) return;
      setRoots(r.data || []);
      if (r.data && r.data.length > 0) {
        // Start in the user's home (typically the first root).
        setCurrent(r.data[0].path);
      }
    }).catch((e: any) => {
      if (cancelled) return;
      setError(formatError(e, 'Failed to load folder roots.'));
    }).finally(() => {
      if (!cancelled) setLoadingRoots(false);
    });
    return () => { cancelled = true; };
  }, []);

  // ----- Load entries whenever the current path changes -----
  useEffect(() => {
    if (!current) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.get<FsEntry[]>('/fs/list', { params: { path: current } })
      .then((r) => {
        if (cancelled) return;
        setEntries(r.data || []);
      })
      .catch((e: any) => {
        if (cancelled) return;
        setError(formatError(e, 'Failed to list directory.'));
        setEntries([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [current]);

  // ----- Breadcrumb segments -----
  // Split on both / and \. On Windows ``C:\Users\me\projects`` →
  // ["C:", "Users", "me", "projects"]. The first segment needs
  // special handling to keep the drive letter glued to the colon.
  const buildSegments = (path: string): { name: string; path: string }[] => {
    if (!path) return [];
    const segs: { name: string; path: string }[] = [];
    // Drive letter (Windows): "C:\" prefix
    let rest = path;
    if (/^[A-Za-z]:[\\/]/.test(rest)) {
      segs.push({ name: rest.slice(0, 2), path: rest.slice(0, 3) });
      rest = rest.slice(3);
    } else if (path.startsWith('/')) {
      // POSIX root
      segs.push({ name: '/', path: '/' });
      rest = rest.slice(1);
    }
    const parts = rest.split(/[\\/]/).filter(Boolean);
    let cursor = segs.length > 0 ? segs[0].path : '';
    for (const p of parts) {
      cursor = cursor ? `${cursor.replace(/[\\/]+$/, '')}/${p}` : p;
      // On Windows, keep backslashes for display.
      if (/^[A-Za-z]:/.test(segs[0]?.path || '')) {
        cursor = cursor.replace(/\//g, '\\');
      }
      segs.push({ name: p, path: cursor });
    }
    return segs;
  };

  const segments = buildSegments(current);

  // ----- Parent navigation (breadcrumb) -----
  const goUp = () => {
    if (!current) return;
    const trimmed = current.replace(/[\\/]+$/, '');
    const last = trimmed.lastIndexOf('\\');
    const lastFwd = trimmed.lastIndexOf('/');
    const cut = Math.max(last, lastFwd);
    if (cut <= 0) return;  // already at root
    const parent = trimmed.slice(0, cut);
    // Preserve drive root on Windows ("C:\" not "C:").
    if (/^[A-Za-z]:$/.test(parent)) {
      setCurrent(parent + '\\');
    } else {
      setCurrent(parent);
    }
  };

  return (
    <div>
      {loadingRoots ? (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin tip="Loading starting points…" />
        </div>
      ) : roots.length === 0 ? (
        <Alert
          type="warning"
          showIcon
          message="No starting points available"
          description="The backend could not enumerate any roots (home, workspace, drives). On Windows, drives are listed via the Win32 API; on POSIX, the home dir is the default."
        />
      ) : (
        <>
          {/* ----- Roots row (R38.6 §25.1) -----
             User reported "只能选择C盘吗？切换不了其他盘符" — the
             Jump-to links at the bottom were too easy to miss
             and the breadcrumb alone only works WITHIN a root.
             Switching from C:\Users\user to D:\ requires a
             dedicated switcher. We render the roots as a row of
             pills at the TOP, always visible, with the active
             one highlighted. */}
          <div
            data-testid="browse-roots-row"
            style={{
              display: 'flex', flexWrap: 'wrap', gap: 6,
              marginBottom: 8,
              padding: '4px 0',
              borderBottom: `1px solid ${tokens.border}`,
            }}
          >
            {roots.map((r) => {
              // The current root is whichever root is an ancestor
              // of the current path (or equal to it).
              const isActive = current
                ? (current === r.path
                    || (r.path.length > 1
                        && current.toLowerCase().startsWith(
                          r.path.toLowerCase().replace(/[\\/]+$/, ''))))
                : false;
              return (
                <Button
                  key={r.path}
                  data-testid="browse-root-pill"
                  size="small"
                  type={isActive ? 'primary' : 'default'}
                  onClick={() => setCurrent(r.path)}
                  icon={isActive ? undefined : <HomeOutlined />}
                  style={{ fontSize: 12 }}
                >
                  {r.name}
                </Button>
              );
            })}
          </div>

          {/* ----- Breadcrumb ----- */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 4,
            marginBottom: 8, fontSize: 12,
            color: tokens.labelSecondary,
            flexWrap: 'wrap',
          }}>
            <HomeOutlined style={{ marginRight: 4 }} />
            {segments.length === 0 ? (
              <span>Pick a folder</span>
            ) : (
              <Breadcrumb
                style={{ fontSize: 12 }}
                items={segments.map((s, i) => ({
                  title: (
                    <a
                      data-testid="browse-breadcrumb-segment"
                      onClick={() => setCurrent(s.path)}
                      style={{ cursor: 'pointer' }}
                    >
                      {s.name}
                    </a>
                  ),
                  key: `seg-${i}-${s.path}`,
                })).concat([{
                  title: <span style={{ color: tokens.coderAccent }}>
                    {basename(current) || current}
                  </span>,
                  key: 'current',
                }])}
              />
            )}
            {current && segments.length > 1 && (
              <Button
                type="link" size="small"
                onClick={goUp}
                style={{ marginLeft: 'auto', padding: 0 }}
              >
                ↑ Up
              </Button>
            )}
          </div>

          {/* ----- Directory list ----- */}
          <Spin spinning={loading} tip="Loading…">
            {error ? (
              <Alert type="error" showIcon message={error} />
            ) : entries.length === 0 ? (
              <Empty
                description="No subfolders"
                styles={{ image: { height: 40 } }}
                style={{ padding: '12px 0' }}
              />
            ) : (
              <div
                data-testid="browse-folder-list"
                style={{
                  maxHeight: 280, overflowY: 'auto',
                  border: `1px solid ${tokens.border}`,
                  borderRadius: 6,
                }}
              >
                {entries.map((e) => (
                  <div
                    key={e.path}
                    data-testid="browse-folder-row"
                    role="button"
                    tabIndex={0}
                    onClick={() => setCurrent(e.path)}
                    onDoubleClick={() => onSelect(e.path, e.name)}
                    onKeyDown={(ev) => {
                      if (ev.key === 'Enter' || ev.key === ' ') {
                        ev.preventDefault();
                        onSelect(e.path, e.name);
                      }
                    }}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 8,
                      padding: '6px 10px', cursor: 'pointer',
                      borderBottom: `1px solid ${tokens.border}`,
                      fontSize: 13,
                    }}
                    onMouseEnter={(ev) => {
                      (ev.currentTarget as HTMLDivElement).style.background
                        = tokens.bgLay2;
                    }}
                    onMouseLeave={(ev) => {
                      (ev.currentTarget as HTMLDivElement).style.background
                        = 'transparent';
                    }}
                  >
                    <FolderOpenOutlined
                      style={{ color: tokens.coderAccent }} />
                    <span style={{ flex: 1 }}>{e.name}</span>
                    {e.has_children && (
                      <RightOutlined
                        style={{ color: tokens.labelTertiary, fontSize: 11 }} />
                    )}
                  </div>
                ))}
              </div>
            )}
          </Spin>

          {/* ----- Commit ----- */}
          <div style={{
            marginTop: 12, display: 'flex',
            justifyContent: 'flex-end', gap: 8,
          }}>
            <Button
              data-testid="browse-select-folder"
              type="primary"
              disabled={!current || disabled}
              onClick={() => onSelect(current, basename(current))}
            >
              Use this folder
            </Button>
          </div>
        </>
      )}
    </div>
  );
};

export default BrowsePanel;

