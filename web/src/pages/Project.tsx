import React, { useEffect, useState, useRef } from 'react';
import {
  Card, Button, Table, Modal, Form, Input, Typography, Space, Tag, message, Upload, Tooltip, Progress,
} from 'antd';
import {
  PlusOutlined, PlayCircleOutlined, DeleteOutlined, PauseCircleOutlined,
  FileTextOutlined, PaperClipOutlined, InboxOutlined,
} from '@ant-design/icons';
import api from '../api/client';
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
      message.error('Failed to load projects');
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
      message.success('Project created');
      setCreateModalOpen(false);
      form.resetFields();
      fetchProjects();
    } catch (e) {
      message.error('Failed to create project');
    }
  };

  const handleStart = async (values: any) => {
    if (!selectedProject) return;
    try {
      const res = await api.post(`/projects/${selectedProject.id}/start`, {
        requirement: values.requirement,
      });
      message.success('Project started! Check the Collaboration tab.');
      setStartModalOpen(false);
      startForm.resetFields();
      setCurrentProject(selectedProject);
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || 'Unknown error';
      message.error(`Failed to start project: ${detail}`);
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
      message.error('Failed to load reference files');
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
      message.error('File too large (max 5 MB)');
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
              message.error(`Upload failed: ${detail}`);
            } catch { message.error(`Upload failed: ${xhr.statusText}`); }
            reject(new Error(xhr.statusText));
          }
        };
        xhr.onerror = () => { message.error('Upload network error'); reject(new Error('network')); };
        xhr.send(formData);
      });
      message.success(`Uploaded ${file.name}`);
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
      message.success('Removed');
      await fetchFiles(filesProject);
      fetchProjects();
    } catch (e) {
      message.error('Delete failed');
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
      title: 'Delete Project',
      content: `Are you sure you want to delete "${project.name}"?`,
      okText: 'Delete',
      okType: 'danger',
      onOk: async () => {
        try {
          await api.delete(`/projects/${project.id}`);
          message.success('Project deleted');
          fetchProjects();
        } catch (e) {
          message.error('Failed to delete project');
        }
      },
    });
  };

  const columns = [
    { title: 'Name', dataIndex: 'name', key: 'name' },
    { title: 'Description', dataIndex: 'description', key: 'description', ellipsis: true },
    { title: 'Work Dir', dataIndex: 'work_dir', key: 'work_dir', ellipsis: true,
      render: (v: string) => v || <Tag>default</Tag> },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      render: (s: string) => {
        const labels: Record<string, string> = { active: 'Ready', working: 'Running', done: 'Done' };
        const colors: Record<string, string> = { active: 'green', working: 'blue', done: 'default' };
        return <Tag color={colors[s] || 'default'}>{labels[s] || s}</Tag>;
      },
    },
    { title: 'Agents', dataIndex: 'agent_count', key: 'agent_count' },
    { title: 'Tasks', dataIndex: 'task_count', key: 'task_count' },
    {
      title: '参考资料',
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
            {n > 0 ? `${n} 个文件` : '添加'}
          </Button>
        );
      },
    },
    {
      title: 'Actions',
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
              Running
            </Button>
          ) : (
            <Button
              type="primary"
              size="small"
              icon={<PlayCircleOutlined />}
              onClick={() => openStartModal(record)}
            >
              Start
            </Button>
          )}
          <Button
            danger
            size="small"
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(record)}
          >
            Delete
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>Projects</Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModalOpen(true)}>
          New Project
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
        title="Create New Project"
        open={createModalOpen}
        onCancel={() => setCreateModalOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="Project Name" rules={[{ required: true }]}>
            <Input placeholder="e.g., E-commerce Platform" />
          </Form.Item>
          <Form.Item name="description" label="Description" rules={[{ required: true }]}>
            <TextArea rows={3} placeholder="Describe what you want to build..." />
          </Form.Item>
          <Form.Item name="work_dir" label="Work Directory">
            <Input placeholder="e.g., D:\projects\my-app (optional, leave empty for default)" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Start Project Modal */}
      <Modal
        title={`Start Development: ${selectedProject?.name}`}
        open={startModalOpen}
        onCancel={() => setStartModalOpen(false)}
        onOk={() => startForm.submit()}
        okText="Start Development"
      >
        {selectedProject && (selectedProject.files?.length ?? 0) > 0 && (
          <div
            style={{
              marginBottom: 12, padding: '6px 10px',
              background: '#f6ffed', border: '1px solid #b7eb8f',
              borderRadius: 4, fontSize: 12,
            }}
          >
            <FileTextOutlined style={{ color: '#52c41a', marginRight: 6 }} />
            将自动注入 <strong>{selectedProject.files!.length}</strong> 个参考文件到 Coder 的 prompt:
            {selectedProject.files!.slice(0, 3).map(f => ` ${f.name}`).join(', ')}
            {selectedProject.files!.length > 3 && ` …+${selectedProject.files!.length - 3}`}
          </div>
        )}
        <Form form={startForm} layout="vertical" onFinish={handleStart}>
          <Form.Item name="requirement" label="Requirements" rules={[{ required: true }]}>
            <TextArea
              rows={6}
              placeholder="Describe your requirements in detail. The Team Leader will analyze this and delegate tasks to the team."
              onChange={(e) => handleRequirementsChange(e.target.value)}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* Reference Files Modal */}
      <Modal
        title={`参考资料: ${filesProject?.name || ''}`}
        open={filesModalOpen}
        onCancel={() => { setFilesModalOpen(false); setFilesProject(null); }}
        footer={null}
        width={680}
      >
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          上传 PDF、markdown、txt、yaml 等任意格式。Loop 启动时,文件会作为
          “参考资料” 段注入 Coder 的首轮 prompt(小文件全文,大文件摘要)。
          最多 5 MB/文件,任意数量。
        </Paragraph>

        <Upload.Dragger
          multiple
          showUploadList={false}
          beforeUpload={handleFileUpload}
          accept="*/*"
          disabled={uploading}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">点击或拖拽文件到这里上传</p>
          <p className="ant-upload-hint">支持任意格式,单文件不超过 5 MB</p>
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
          locale={{ emptyText: '暂无参考文件' }}
          columns={[
            { title: '名称', dataIndex: 'name',
              render: (n: string, r: ProjectFile) => (
                <Tooltip title={r.mime}>
                  <span><FileTextOutlined style={{ marginRight: 6, color: '#1677ff' }} />{n}</span>
                </Tooltip>
              ),
            },
            { title: '大小', dataIndex: 'size', width: 100,
              render: (s: number) => formatBytes(s) },
            { title: '上传时间', dataIndex: 'uploaded_at', width: 160,
              render: (t: number) => new Date(t * 1000).toLocaleString() },
            { title: '操作', width: 80,
              render: (_: any, r: ProjectFile) => (
                <Button size="small" danger type="link"
                  icon={<DeleteOutlined />} onClick={() => handleFileDelete(r)}>
                  删除
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
