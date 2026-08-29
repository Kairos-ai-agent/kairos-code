/**
 * WorkbenchPanel — the right-side panel (R38.6 §26).
 *
 * Layout:
 *
 *   ┌─ Files │ Changes │ Tasks │ Deliverables ─┐
 *   ├──────────────────────────────────────────┤
 *   │                                          │
 *   │   <tab content>                          │
 *   │                                          │
 *   ├──────────────────────────────────────────┤
 *   │   [ Snapshot now ]   [ Restore all ]    │
 *   └──────────────────────────────────────────┘
 *
 * Mirrors minimax-code's right panel:
 *  - Files: live file tree with status badges (added / modified)
 *  - Changes: unified diff for the selected file
 *  - Tasks: ✓-progress list for the current loop's plan
 *  - Deliverables: every file the agent created or modified since
 *    the last checkpoint (the "what did the agent actually do?"
 *    answer)
 *
 * The "Snapshot now" / "Restore all" buttons at the bottom are
 * the manual undo checkpoint — separate from any automatic
 * snapshot the backend might take before agent edits.
 */
import React, { useEffect, useState, useCallback, useMemo } from 'react';
import {
  Tabs, Tree, Button, List, Spin, Empty, Alert, message, Tag,
  Space, Tooltip, Typography, Modal, Switch, Badge,
} from 'antd';
import {
  FolderOutlined, FolderOpenOutlined, FileOutlined,
  FileTextOutlined, CheckCircleOutlined, LoadingOutlined,
  RollbackOutlined, CameraOutlined, DiffOutlined,
  FolderAddOutlined, EditOutlined, GlobalOutlined,
} from '@ant-design/icons';

import api from '../api/client';
import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { onWebSocketMessage } from '../api/client';
import AgentsMdEditor from './AgentsMdEditor';
import BrowserPanel from './BrowserPanel';

const { Text } = Typography;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  has_children: boolean;
  size: number;
  mtime: number;
  status?: 'added' | 'modified' | 'deleted' | 'unchanged' | null;
}

interface TreeResponse {
  root: string;
  entries: FileEntry[];
}

interface FileContent {
  path: string;
  content: string;
  size: number;
  is_binary: boolean;
  mtime: number;
  status?: string | null;
}

interface FileDiff {
  path: string;
  status: 'added' | 'modified' | 'deleted' | 'unchanged';
  diff: string;
  current_size: number;
  checkpoint_size: number;
}

interface TaskItem {
  title: string;
  status: 'pending' | 'in_progress' | 'done' | 'failed';
  detail?: string | null;
  round?: number | null;
  timestamp: number;
}

interface TasksResponse {
  tasks: TaskItem[];
  round: number;
  score: number;
  last_approve: boolean;
  running: boolean;
}

interface DeliverableItem {
  path: string;
  name: string;
  status: 'added' | 'modified' | 'deleted' | 'unchanged';
  size: number;
  mtime: number;
  description?: string | null;
}

interface WorkbenchPanelProps {
  /** If true, show the panel collapsed to its tab strip + a
   * button to expand. The parent (AppLayout) controls visibility. */
  initiallyOpen?: boolean;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const WorkbenchPanel: React.FC<WorkbenchPanelProps> = ({
  initiallyOpen = true,
}) => {
  const tokens = useThemeTokens();
  const currentProject = useChatStore((s) => s.currentProject);
  const [activeTab, setActiveTab] = useState('files');
  const [open, setOpen] = useState(initiallyOpen);

  // Close the panel automatically when the project changes (so
  // the user doesn't see stale data from a different project).
  useEffect(() => { setOpen(initiallyOpen); }, [currentProject?.id]);

  if (!currentProject) {
    return (
      <div style={{ padding: 16 }}>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="Select a project to see the workbench"
        />
      </div>
    );
  }

  return (
    <div
      data-testid="workbench-panel"
      style={{
        display: 'flex', flexDirection: 'column',
        height: '100%', background: tokens.bgElevated,
        borderLeft: `1px solid ${tokens.border}`,
      }}
    >
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '6px 12px', borderBottom: `1px solid ${tokens.border}`,
        fontSize: 12, color: tokens.labelSecondary,
      }}>
        <span style={{ fontWeight: 600 }}>Workbench</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <AgentsMdEditor />
          <Button
            size="small" type="text"
            onClick={() => setOpen(!open)}
            data-testid="workbench-toggle"
          >
            {open ? '›' : '‹'}
          </Button>
        </div>
      </div>
      {open && (
        <>
          <Tabs
            activeKey={activeTab}
            onChange={setActiveTab}
            size="small"
            style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
            tabBarStyle={{ marginBottom: 0, paddingLeft: 8 }}
            items={[
              {
                key: 'files',
                label: <span><FolderOutlined /> Files</span>,
                children: <FilesTab projectId={currentProject.id} />,
              },
              {
                key: 'changes',
                label: <span><DiffOutlined /> Changes</span>,
                children: <ChangesTab projectId={currentProject.id} />,
              },
              {
                key: 'tasks',
                label: <span><CheckCircleOutlined /> Tasks</span>,
                children: <TasksTab projectId={currentProject.id} />,
              },
              {
                key: 'deliverables',
                label: <span><CameraOutlined /> Deliverables</span>,
                children: <DeliverablesTab projectId={currentProject.id} />,
              },
              {
                // R38.6 §32: Playwright-backed browser. The
                // 5th tab gives the user a live headless
                // Chromium session scoped to this project.
                key: 'browser',
                label: <span><GlobalOutlined /> Browser</span>,
                children: <BrowserPanel projectId={currentProject.id} />,
              },
            ]}
          />
          <CheckpointBar projectId={currentProject.id} />
        </>
      )}
    </div>
  );
};

export default WorkbenchPanel;

// ---------------------------------------------------------------------------
// Checkpoint bar (shared by all tabs)
// ---------------------------------------------------------------------------

const CheckpointBar: React.FC<{ projectId: string }> = ({ projectId }) => {
  const tokens = useThemeTokens();
  const [busy, setBusy] = useState(false);
  const [msgApi, contextHolder] = message.useMessage();
  const doSnapshot = useCallback(async () => {
    setBusy(true);
    try {
      const r = await api.post<{ snapshot_id: string; path_count: number; size_bytes: number }>(
        `/workbench/checkpoint?project_id=${projectId}`,
        {},
      );
      msgApi.success(
        `Snapshot ${r.data.snapshot_id}: ${r.data.path_count} files (${r.data.size_bytes} bytes)`,
      );
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'checkpoint failed');
    } finally {
      setBusy(false);
    }
  }, [projectId, msgApi]);
  const doRestore = useCallback(() => {
    Modal.confirm({
      title: 'Restore all files from latest checkpoint?',
      content: 'This will overwrite any files the agent has changed since the last snapshot.',
      okText: 'Restore',
      okType: 'danger',
      onOk: async () => {
        setBusy(true);
        try {
          const r = await api.post<{ restored: number; path: string }>(
            `/workbench/restore?project_id=${projectId}`,
            { path: 'all' },
          );
          msgApi.success(`Restored ${r.data.restored} files from checkpoint.`);
        } catch (e: any) {
          msgApi.error(e?.response?.data?.detail || 'restore failed');
        } finally {
          setBusy(false);
        }
      },
    });
  }, [projectId, msgApi]);
  return (
    <>
      {contextHolder}
      <div style={{
        display: 'flex', gap: 6, padding: '6px 10px',
        borderTop: `1px solid ${tokens.border}`,
        background: tokens.bgBase,
      }}>
        <Button
          size="small" icon={<CameraOutlined />}
          onClick={doSnapshot}
          loading={busy}
          data-testid="workbench-snapshot-btn"
        >
          Snapshot now
        </Button>
        <Button
          size="small" icon={<RollbackOutlined />}
          onClick={doRestore}
          disabled={busy}
          data-testid="workbench-restore-btn"
        >
          Restore all
        </Button>
      </div>
    </>
  );
};

// ---------------------------------------------------------------------------
// Files tab
// ---------------------------------------------------------------------------

const FilesTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const tokens = useThemeTokens();
  const [tree, setTree] = useState<TreeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.get<TreeResponse>(`/workbench/tree`, {
        params: { project_id: projectId, depth: 4 },
      });
      setTree(r.data);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || 'failed to load tree');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { reload(); }, [reload]);

  // Refresh on checkpoint / restore events.
  useEffect(() => {
    const off = onWebSocketMessage((data) => {
      const topic = data?.message?.topic || '';
      if (topic === 'workbench.checkpoint' || topic === 'workbench.restore') {
        reload();
      }
    });
    return () => off();
  }, [reload]);

  // Build antd Tree data shape.
  const treeData = useMemo(() => {
    if (!tree) return [];
    return buildTreeNodes(tree.entries, '');
  }, [tree]);

  if (loading && !tree) {
    return <div style={{ padding: 24, textAlign: 'center' }}><Spin /></div>;
  }
  if (error) {
    return <Alert type="error" message={error} showIcon style={{ margin: 8 }} />;
  }
  if (!tree || treeData.length === 0) {
    return (
      <div style={{ padding: 24 }}>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="No files yet — pick a folder to start"
        />
        <Button
          size="small" type="link" onClick={reload}
          style={{ marginTop: 8 }}
        >
          Reload
        </Button>
      </div>
    );
  }

  return (
    <div
      data-testid="files-tab"
      style={{ flex: 1, overflow: 'auto', padding: '4px 0' }}
    >
      <div style={{ padding: '4px 12px', fontSize: 11,
                    color: tokens.labelTertiary }}>
        {tree.entries.length} entries · click a file to view diff
      </div>
      <Tree
        treeData={treeData}
        showLine
        showIcon={false}
        blockNode
        defaultExpandAll={false}
        onSelect={(keys, info) => {
          const node: any = info.node;
          if (!node.isLeaf) return;
          setSelectedPath(node.key as string);
        }}
        titleRender={(node: any) => (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {node.isLeaf
              ? <FileTextOutlined style={{ color: tokens.labelTertiary }} />
              : <FolderOutlined style={{ color: tokens.labelTertiary }} />}
            <span>{node.title}</span>
            {node.status && (
              <Tag
                color={node.status === 'added' ? 'green'
                       : node.status === 'modified' ? 'orange'
                       : node.status === 'deleted' ? 'red' : 'default'}
                style={{ marginLeft: 4, fontSize: 10, lineHeight: '14px',
                         padding: '0 4px' }}
              >
                {node.status === 'added' ? 'A'
                 : node.status === 'modified' ? 'M'
                 : node.status === 'deleted' ? 'D' : '?'}
              </Tag>
            )}
          </span>
        )}
      />
      {selectedPath && (
        <FilePreview projectId={projectId} path={selectedPath}
                     onClose={() => setSelectedPath(null)} />
      )}
    </div>
  );
};

function buildTreeNodes(entries: FileEntry[], prefix: string): any[] {
  // Group entries by their first path segment under prefix.
  const dirs = new Map<string, FileEntry[]>();
  const files: FileEntry[] = [];
  for (const e of entries) {
    let rel = e.path;
    if (prefix) {
      if (!rel.startsWith(prefix + '/')) continue;
      rel = rel.slice(prefix.length + 1);
    }
    const seg = rel.split('/');
    if (seg.length === 1 || (seg.length === 2 && e.is_dir)) {
      files.push({ ...e, path: e.path });
    } else {
      const dirName = seg[0];
      if (!dirs.has(dirName)) dirs.set(dirName, []);
      dirs.get(dirName)!.push(e);
    }
  }
  const out: any[] = [];
  for (const f of files) {
    out.push({
      key: f.path,
      title: f.name,
      isLeaf: true,
      status: f.status,
    });
  }
  for (const [dname, children] of dirs) {
    const dirPath = prefix ? `${prefix}/${dname}` : dname;
    out.push({
      key: dirPath,
      title: dname,
      isLeaf: false,
      children: buildTreeNodes(children, dirPath),
    });
  }
  return out;
}

const FilePreview: React.FC<{ projectId: string; path: string; onClose: () => void }> = ({
  projectId, path, onClose,
}) => {
  const tokens = useThemeTokens();
  const [content, setContent] = useState<FileContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.get<FileContent>(`/workbench/file`, { params: { project_id: projectId, path } })
      .then((r) => { if (!cancelled) setContent(r.data); })
      .catch((e) => { if (!cancelled) setError(e?.response?.data?.detail || 'read failed'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [projectId, path]);
  return (
    <Modal
      open
      onCancel={onClose}
      footer={null}
      title={<span style={{ fontSize: 12, fontFamily: 'monospace' }}>{path}</span>}
      width={720}
    >
      {loading && <Spin />}
      {error && <Alert type="error" message={error} />}
      {content && (
        <pre
          data-testid="file-preview-pre"
          style={{
            maxHeight: 480, overflow: 'auto',
            background: tokens.bgLay1, padding: 12,
            fontSize: 12, fontFamily: 'monospace',
            border: `1px solid ${tokens.border}`,
            borderRadius: 4,
            color: tokens.labelPrimary,
            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}
        >
          {content.is_binary
            ? `[binary file, ${content.size} bytes — preview disabled]`
            : content.content}
        </pre>
      )}
    </Modal>
  );
};

// ---------------------------------------------------------------------------
// Changes tab
// ---------------------------------------------------------------------------

const ChangesTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const tokens = useThemeTokens();
  const [items, setItems] = useState<DeliverableItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [diff, setDiff] = useState<FileDiff | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.get<{ deliverables: DeliverableItem[] }>(
        `/workbench/deliverables`, { params: { project_id: projectId } },
      );
      // Show only modified / added / deleted (skip unchanged).
      setItems((r.data.deliverables || []).filter(
        (d) => d.status !== 'unchanged',
      ));
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'failed to load changes');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { reload(); }, [reload]);

  // Refresh on checkpoint / restore events.
  useEffect(() => {
    const off = onWebSocketMessage((data) => {
      const topic = data?.message?.topic || '';
      if (topic === 'workbench.checkpoint' || topic === 'workbench.restore') {
        reload();
      }
    });
    return () => off();
  }, [reload]);

  // Load the diff when a file is selected.
  useEffect(() => {
    if (!selected) { setDiff(null); return; }
    let cancelled = false;
    setDiffLoading(true);
    api.get<FileDiff>(`/workbench/diff`, { params: { project_id: projectId, path: selected } })
      .then((r) => { if (!cancelled) setDiff(r.data); })
      .catch((e) => { if (!cancelled) setError(e?.response?.data?.detail || 'diff failed'); })
      .finally(() => { if (!cancelled) setDiffLoading(false); });
    return () => { cancelled = true; };
  }, [projectId, selected]);

  return (
    <div
      data-testid="changes-tab"
      style={{ display: 'flex', flexDirection: 'column',
               flex: 1, overflow: 'hidden' }}
    >
      {error && <Alert type="error" message={error} showIcon
                       style={{ margin: 8 }} />}
      <div style={{ padding: '4px 12px', fontSize: 11,
                    color: tokens.labelTertiary, borderBottom: `1px solid ${tokens.border}` }}>
        {items.length} changed files since the last checkpoint
      </div>
      <div style={{ flex: 1, overflow: 'auto' }}>
        {loading ? <Spin style={{ display: 'block', margin: 24 }} /> :
         items.length === 0 ? (
           <Empty
             image={Empty.PRESENTED_IMAGE_SIMPLE}
             description="No changes — agent hasn't modified any files yet"
             style={{ marginTop: 32 }}
           />
         ) : (
           <List
             size="small"
             dataSource={items}
             renderItem={(d) => (
               <List.Item
                 data-testid="changes-row"
                 onClick={() => setSelected(d.path)}
                 style={{
                   cursor: 'pointer',
                   background: selected === d.path ? tokens.bgLay1 : 'transparent',
                   padding: '6px 12px',
                 }}
               >
                 <List.Item.Meta
                   avatar={
                     d.status === 'added' ? <FolderAddOutlined style={{ color: 'green' }} />
                       : d.status === 'deleted' ? <RollbackOutlined style={{ color: 'red' }} />
                       : <EditOutlined style={{ color: 'orange' }} />
                   }
                   title={
                     <span style={{ fontSize: 12 }}>
                       <span style={{ fontFamily: 'monospace' }}>{d.name}</span>
                       <Tag
                         color={d.status === 'added' ? 'green'
                                : d.status === 'modified' ? 'orange'
                                : 'red'}
                         style={{ marginLeft: 6, fontSize: 10, lineHeight: '14px',
                                  padding: '0 4px' }}
                       >
                         {d.status}
                       </Tag>
                     </span>
                   }
                   description={
                     <span style={{ fontSize: 11, color: tokens.labelTertiary,
                                    fontFamily: 'monospace' }}>
                       {d.path}
                     </span>
                   }
                 />
               </List.Item>
             )}
           />
         )}
      </div>
      {selected && (
        <div style={{
          borderTop: `1px solid ${tokens.border}`,
          maxHeight: 240, overflow: 'auto',
          background: tokens.bgLay1, padding: 8,
          fontFamily: 'monospace', fontSize: 11,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between',
                        marginBottom: 4 }}>
            <span style={{ color: tokens.labelSecondary }}>{selected}</span>
            <Button size="small" type="text"
                    onClick={() => setSelected(null)}>×</Button>
          </div>
          {diffLoading && <Spin />}
          {diff && (
            <pre
              data-testid="changes-diff-pre"
              style={{
                margin: 0, whiteSpace: 'pre-wrap',
                color: diff.status === 'added' ? 'green'
                     : diff.status === 'deleted' ? 'red'
                     : tokens.labelPrimary,
              }}
            >
              {diff.diff || '(no textual diff)'}
            </pre>
          )}
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Tasks tab (✓ progress list)
// ---------------------------------------------------------------------------

const TasksTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const tokens = useThemeTokens();
  const [resp, setResp] = useState<TasksResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.get<TasksResponse>(
        `/workbench/tasks`, { params: { project_id: projectId } },
      );
      setResp(r.data);
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'failed to load tasks');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { reload(); }, [reload]);

  // Auto-refresh while a loop is running.
  useEffect(() => {
    if (!resp?.running) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [resp?.running, reload]);

  return (
    <div
      data-testid="tasks-tab"
      style={{ flex: 1, overflow: 'auto', padding: '8px 12px' }}
    >
      {resp && (
        <div style={{ marginBottom: 8, display: 'flex', gap: 12, fontSize: 11,
                      color: tokens.labelTertiary }}>
          <span>Round: <b>{resp.round}</b></span>
          <span>Score: <b>{resp.score}</b></span>
          <span>{resp.running
            ? <Tag color="processing" icon={<LoadingOutlined />}>running</Tag>
            : resp.last_approve
              ? <Tag color="success">approved</Tag>
              : <Tag>idle</Tag>
          }</span>
        </div>
      )}
      {error && <Alert type="error" message={error} showIcon
                       style={{ marginBottom: 8 }} />}
      {loading && !resp ? <Spin style={{ display: 'block', margin: 24 }} /> :
       resp && resp.tasks.length === 0 ? (
         <Empty
           image={Empty.PRESENTED_IMAGE_SIMPLE}
           description="No tasks yet — start a loop to see progress"
           style={{ marginTop: 24 }}
         />
       ) : (
         <List
           size="small"
           dataSource={resp?.tasks || []}
           renderItem={(t) => {
             const icon = t.status === 'done' ? <CheckCircleOutlined style={{ color: 'green' }} />
               : t.status === 'in_progress' ? <LoadingOutlined style={{ color: tokens.coderAccent }} />
               : t.status === 'failed' ? <RollbackOutlined style={{ color: 'red' }} />
               : <span style={{ display: 'inline-block', width: 14, height: 14,
                                 borderRadius: '50%', border: `1px solid ${tokens.labelTertiary}` }} />;
             return (
               <List.Item style={{ padding: '4px 0', borderBottom: 'none' }}>
                 <Space>
                   {icon}
                   <span style={{
                     fontSize: 13,
                     textDecoration: t.status === 'done' ? 'line-through' : 'none',
                     color: t.status === 'pending' ? tokens.labelTertiary
                           : t.status === 'failed' ? 'red'
                           : tokens.labelPrimary,
                   }}>
                     {t.title}
                   </span>
                 </Space>
               </List.Item>
             );
           }}
         />
       )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Deliverables tab
// ---------------------------------------------------------------------------

const DeliverablesTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const tokens = useThemeTokens();
  const [items, setItems] = useState<DeliverableItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.get<{ deliverables: DeliverableItem[] }>(
        `/workbench/deliverables`, { params: { project_id: projectId } },
      );
      setItems(r.data.deliverables || []);
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'failed to load deliverables');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    const off = onWebSocketMessage((data) => {
      const topic = data?.message?.topic || '';
      if (topic === 'workbench.checkpoint' || topic === 'workbench.restore') {
        reload();
      }
    });
    return () => off();
  }, [reload]);

  return (
    <div
      data-testid="deliverables-tab"
      style={{ flex: 1, overflow: 'auto', padding: '8px 12px' }}
    >
      <div style={{ marginBottom: 8, fontSize: 11, color: tokens.labelTertiary }}>
        Files the agent has created or modified — click a name to view
        its content.
      </div>
      {error && <Alert type="error" message={error} showIcon
                       style={{ marginBottom: 8 }} />}
      {loading ? <Spin style={{ display: 'block', margin: 24 }} /> :
       items.length === 0 ? (
         <Empty
           image={Empty.PRESENTED_IMAGE_SIMPLE}
           description="No deliverables yet — take a snapshot to start tracking"
           style={{ marginTop: 24 }}
         />
       ) : (
         <List
           size="small"
           dataSource={items}
           renderItem={(d) => (
             <List.Item style={{ padding: '6px 0' }}>
               <List.Item.Meta
                 avatar={
                   d.status === 'added' ? <FolderAddOutlined style={{ color: 'green' }} />
                   : d.status === 'deleted' ? <RollbackOutlined style={{ color: 'red' }} />
                   : <EditOutlined style={{ color: 'orange' }} />
                 }
                 title={
                   <span style={{ fontSize: 12 }}>
                     {d.name}{' '}
                     <Tag
                       color={d.status === 'added' ? 'green'
                              : d.status === 'modified' ? 'orange'
                              : d.status === 'deleted' ? 'red' : 'default'}
                       style={{ marginLeft: 4, fontSize: 10, lineHeight: '14px',
                                padding: '0 4px' }}
                     >
                       {d.status}
                     </Tag>
                     <span style={{ marginLeft: 6, fontSize: 11,
                                    color: tokens.labelTertiary }}>
                       {(d.size / 1024).toFixed(1)} KB
                     </span>
                   </span>
                 }
                 description={
                   <span style={{ fontSize: 11, color: tokens.labelTertiary,
                                  fontFamily: 'monospace' }}>
                     {d.path}
                   </span>
                 }
               />
             </List.Item>
           )}
         />
       )}
    </div>
  );
};
