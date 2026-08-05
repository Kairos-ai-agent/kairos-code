import React, { useEffect, useState, useCallback } from 'react';
import {
  Card, Typography, Table, Select, Button, Space, Tag, message,
  Tabs, Input, Divider, Form, Modal, Radio,
} from 'antd';
import {
  SaveOutlined, RobotOutlined, TeamOutlined,
  PlusOutlined, DeleteOutlined, ReloadOutlined, LinkOutlined,
  ApiOutlined, CheckCircleOutlined, CloseCircleOutlined, EditOutlined,
} from '@ant-design/icons';
import api from '../api/client';

const { Title, Text, Paragraph } = Typography;

// LoopReview only ships two roles. Anything else coming from
// /api/config/models (e.g. a stale settings.json) is ignored by the UI
// — only entries in this map get a row in the assignment table.
const roleLabels: Record<string, string> = {
  coder: 'Coder',
  reviewer: 'Reviewer',
};

const roleDescriptions: Record<string, string> = {
  coder: '通用编码 Agent:读、写、运行命令、跑测试',
  reviewer: '只读审查 Agent:打分并给出可执行修复建议',
};

interface ModelOption { id: string; name: string; }
interface CustomModel { name: string; base_url: string; api_key: string; model: string; protocol: 'openai' | 'anthropic'; }

const SettingsPage: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [deepseekKey, setDeepseekKey] = useState('');
  const [deepseekModels, setDeepseekModels] = useState<ModelOption[]>([]);
  const [deepseekModel, setDeepseekModel] = useState('deepseek-chat');
  const [deepseekTesting, setDeepseekTesting] = useState(false);
  const [deepseekTestResult, setDeepseekTestResult] = useState<boolean | null>(null);

  const [customModels, setCustomModels] = useState<CustomModel[]>([]);
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [newModel, setNewModel] = useState<CustomModel>({ name: '', base_url: '', api_key: '', model: '', protocol: 'openai' });
  const [fetchingModels, setFetchingModels] = useState(false);
  const [fetchedModels, setFetchedModels] = useState<ModelOption[]>([]);
  const [fetchNote, setFetchNote] = useState('');
  const [testingCustom, setTestingCustom] = useState(false);
  const [testResult, setTestResult] = useState<boolean | null>(null);
  const [editIndex, setEditIndex] = useState<number | null>(null);

  const [roleMappings, setRoleMappings] = useState<Record<string, string>>({});
  const [specialists, setSpecialists] = useState<string[]>([]);
  const [bestOfN, setBestOfN] = useState<number>(1);
  const [savingLoopCfg, setSavingLoopCfg] = useState(false);

  // Load all data from backend
  const loadAll = useCallback(async () => {
    try {
      const [settingsRes, modelsRes] = await Promise.all([
        api.get('/config/settings'),
        api.get('/config/models'),
      ]);
      const keys = settingsRes.data.raw_keys || {};
      setDeepseekKey(keys.deepseek || '');
      setCustomModels(settingsRes.data.custom_models || []);
      setRoleMappings(modelsRes.data.role_mappings || {});
      try {
        const loopRes = await api.get('/config/loop');
        setSpecialists(loopRes.data.specialists || []);
        setBestOfN(loopRes.data.best_of_n || 1);
      } catch (e) { /* loop config optional */ }
      if (keys.deepseek) fetchDeepSeekModels();
    } catch (e) {
      if (window.location.hostname === 'localhost') console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  const fetchDeepSeekModels = async () => {
    try {
      const res = await api.get('/config/models/deepseek');
      setDeepseekModels(res.data.models || []);
    } catch (e) {
      setDeepseekModels([
        { id: 'deepseek-chat', name: 'DeepSeek Chat (V3)' },
        { id: 'deepseek-reasoner', name: 'DeepSeek Reasoner (R1)' },
      ]);
    }
  };

  // Save settings to backend immediately
  const saveToBackend = async (models: CustomModel[], keys?: Record<string, string>) => {
    try {
      await api.post('/config/settings', {
        api_keys: keys || { deepseek: deepseekKey },
        custom_models: models,
      });
    } catch (e) {
      // Silent fail, user can retry
    }
  };

  const handleSaveLoopConfig = async () => {
    setSavingLoopCfg(true);
    try {
      await api.post('/config/loop', { specialists, best_of_n: bestOfN });
      message.success('Loop config saved (applies on next loop start)');
    } catch (e: any) {
      message.error(e?.message || 'failed to save loop config');
    } finally {
      setSavingLoopCfg(false);
    }
  };

  const handleSave = async () => {
    try {
      await api.post('/config/settings', {
        api_keys: { deepseek: deepseekKey },
        custom_models: customModels,
      });
      for (const [role, model] of Object.entries(roleMappings)) {
        await api.post('/config/models/assign', { role, model_name: model });
      }
      message.success('设置已保存');
    } catch (e) {
      message.error('保存失败');
    }
  };

  const handleTestDeepSeek = async () => {
    if (!deepseekKey) { message.warning('请输入 API Key'); return; }
    setDeepseekTesting(true);
    setDeepseekTestResult(null);
    try {
      const res = await api.post('/config/test-provider', {
        provider: 'deepseek', api_key: deepseekKey, model: deepseekModel,
      });
      setDeepseekTestResult(res.data.success);
      message[res.data.success ? 'success' : 'error'](res.data.message);
    } catch (e) {
      setDeepseekTestResult(false);
      message.error('测试失败');
    } finally {
      setDeepseekTesting(false);
    }
  };

  const handleFetchCustomModels = async () => {
    if (!newModel.base_url) { message.warning('请先输入 API URL'); return; }
    setFetchingModels(true);
    setFetchNote('');
    try {
      const res = await api.post('/config/models/custom/fetch', {
        base_url: newModel.base_url, api_key: newModel.api_key, protocol: newModel.protocol,
      });
      if (res.data.models?.length > 0) {
        setFetchedModels(res.data.models);
        message.success(`获取到 ${res.data.count} 个模型`);
      } else {
        message.warning(res.data.error || '未获取到模型，可手动输入模型 ID');
      }
      if (res.data.note) setFetchNote(res.data.note);
    } catch (e) {
      message.error('获取模型列表失败，可手动输入模型 ID');
    } finally {
      setFetchingModels(false);
    }
  };

  const handleTestCustom = async () => {
    if (!newModel.base_url || !newModel.model) { message.warning('请填写 API URL 和模型 ID'); return; }
    setTestingCustom(true);
    setTestResult(null);
    try {
      const res = await api.post('/config/test-provider', {
        provider: 'custom', base_url: newModel.base_url,
        api_key: newModel.api_key, model: newModel.model, protocol: newModel.protocol,
      });
      setTestResult(res.data.success);
      message[res.data.success ? 'success' : 'error'](res.data.message);
    } catch (e) {
      setTestResult(false);
      message.error('测试失败');
    } finally {
      setTestingCustom(false);
    }
  };

  // Open edit modal
  const handleOpenEdit = (index: number) => {
    setEditIndex(index);
    setNewModel({ ...customModels[index] });
    setFetchedModels([]);
    setFetchNote('');
    setTestResult(null);
    setAddModalOpen(true);
  };

  // Add or edit custom model and save immediately
  const handleAddCustomModel = async () => {
    if (!newModel.name || !newModel.base_url || !newModel.model) { message.warning('请填写完整信息'); return; }
    let updated: CustomModel[];
    if (editIndex !== null) {
      updated = customModels.map((m, i) => i === editIndex ? { ...newModel } : m);
    } else {
      updated = [...customModels, { ...newModel }];
    }
    setCustomModels(updated);
    await saveToBackend(updated);
    setNewModel({ name: '', base_url: '', api_key: '', model: '', protocol: 'openai' });
    setFetchedModels([]);
    setFetchNote('');
    setTestResult(null);
    setEditIndex(null);
    setAddModalOpen(false);
    message.success(editIndex !== null ? '已更新并保存' : '已添加并保存');
  };

  // Delete custom model and save immediately
  const handleDeleteCustomModel = async (index: number) => {
    const updated = customModels.filter((_, i) => i !== index);
    setCustomModels(updated);
    await saveToBackend(updated);
    message.success('已删除并保存');
  };

  // Assign role model and save immediately
  const handleAssign = async (role: string, model: string) => {
    setRoleMappings((prev) => ({ ...prev, [role]: model }));
    try {
      await api.post('/config/models/assign', { role, model_name: model });
    } catch (e) {
      message.error('分配失败');
    }
  };

  // Build all available models for assignment dropdown
  const allModels: ModelOption[] = [
    ...(deepseekModel ? [{ id: `deepseek:${deepseekModel}`, name: `DeepSeek: ${deepseekModel}` }] : []),
    ...customModels.map((m) => ({ id: `custom:${m.name}`, name: `${m.name}: ${m.model}` })),
  ];

  const tabItems = [
    {
      key: 'deepseek',
      label: <span><RobotOutlined /> DeepSeek</span>,
      children: (
        <Card>
          <Paragraph type="secondary">配置 DeepSeek API Key。</Paragraph>
          <Divider />
          <Form layout="vertical" style={{ maxWidth: 500 }}>
            <Form.Item label="API Key" required>
              <Input.Password placeholder="sk-..." value={deepseekKey}
                onChange={(e) => setDeepseekKey(e.target.value)} />
            </Form.Item>
            <Form.Item label="默认模型">
              <Select value={deepseekModel} onChange={setDeepseekModel} style={{ width: '100%' }}
                options={deepseekModels.map((m) => ({ value: m.id, label: m.name }))} />
            </Form.Item>
            <Space>
              <Button icon={<ReloadOutlined />} onClick={fetchDeepSeekModels}>刷新模型列表</Button>
              <Button onClick={handleTestDeepSeek} loading={deepseekTesting}
                icon={deepseekTestResult === true ? <CheckCircleOutlined /> : deepseekTestResult === false ? <CloseCircleOutlined /> : undefined}>
                测试连接
              </Button>
            </Space>
          </Form>
        </Card>
      ),
    },
    {
      key: 'custom',
      label: <span><LinkOutlined /> 自定义模型</span>,
      children: (
        <Card>
          <Paragraph type="secondary">
            添加 OpenAI 或 Anthropic 兼容的 LLM 服务。
          </Paragraph>
          <Divider />
          <Button type="primary" icon={<PlusOutlined />}
            onClick={() => { setEditIndex(null); setNewModel({ name: '', base_url: '', api_key: '', model: '', protocol: 'openai' }); setFetchedModels([]); setFetchNote(''); setTestResult(null); setAddModalOpen(true); }}>
            添加自定义模型
          </Button>

          {customModels.length > 0 && (
            <Table style={{ marginTop: 16 }}
              dataSource={customModels.map((m, i) => ({ ...m, key: i }))}
              columns={[
                { title: '名称', dataIndex: 'name' },
                { title: 'API URL', dataIndex: 'base_url', ellipsis: true },
                { title: '协议', dataIndex: 'protocol', width: 100,
                  render: (p: string) => <Tag color={p === 'anthropic' ? 'orange' : 'blue'}>{p}</Tag> },
                { title: '模型', dataIndex: 'model' },
                { title: '操作', width: 120,
                  render: (_: any, __: any, index: number) => (
                    <Space>
                      <Button type="link" icon={<EditOutlined />}
                        onClick={() => handleOpenEdit(index)}>编辑</Button>
                      <Button type="link" danger icon={<DeleteOutlined />}
                        onClick={() => handleDeleteCustomModel(index)} />
                    </Space>
                  )},
              ]}
              pagination={false} size="small" />
          )}

          <Modal title={editIndex !== null ? '编辑自定义模型' : '添加自定义模型'} open={addModalOpen} width={600}
            onCancel={() => { setAddModalOpen(false); setEditIndex(null); setFetchedModels([]); setFetchNote(''); setTestResult(null); }}
            onOk={handleAddCustomModel} okText={editIndex !== null ? '保存' : '添加'}>
            <Form layout="vertical">
              <Form.Item label="名称" required>
                <Input placeholder="例如: MiniMax" value={newModel.name}
                  onChange={(e) => setNewModel((p) => ({ ...p, name: e.target.value }))} />
              </Form.Item>
              <Form.Item label="协议" required>
                <Radio.Group value={newModel.protocol}
                  onChange={(e) => setNewModel((p) => ({ ...p, protocol: e.target.value }))}>
                  <Radio.Button value="openai">OpenAI 兼容</Radio.Button>
                  <Radio.Button value="anthropic">Anthropic 兼容</Radio.Button>
                </Radio.Group>
              </Form.Item>
              <Form.Item label="API Base URL" required>
                <Input placeholder={newModel.protocol === 'anthropic' ? 'https://api.minimaxi.com/anthropic' : 'http://localhost:11434/v1'}
                  value={newModel.base_url}
                  onChange={(e) => setNewModel((p) => ({ ...p, base_url: e.target.value }))} />
              </Form.Item>
              <Form.Item label="API Key">
                <Input.Password placeholder="如果需要认证" value={newModel.api_key}
                  onChange={(e) => setNewModel((p) => ({ ...p, api_key: e.target.value }))} />
              </Form.Item>
              <Form.Item label="模型 ID" required>
                {fetchedModels.length > 0 ? (
                  <Select placeholder="选择模型" value={newModel.model || undefined}
                    onChange={(v) => setNewModel((p) => ({ ...p, model: v }))}
                    style={{ width: '100%' }} showSearch
                    options={fetchedModels.map((m) => ({ value: m.id, label: m.id }))} />
                ) : (
                  <Input placeholder={newModel.protocol === 'anthropic' ? '例如: MiniMax-Text-01' : '例如: gpt-4o, llama3.1'}
                    value={newModel.model}
                    onChange={(e) => setNewModel((p) => ({ ...p, model: e.target.value }))} />
                )}
              </Form.Item>
              {fetchNote && <Paragraph type="secondary" style={{ fontSize: 12 }}>{fetchNote}</Paragraph>}
              <Space>
                <Button icon={<ReloadOutlined />} onClick={handleFetchCustomModels}
                  loading={fetchingModels} disabled={!newModel.base_url}>
                  获取模型列表
                </Button>
                <Button icon={<ApiOutlined />} onClick={handleTestCustom}
                  loading={testingCustom} disabled={!newModel.base_url || !newModel.model}>
                  {testResult === true ? <CheckCircleOutlined style={{ color: '#52c41a' }} /> :
                   testResult === false ? <CloseCircleOutlined style={{ color: '#ff4d4f' }} /> : null}
                  测试连接
                </Button>
              </Space>
            </Form>
          </Modal>
        </Card>
      ),
    },
    {
      key: 'assign',
      label: <span><TeamOutlined /> Agent 分配</span>,
      children: (
        <Card>
          <Paragraph type="secondary">为不同角色分配模型。</Paragraph>
          <Divider />
          <Table dataSource={Object.keys(roleLabels).map((role) => ({ role, key: role }))}
            columns={[
              { title: '角色', dataIndex: 'role',
                render: (role: string) => (
                  <Space direction="vertical" size={0}>
                    <Text strong>{roleLabels[role]}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>{roleDescriptions[role]}</Text>
                  </Space>
                )},
              { title: '分配模型', dataIndex: 'role',
                render: (role: string) => (
                  <Select value={roleMappings[role] || undefined} placeholder="选择模型"
                    style={{ width: 280 }}
                    onChange={(v) => handleAssign(role, v)}
                    showSearch
                    options={allModels.map((m) => ({ value: m.id, label: m.name }))} />
                )},
            ]}
            pagination={false} />
        </Card>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>设置</Title>
        <Button type="primary" icon={<SaveOutlined />} onClick={handleSave}>保存所有设置</Button>
      </Space>
      <Tabs defaultActiveKey="deepseek" items={tabItems} />
    </div>
      <Card title="Loop Behavior" size="small" style={{ marginTop: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Alert type="info" message="These settings apply on the next loop start. They do not change a running loop." />
          <div>
            <Text strong>Specialist reviewers</Text>
            <div style={{ marginTop: 8 }}>
              <Checkbox.Group
                value={specialists}
                onChange={(v) => setSpecialists(v as string[])}
                options={[
                  { label: 'Security (OWASP)', value: 'security_reviewer' },
                  { label: 'Performance', value: 'perf_reviewer' },
                  { label: 'Design / Architecture', value: 'design_reviewer' },
                  { label: 'Test coverage', value: 'test_reviewer' },
                ]}
              />
              <div style={{ marginTop: 4, fontSize: 11, color: '#888' }}>
                Each specialist runs in parallel with the main Reviewer.
                Scores are weighted-averaged.
              </div>
            </div>
          </div>

          <div>
            <Text strong>Best-of-N coders per round</Text>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8 }}>
              <Slider min={1} max={5} value={bestOfN}
                onChange={(v) => setBestOfN(v)}
                style={{ width: 200 }} />
              <Tag color={bestOfN > 1 ? 'orange' : 'default'}>{bestOfN}</Tag>
              <span style={{ fontSize: 11, color: '#888' }}>
                {bestOfN === 1 ? 'single coder (default)' : 'runs ' + bestOfN + ' parallel coders, picks highest-scoring diff'}
              </span>
            </div>
          </div>

          <Button type="primary" icon={<SaveOutlined />}
            loading={savingLoopCfg}
            onClick={handleSaveLoopConfig}>Save loop config</Button>
        </Space>
      </Card>
    </div>
  );
};

export default SettingsPage;
