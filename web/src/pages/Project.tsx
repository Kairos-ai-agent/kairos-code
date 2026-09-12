import React, { useEffect, useState, useRef } from 'react';
import {
  Card, Button, Table, Modal, Form, Input, Typography, Space, Tag, message, Upload, Tooltip, Progress,
} from 'antd';
import {
  PlusOutlined, PlayCircleOutlined, DeleteOutlined, PauseCircleOutlined,
  FileTextOutlined, PaperClipOutlined, InboxOutlined,
} from '@ant-design/icons';
import api from '../api/client';
import { formatError } from '../utils/formatError';
import { useT } from '../i18n';
import { useAgentStore } from '../stores/agentStore';
import type { Project, ProjectFile } from '../types';

const { Title, Paragraph } = Typography;
const { TextArea } = Input;

const ProjectPage: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [startModalOpen, setStartModalOpen] = useState(false);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [form] = Form.useForm();
  const [startForm] = Form.useForm();
  const { setCurrentProject } = useAgentStore();
  const t = useT();

  // Reference files state
  const [filesModalOpen, setFilesModalOpen] = useState(false);
  const [filesProject, setFilesProject] = useState<Project | null>(null);
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);

  const fetchProjects = async () => {
    try {
      const res = await api.get('/projects');
      setProjects(res.data.projects);
    } catch (e) {
      message.error(t('project.page.loadFailed'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProjects();
    // Also refresh when page becomes visible
    const handleVisibility = () => { if (!document.hidden) fetchProjects(); };
    document.addEventListener('visibilitychange', handleVisibility);
    return () => document.removeEventListener('visibilitychange', handleVisibility);
  }, []);

  const handleCreate = async (values: any) => {
    try {
      await api.post('/projects', values);
      message.success(t('project.page.created'));
      setCreateModalOpen(false);
      form.resetFields();
      fetchProjects();
    } catch (e) {
      message.error(t('project.page.createFailed'));
    }
  };

  const handleStart = async (values: any) => {
    if (!selectedProject) return;
    try {
      const res = await api.post(`/projects/${selectedProject.id}/start`, {
        requirement: values.requirement,
      });
      message.success(t('project.page.started'));
      setStartModalOpen(false);
      startForm.resetFields();
      setCurrentProject(selectedProject);
    } catch (e: any) {
      const detail = formatError(e, t('common.unknownError'));
      message.error(t('project.page.startFailed', { detail }));
    }
  };

  // -------------------------------------------------------- Reference files
  // CRUD lives in /api/projects/{id}/files. The list comes back via the
  // project detail endpoint (to_dict includes `files`) so we mostly
  // refresh after writes.

  const fetchFiles = async (project: Project) => {
    setFilesLoading(true);
    try {
      const res = await api.get(`/projects/${project.id}/files`);
      setFiles(res.data.files || []);
    } catch (e) {
      message.error(t('project.files.loadFailed'));
    } finally {
      setFilesLoading(false);
    }
  };

  const openFilesModal = async (project: Project) => {
    setFilesProject(project);
    setFilesModalOpen(true);
    await fetchFiles(project);
  };

  const handleFileUpload = async (file: File) => {
    if (!filesProject) return false;
    if (file.size > 5 * 1024 * 1024) {
      message.error(t('project.files.tooLarge'));
      return false;
    }
    setUploading(true);
    setUploadProgress(0);
    try {
      const formData = new FormData();
      formData.append('file', file);
      // Use XHR for upload progress; axios fetch gives less control here.
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', `/api/projects/${filesProject.id}/files`);
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) setUploadProgress(Math.round((e.loaded / e.total) * 100));
        };
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) resolve();
          else {
            try {
              const detail = JSON.parse(xhr.responseText)?.detail || xhr.statusText;
              message.error(t('project.files.uploadFailed', { detail }));
            } catch { message.error(t('project.files.uploadFailed', { detail: xhr.statusText })); }
            reject(new Error(xhr.statusText));
          }
        };
        xhr.onerror = () => { message.error(t('common.networkError')); reject(new Error('network')); };
        xhr.send(formData);
      });
      message.success(t('project.files.uploaded', { name: file.name }));
      await fetchFiles(filesProject);
      // Refresh the project list so the "files" count chip updates.
      fetchProjects();
    } catch (e) {
      // already surfaced via message.error above
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
    // Returning false tells antd Upload not to POST on its own.
    return false;
  };

  const handleFileDelete = async (file: ProjectFile) => {
    if (!filesProject) return;
    try {
      await api.delete(`/projects/${filesProject.id}/files/${file.id}`);
      message.success(t('project.files.removed'));
      await fetchFiles(filesProject);
      fetchProjects();
    } catch (e) {
      message.error(t('project.files.deleteFailed'));
    }
  };

  const formatBytes = (n: number) => {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(2)} MB`;
  };

  // Auto-save requirements (debounced)
  const saveTimerRef = React.useRef<any>(null);
  const handleRequirementsChange = (value: string) => {
    if (!selectedProject) return;
    startForm.setFieldsValue({ requirement: value });
    // Debounce save
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      try {
        await api.post(`/projects/${selectedProject.id}/requirements`, { requirements: value });
      } catch (e) { /* silent */ }
    }, 1000);
  };

  // Open start modal and load saved requirements
  const openStartModal = (project: Project) => {
    setSelectedProject(project);
    setStartModalOpen(true);
    // Load saved requirements
    const req = (project as any).requirements || '';
    setTimeout(() => startForm.setFieldsValue({ requirement: req }), 100);
  };

  const handleDelete = async (project: Project) => {
    Modal.confirm({
      title: t('project.page.deleteTitle'),
      content: t('project.page.deleteConfirm', { name: project.name }),
      okText: t('common.delete'),
      okType: 'danger',
      onOk: async () => {
        try {
          await api.delete(`/projects/${project.id}`);
          message.success(t('project.page.deleted'));
          fetchProjects();
        } catch (e) {
          message.error(t('project.page.deleteFailed'));
        }
      },
    });
  };

  const columns = [
    { title: t('common.name'), dataIndex: 'name', key: 'name' },
    { title: t('project.table.description'), dataIndex: 'description', key: 'description', ellipsis: true },
    { title: t('project.table.workDir'), dataIndex: 'work_dir', key: 'work_dir', ellipsis: true,
      render: (v: string) => v || <Tag>{t('project.table.default')}</Tag> },
    {
      title: t('common.status'),
      dataIndex: 'status',
      key: 'status',
      render: (s: string) => {
        const labels: Record<string, string> = {
          active: t('project.status.active'),
          working: t('project.status.working'),
          done: t('project.status.done'),
        };
        const colors: Record<string, string> = { active: 'green', working: 'blue', done: 'default' };
        return <Tag color={colors[s] || 'default'}>{labels[s] || s}</Tag>;
      },
    },
    { title: t('common.agents'), dataIndex: 'agent_count', key: 'agent_count' },
    { title: t('project.table.tasks'), dataIndex: 'task_count', key: 'task_count' },
    {
      title: t('project.table.files'),
      key: 'files',
      render: (_: any, record: Project) => {
        const n = record.files?.length ?? 0;
        return (
          <Button
            size="small"
            type={n > 0 ? 'default' : 'dashed'}
            icon={<PaperClipOutlined />}
            onClick={() => openFilesModal(record)}
          >
            {n > 0 ? t('project.table.fileCount', { n }) : t('common.add')}
          </Button>
        );
      },
    },
    {
      title: t('common.actions'),
      key: 'actions',
      render: (_: any, record: Project) => (
        <Space>
          {record.status === 'working' ? (
            <Button
              type="primary"
              size="small"
              icon={<PauseCircleOutlined />}
              disabled
            >
              {t('common.running')}
            </Button>
          ) : (
            <Button
              type="primary"
              size="small"
              icon={<PlayCircleOutlined />}
              onClick={() => openStartModal(record)}
            >
              {t('common.start')}
            </Button>
          )}
          <Button
            danger
            size="small"
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(record)}
          >
            {t('common.delete')}
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>{t('common.projects')}</Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModalOpen(true)}>
          {t('project.page.new')}
        </Button>
      </Space>

      <Card>
        <Table
          dataSource={projects}
          columns={columns}
          rowKey="id"
          loading={loading}
        />
      </Card>

      {/* Create Project Modal */}
      <Modal
        title={t('project.create.title')}
        open={createModalOpen}
        onCancel={() => setCreateModalOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label={t('project.form.name')} rules={[{ required: true }]}>
            <Input placeholder={t('project.form.namePlaceholder')} />
          </Form.Item>
          <Form.Item name="description" label={t('project.form.description')} rules={[{ required: true }]}>
            <TextArea rows={3} placeholder={t('project.form.descriptionPlaceholder')} />
          </Form.Item>
          <Form.Item name="work_dir" label={t('project.form.workDir')}>
            <Input placeholder={t('project.form.workDirPlaceholder')} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Start Project Modal */}
      <Modal
        title={t('project.start.title', { name: selectedProject?.name ?? '' })}
        open={startModalOpen}
        onCancel={() => setStartModalOpen(false)}
        onOk={() => startForm.submit()}
        okText={t('project.start.ok')}
      >
        {selectedProject && (selectedProject.files?.length ?? 0) > 0 && (
          <div
            style={{
              marginBottom: 12, padding: '6px 10px',
              background: '#f6ffed', border: '1px solid #b7eb8f',
              borderRadius: 4, fontSize: 12,
            }}
          >
            <FileTextOutlined style={{ color: '#52c41a', marginInlineEnd: 6 }} />
            {t('project.start.injectNote', { n: selectedProject.files!.length })}
            {selectedProject.files!.slice(0, 3).map(f => ` ${f.name}`).join(', ')}
            {selectedProject.files!.length > 3 && ` …+${selectedProject.files!.length - 3}`}
          </div>
        )}
        <Form form={startForm} layout="vertical" onFinish={handleStart}>
          <Form.Item name="requirement" label={t('project.start.requirements')} rules={[{ required: true }]}>
            <TextArea
              rows={6}
              placeholder={t('project.start.requirementsPlaceholder')}
              onChange={(e) => handleRequirementsChange(e.target.value)}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* Reference Files Modal */}
      <Modal
        title={t('project.files.title', { name: filesProject?.name || '' })}
        open={filesModalOpen}
        onCancel={() => { setFilesModalOpen(false); setFilesProject(null); }}
        footer={null}
        width={680}
      >
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          {t('project.files.help')}
        </Paragraph>

        <Upload.Dragger
          multiple
          showUploadList={false}
          beforeUpload={handleFileUpload}
          accept="*/*"
          disabled={uploading}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">{t('project.files.dragText')}</p>
          <p className="ant-upload-hint">{t('project.files.dragHint')}</p>
        </Upload.Dragger>

        {uploading && (
          <div style={{ marginTop: 12 }}>
            <Progress percent={uploadProgress} status="active" />
          </div>
        )}

        <Table
          style={{ marginTop: 16 }}
          size="small"
          dataSource={files.map((f, i) => ({ ...f, key: f.id || i }))}
          loading={filesLoading}
          pagination={false}
          locale={{ emptyText: t('project.files.empty') }}
          columns={[
            { title: t('common.name'), dataIndex: 'name',
              render: (n: string, r: ProjectFile) => (
                <Tooltip title={r.mime}>
                  <span><FileTextOutlined style={{ marginInlineEnd: 6, color: '#1677ff' }} />{n}</span>
                </Tooltip>
              ),
            },
            { title: t('common.size'), dataIndex: 'size', width: 100,
              render: (s: number) => formatBytes(s) },
            { title: t('project.files.uploadedAt'), dataIndex: 'uploaded_at', width: 160,
              render: (ts: number) => new Date(ts * 1000).toLocaleString() },
            { title: t('common.actions'), width: 80,
              render: (_: any, r: ProjectFile) => (
                <Button size="small" danger type="link"
                  icon={<DeleteOutlined />} onClick={() => handleFileDelete(r)}>
                  {t('common.delete')}
                </Button>
              ),
            },
          ]}
        />
      </Modal>
    </div>
  );
};

export default ProjectPage;

