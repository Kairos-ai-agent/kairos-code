import React, { useEffect, useState, useCallback } from 'react';
import {
  Card, Typography, Table, Select, Button, Space, Tag, message,
  Tabs, Input, Divider, Form, Modal, Radio, Alert, Checkbox, Slider,
} from 'antd';
import {
  SaveOutlined, RobotOutlined, TeamOutlined,
  PlusOutlined, DeleteOutlined, ReloadOutlined, LinkOutlined,
  ApiOutlined, CheckCircleOutlined, CloseCircleOutlined, EditOutlined,
} from '@ant-design/icons';
import api from '../api/client';
import { useT } from '../i18n';

const { Title, Text, Paragraph } = Typography;

// LoopReview only ships two roles. Anything else coming from
// /api/config/models (e.g. a stale settings.json) is ignored by the UI
// — only entries in this map get a row in the assignment table.
// The maps hold dictionary keys; resolve them with t() at render time so
// the text stays translatable.
const roleLabelKeys: Record<string, string> = {
  coder: 'settings.assign.role.coder',
  reviewer: 'settings.assign.role.reviewer',
};

const roleDescKeys: Record<string, string> = {
  coder: 'settings.assign.role.coderDesc',
  reviewer: 'settings.assign.role.reviewerDesc',
};

interface ModelOption { id: string; name: string; }
interface CustomModel { name: string; base_url: string; api_key: string; model: string; protocol: 'openai' | 'anthropic'; }

const SettingsPage: React.FC = () => {
  const t = useT();
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
      // ``raw_keys`` is intentionally NOT returned by the backend (it leaks
      // plaintext keys). Use the masked ``api_keys``; a masked value like
      // ``sk-a****wxyz`` posted back is ignored server-side, so the real key
      // is preserved.
      const keys = settingsRes.data.api_keys || {};
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
      message.success(t('settings.loop.saved'));
    } catch (e: any) {
      message.error(e?.message || t('settings.loop.saveFailed'));
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
      message.success(t('settings.page.saved'));
    } catch (e) {
      message.error(t('settings.page.saveFailed'));
    }
  };

  const handleTestDeepSeek = async () => {
    if (!deepseekKey) { message.warning(t('settings.deepseek.apiKeyRequired')); return; }
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
      message.error(t('settings.shared.testFailed'));
    } finally {
      setDeepseekTesting(false);
    }
  };

  const handleFetchCustomModels = async () => {
    if (!newModel.base_url) { message.warning(t('settings.customModel.apiUrlRequired')); return; }
    setFetchingModels(true);
    setFetchNote('');
    try {
      const res = await api.post('/config/models/custom/fetch', {
        base_url: newModel.base_url, api_key: newModel.api_key, protocol: newModel.protocol,
      });
      if (res.data.models?.length > 0) {
        setFetchedModels(res.data.models);
        message.success(t('settings.customModel.fetched', { n: res.data.count }));
      } else {
        message.warning(res.data.error || t('settings.customModel.noneFetched'));
      }
      if (res.data.note) setFetchNote(res.data.note);
    } catch (e) {
      message.error(t('settings.customModel.fetchFailed'));
    } finally {
      setFetchingModels(false);
    }
  };

  const handleTestCustom = async () => {
    if (!newModel.base_url || !newModel.model) { message.warning(t('settings.customModel.urlAndModelRequired')); return; }
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
      message.error(t('settings.shared.testFailed'));
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
    if (!newModel.name || !newModel.base_url || !newModel.model) { message.warning(t('settings.customModel.incomplete')); return; }
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
    message.success(t(editIndex !== null ? 'settings.customModel.updated' : 'settings.customModel.added'));
  };

  // Delete custom model and save immediately
  const handleDeleteCustomModel = async (index: number) => {
    const updated = customModels.filter((_, i) => i !== index);
    setCustomModels(updated);
    await saveToBackend(updated);
    message.success(t('settings.customModel.deleted'));
  };

  // Assign role model and save immediately
  const handleAssign = async (role: string, model: string) => {
    setRoleMappings((prev) => ({ ...prev, [role]: model }));
    try {
      await api.post('/config/models/assign', { role, model_name: model });
    } catch (e) {
      message.error(t('settings.assign.failed'));
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
          <Paragraph type="secondary">{t('settings.deepseek.intro')}</Paragraph>
          <Divider />
          <Form layout="vertical" style={{ maxWidth: 500 }}>
            <Form.Item label={t('settings.shared.apiKeyLabel')} required>
              <Input.Password placeholder={t('settingsPage.deepseek.keyPlaceholder')} value={deepseekKey}
                onChange={(e) => setDeepseekKey(e.target.value)} />
            </Form.Item>
            <Form.Item label={t('settings.deepseek.defaultModel')}>
              <Select value={deepseekModel} onChange={setDeepseekModel} style={{ width: '100%' }}
                options={deepseekModels.map((m) => ({ value: m.id, label: m.name }))} />
            </Form.Item>
            <Space>
              <Button icon={<ReloadOutlined />} onClick={fetchDeepSeekModels}>{t('settings.deepseek.refreshModels')}</Button>
              <Button onClick={handleTestDeepSeek} loading={deepseekTesting}
                icon={deepseekTestResult === true ? <CheckCircleOutlined /> : deepseekTestResult === false ? <CloseCircleOutlined /> : undefined}>
                {t('settings.shared.testConnection')}
              </Button>
            </Space>
          </Form>
        </Card>
      ),
    },
    {
      key: 'custom',
      label: <span><LinkOutlined /> {t('settings.page.tabCustom')}</span>,
      children: (
        <Card>
          <Paragraph type="secondary">
            {t('settings.customModel.intro')}
          </Paragraph>
          <Divider />
          <Button type="primary" icon={<PlusOutlined />}
            onClick={() => { setEditIndex(null); setNewModel({ name: '', base_url: '', api_key: '', model: '', protocol: 'openai' }); setFetchedModels([]); setFetchNote(''); setTestResult(null); setAddModalOpen(true); }}>
            {t('settings.page.addCustomModel')}
          </Button>

          {customModels.length > 0 && (
            <Table style={{ marginTop: 16 }}
              dataSource={customModels.map((m, i) => ({ ...m, key: i }))}
              columns={[
                { title: t('common.name'), dataIndex: 'name' },
                { title: t('settings.customModel.colApiUrl'), dataIndex: 'base_url', ellipsis: true },
                { title: t('settings.customModel.protocol'), dataIndex: 'protocol', width: 100,
                  render: (p: string) => <Tag color={p === 'anthropic' ? 'orange' : 'blue'}>{p}</Tag> },
                { title: t('common.model'), dataIndex: 'model' },
                { title: t('common.actions'), width: 120,
                  render: (_: any, __: any, index: number) => (
                    <Space>
                      <Button type="link" icon={<EditOutlined />}
                        onClick={() => handleOpenEdit(index)}>{t('common.edit')}</Button>
                      <Button type="link" danger icon={<DeleteOutlined />}
                        onClick={() => handleDeleteCustomModel(index)} />
                    </Space>
                  )},
              ]}
              pagination={false} size="small" />
          )}

          <Modal title={editIndex !== null ? t('settings.page.editCustomModel') : t('settings.page.addCustomModel')} open={addModalOpen} width={600}
            onCancel={() => { setAddModalOpen(false); setEditIndex(null); setFetchedModels([]); setFetchNote(''); setTestResult(null); }}
            onOk={handleAddCustomModel} okText={editIndex !== null ? t('common.save') : t('common.add')}>
            <Form layout="vertical">
              <Form.Item label={t('common.name')} required>
                <Input placeholder={t('settings.customModel.namePlaceholder')} value={newModel.name}
                  onChange={(e) => setNewModel((p) => ({ ...p, name: e.target.value }))} />
              </Form.Item>
              <Form.Item label={t('settings.customModel.protocol')} required>
                <Radio.Group value={newModel.protocol}
                  onChange={(e) => setNewModel((p) => ({ ...p, protocol: e.target.value }))}>
                  <Radio.Button value="openai">{t('settings.customModel.protocolOpenai')}</Radio.Button>
                  <Radio.Button value="anthropic">{t('settings.customModel.protocolAnthropic')}</Radio.Button>
                </Radio.Group>
              </Form.Item>
              <Form.Item label={t('settings.customModel.baseUrlLabel')} required>
                <Input placeholder={newModel.protocol === 'anthropic' ? 'https://api.minimaxi.com/anthropic' : 'http://localhost:11434/v1'}
                  value={newModel.base_url}
                  onChange={(e) => setNewModel((p) => ({ ...p, base_url: e.target.value }))} />
              </Form.Item>
              <Form.Item label={t('settings.shared.apiKeyLabel')}>
                <Input.Password placeholder={t('settings.customModel.apiKeyPlaceholder')} value={newModel.api_key}
                  onChange={(e) => setNewModel((p) => ({ ...p, api_key: e.target.value }))} />
              </Form.Item>
              <Form.Item label={t('settings.customModel.modelIdLabel')} required>
                {fetchedModels.length > 0 ? (
                  <Select placeholder={t('settings.shared.selectModel')} value={newModel.model || undefined}
                    onChange={(v) => setNewModel((p) => ({ ...p, model: v }))}
                    style={{ width: '100%' }} showSearch
                    options={fetchedModels.map((m) => ({ value: m.id, label: m.id }))} />
                ) : (
                  <Input placeholder={newModel.protocol === 'anthropic' ? t('settings.customModel.modelAnthropicPlaceholder') : t('settings.customModel.modelOpenaiPlaceholder')}
                    value={newModel.model}
                    onChange={(e) => setNewModel((p) => ({ ...p, model: e.target.value }))} />
                )}
              </Form.Item>
              {fetchNote && <Paragraph type="secondary" style={{ fontSize: 12 }}>{fetchNote}</Paragraph>}
              <Space>
                <Button icon={<ReloadOutlined />} onClick={handleFetchCustomModels}
                  loading={fetchingModels} disabled={!newModel.base_url}>
                  {t('settings.customModel.fetchModels')}
                </Button>
                <Button icon={<ApiOutlined />} onClick={handleTestCustom}
                  loading={testingCustom} disabled={!newModel.base_url || !newModel.model}>
                  {testResult === true ? <CheckCircleOutlined style={{ color: '#52c41a' }} /> :
                   testResult === false ? <CloseCircleOutlined style={{ color: '#ff4d4f' }} /> : null}
                  {t('settings.shared.testConnection')}
                </Button>
              </Space>
            </Form>
          </Modal>
        </Card>
      ),
    },
    {
      key: 'assign',
      label: <span><TeamOutlined /> {t('settings.page.tabAssign')}</span>,
      children: (
        <Card>
          <Paragraph type="secondary">{t('settingsPage.assign.intro')}</Paragraph>
          <Divider />
          <Table dataSource={Object.keys(roleLabelKeys).map((role) => ({ role, key: role }))}
            columns={[
              { title: t('settings.assign.colRole'), dataIndex: 'role',
                render: (role: string) => (
                  <Space direction="vertical" size={0}>
                    <Text strong>{t(roleLabelKeys[role])}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>{t(roleDescKeys[role])}</Text>
                  </Space>
                )},
              { title: t('settings.assign.colModel'), dataIndex: 'role',
                render: (role: string) => (
                  <Select value={roleMappings[role] || undefined} placeholder={t('settings.shared.selectModel')}
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
        <Title level={3} style={{ margin: 0 }}>{t('common.settings')}</Title>
        <Button type="primary" icon={<SaveOutlined />} onClick={handleSave}>{t('settingsPage.saveAll')}</Button>
      </Space>
      <Tabs defaultActiveKey="deepseek" items={tabItems} />
      <Card title={t('settings.loop.title')} size="small" style={{ marginTop: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Alert type="info" message={t('settings.loop.alert')} />
          <div>
            <Text strong>{t('settings.loop.specialists')}</Text>
            <div style={{ marginTop: 8 }}>
              <Checkbox.Group
                value={specialists}
                onChange={(v) => setSpecialists(v as string[])}
                options={[
                  { label: t('settings.loop.specialist.security'), value: 'security_reviewer' },
                  { label: t('settings.loop.specialist.perf'), value: 'perf_reviewer' },
                  { label: t('settings.loop.specialist.design'), value: 'design_reviewer' },
                  { label: t('settings.loop.specialist.test'), value: 'test_reviewer' },
                ]}
              />
              <div style={{ marginTop: 4, fontSize: 11, color: '#888' }}>
                {t('settings.loop.specialistsHelp')}
              </div>
            </div>
          </div>

          <div>
            <Text strong>{t('settings.loop.bestOfN')}</Text>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8 }}>
              <Slider min={1} max={5} value={bestOfN}
                onChange={(v) => setBestOfN(v)}
                style={{ width: 200 }} />
              <Tag color={bestOfN > 1 ? 'orange' : 'default'}>{bestOfN}</Tag>
              <span style={{ fontSize: 11, color: '#888' }}>
                {bestOfN === 1 ? t('settings.loop.bestOfNSingle') : t('settings.loop.bestOfNMany', { n: bestOfN })}
              </span>
            </div>
          </div>

          <Button type="primary" icon={<SaveOutlined />}
            loading={savingLoopCfg}
            onClick={handleSaveLoopConfig}>{t('settings.loop.save')}</Button>
        </Space>
      </Card>
    </div>
  );
};

export default SettingsPage;
