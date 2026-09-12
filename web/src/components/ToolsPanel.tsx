/**
 * ToolsPanel — R38.6 §34. Tools.
 *
 * Single panel in the right Sider (toggle via Workbench header)
 * that surfaces ALL the borrowed-feature APIs in one place:
 *   - Plan Mode (Gemini)  - generate/approve/reject TODO
 *   - Skills (Claude)      - invoke any installed skill
 *   - Hooks (Claude)       - register/list shell-command hooks
 *   - Memory (the self-improving agent)      - remember/recall FTS5
 *   - IM platforms          - configure DingTalk/WeCom/Slack/TG
 *   - Sandbox (the agent-gateway)   - check command safety
 *   - Verify (the cloud task)       - run the Reviewer on demand
 *   - Compaction (Claude)   - force-compact long memory
 *   - Trajectory (the self-improving agent)  - export agent's RL data
 *   - Async tasks (the cloud task)   - submit background jobs
 *   - A2A (Gemini)         - register remote A2A agents
 *   - Approval (the cloud task)     - suggest/edit/full-auto mode
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  Tabs, Button, Input, Space, Tag, Spin, Empty, Alert, message,
  Modal, Form, Select, Switch, List, Tooltip, Drawer, Typography, Card,
  Popconfirm, Radio, InputNumber, Row, Col, Divider,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, ThunderboltOutlined, SearchOutlined,
  ApiOutlined, RobotOutlined, CodeOutlined, MessageOutlined,
  CheckCircleOutlined, CloseCircleOutlined, HistoryOutlined,
  ExportOutlined, ForkOutlined, SafetyOutlined, SendOutlined,
} from '@ant-design/icons';
import api from '../api/client';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { useChatStore } from '../stores/chatStore';
import { formatError } from '../utils/formatError';
import { useT } from '../i18n';

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

interface Props {
  open: boolean;
  onClose: () => void;
  projectId: string;
}

const ToolsPanel: React.FC<Props> = ({ open, onClose, projectId }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const [tab, setTab] = useState('plan');

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={t('toolsPanel.toolsTools')}
      width={760}
      destroyOnHidden
    >
      <Tabs
        activeKey={tab}
        onChange={setTab}
        type="card"
        size="small"
        items={[
          // R38.6.3: collapsed 8 tabs → 5. Skills / Hooks / Sandbox
          // / Approval all moved under one "Advanced" tab. The
          // user said the "borrowed from X" source labels (Claude
          // / the cloud task / the agent-gateway / etc) are noise — drop them, show
          // the feature only.
          { key: 'plan', label: t('toolsPanel.plan'), children:
            <PlanTab projectId={projectId} /> },
          { key: 'memory', label: t('toolsPanel.memory'), children:
            <MemoryTab projectId={projectId} /> },
          { key: 'im', label: t('toolsPanel.im'), children:
            <IMTab /> },
          { key: 'verify', label: t('toolsPanel.verify'), children:
            <VerifyTab projectId={projectId} /> },
          { key: 'advanced', label: t('toolsPanel.advanced'), children:
            <AdvancedTab projectId={projectId} /> },
        ]}
      />
    </Drawer>
  );
};

// ============= Plan Mode =============
const PlanTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const [task, setTask] = useState('');
  const [generating, setGenerating] = useState(false);
  const [plan, setPlan] = useState<any>(null);
  const [plans, setPlans] = useState<any[]>([]);
  const [msgApi, ctx] = message.useMessage();

  const load = useCallback(async () => {
    try {
      const r = await api.get<{plans: any[]}>(`/borrowed/${projectId}/plans`);
      setPlans(r.data.plans || []);
    } catch {/* ignore */}
  }, [projectId]);

  useEffect(() => { if (projectId) load(); }, [projectId, load]);

  const generate = async () => {
    if (!task.trim()) { msgApi.warning(t('toolsPanel.enterTask')); return; }
    setGenerating(true);
    try {
      const r = await api.post<any>(`/borrowed/${projectId}/plan/generate`,
        { task, project_context: '' });
      setPlan(r.data);
      msgApi.success(t('toolsPanel.planGenerated'));
      load();
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'failed');
    } finally { setGenerating(false); }
  };

  const decide = async (planId: string, action: 'approve' | 'reject') => {
    try {
      await api.post(`/borrowed/${projectId}/plans/${planId}/${action}`,
        { feedback: '' });
      msgApi.success(`Plan ${action}d`);
      if (plan?.id === planId) setPlan({ ...plan, status: action === 'approve' ? 'approved' : 'rejected' });
      load();
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'failed');
    }
  };

  return (
    <div style={{ padding: '8px 0' }}>
      {ctx}
      <Title level={5}>{t('toolsPanel.generateAPlanBeforeRunningATask')}</Title>
      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t('toolsPanel.llmLaysOutATODOApproveToProceedOrRejectToCancel')}</Paragraph>
      <TextArea
        rows={3}
        value={task}
        onChange={e => setTask(e.target.value)}
        placeholder={t('toolsPanel.eGAddUserLoginWithJWTAuth')}
      />
      <Button type="primary" icon={<ThunderboltOutlined />}
        loading={generating} onClick={generate} style={{ marginTop: 8 }}
        data-testid="plan-generate">
        {t('toolsPanel.generatePlan')}
      </Button>
      {plan && (
        <Card size="small" style={{ marginTop: 12 }}
              title={`Plan ${plan.id} (${plan.status})`}>
          {plan.steps.map((s: any, i: number) => (
            <div key={s.id} style={{ marginBottom: 8 }}>
              <strong>{i+1}. {s.title}</strong>
              <div style={{ color: tokens.labelTertiary, fontSize: 12 }}>
                {s.detail} {s.tool_hint && <Tag>{s.tool_hint}</Tag>}
              </div>
            </div>
          ))}
          {plan.status === 'draft' && (
            <Space>
              <Button type="primary" onClick={() => decide(plan.id, 'approve')}
                data-testid="plan-approve">{t('toolsPanel.approve')}</Button>
              <Button danger onClick={() => decide(plan.id, 'reject')}
                data-testid="plan-reject">{t('toolsPanel.reject')}</Button>
            </Space>
          )}
        </Card>
      )}
      {plans.length > 0 && (
        <>
          <Divider />
          <Title level={5}>{t('toolsPanel.recentPlans')}</Title>
          <List size="small"
            dataSource={plans.slice(0, 5)}
            renderItem={(p: any) => (
              <List.Item>
                <Text style={{ flex: 1 }} ellipsis>{p.task}</Text>
                <Tag color={p.status === 'approved' ? 'green' :
                           p.status === 'rejected' ? 'red' : 'blue'}>
                  {p.status}
                </Tag>
              </List.Item>
            )} />
        </>
      )}
    </div>
  );
};

// ============= Skills =============
const SkillsTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const [skills, setSkills] = useState<any[]>([]);
  const [invoking, setInvoking] = useState<string | null>(null);
  const [invokeResult, setInvokeResult] = useState<any>(null);
  const [msgApi, ctx] = message.useMessage();

  const load = useCallback(async () => {
    try {
      const r = await api.get<{providers: any[]}>('/borrowed/providers');
      // Skills come from the existing skills directory. We can
      // also list the SkillsLoader's inventory.
      const sl = await import('../api/client').then(m => m.default);
      setSkills([]);
    } catch {/* ignore */}
  }, []);

  useEffect(() => { load(); }, [load]);

  const invokeSkill = async (name: string) => {
    setInvoking(name);
    try {
      const r = await api.post<any>(`/borrowed/${projectId}/skills/invoke`,
        { skill_name: name });
      setInvokeResult(r.data);
      msgApi.success(`Skill ${name} loaded`);
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'failed');
    } finally { setInvoking(null); }
  };

  return (
    <div style={{ padding: '8px 0' }}>
      {ctx}
      <Title level={5}>{t('toolsPanel.skills')}</Title>
      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t('toolsPanel.skillsInstalledNote')}
      </Paragraph>
      <Alert
        type="info" showIcon style={{ marginBottom: 12 }}
        message={t('toolsPanel.skills.tip')}
      />
      <Input.Search
        placeholder={t('toolsPanel.searchSkillsEGTestDrivenDevelopmentVerificationB')}
        onSearch={async (q) => {
          // Try to invoke any skill whose name matches the query
          if (q) {
            try {
              await invokeSkill(q);
            } catch {/* ignore */}
          }
        }}
        enterButton
      />
      {invokeResult && (
        <Card size="small" style={{ marginTop: 12 }}
              title={`Loaded: ${invokeResult.name}`}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {invokeResult.description}
          </Text>
          <pre style={{
            maxHeight: 240, overflow: 'auto',
            background: tokens.bgElevated, padding: 8,
            fontSize: 11, marginTop: 8,
          }}>{invokeResult.body?.slice(0, 1000)}</pre>
        </Card>
      )}
    </div>
  );
};

// ============= Hooks =============
const HooksTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const t = useT();
  const [hooks, setHooks] = useState<any[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [form] = Form.useForm();
  const [msgApi, ctx] = message.useMessage();

  const load = useCallback(async () => {
    try {
      const r = await api.get<{hooks: any[]}>(`/borrowed/${projectId}/hooks`);
      setHooks(r.data.hooks || []);
    } catch {/* ignore */}
  }, [projectId]);

  useEffect(() => { if (projectId) load(); }, [projectId, load]);

  const addHook = async (values: any) => {
    try {
      await api.post(`/borrowed/${projectId}/hooks`, values);
      msgApi.success(t('toolsPanel.hookRegistered'));
      setShowAdd(false);
      form.resetFields();
      load();
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'failed');
    }
  };

  return (
    <div style={{ padding: '8px 0' }}>
      {ctx}
      <Title level={5}>{t('toolsPanel.hooks')}</Title>
      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t('toolsPanel.runAShellCommandWhenAnEventFiresEvents')}
        <Tag>{t('toolsPanel.pretooluse')}</Tag><Tag>{t('toolsPanel.posttooluse')}</Tag>
        <Tag>{t('toolsPanel.sessionstart')}</Tag><Tag>{t('toolsPanel.sessionend')}</Tag><Tag>{t('toolsPanel.stop')}</Tag>
      </Paragraph>
      <Button type="primary" icon={<PlusOutlined />}
        onClick={() => setShowAdd(true)} data-testid="hook-add">
        {t('toolsPanel.addHook')}
      </Button>
      <List size="small" style={{ marginTop: 12 }}
        dataSource={hooks}
        renderItem={(h: any) => (
          <List.Item>
            <Space>
              <Tag color="blue">{h.event}</Tag>
              <Text strong>{h.name}</Text>
              <Text type="secondary" style={{ fontSize: 11 }}>
                {h.command?.slice(0, 60)}…
              </Text>
            </Space>
          </List.Item>
        )} />
      <Modal title={t('toolsPanel.addHook')} open={showAdd}
        onCancel={() => setShowAdd(false)}
        onOk={() => form.submit()}>
        <Form form={form} onFinish={addHook} layout="vertical">
          <Form.Item name="event" label={t('toolsPanel.event')} rules={[{required: true}]}>
            <Select options={[
              {value: 'PostToolUse', label: t('toolsPanel.posttooluseAfterTool')},
              {value: 'PreToolUse', label: t('toolsPanel.pretooluseBeforeTool')},
              {value: 'SessionStart', label: t('toolsPanel.sessionstart')},
              {value: 'SessionEnd', label: t('toolsPanel.sessionend')},
              {value: 'Stop', label: t('toolsPanel.stop')},
            ]} />
          </Form.Item>
          <Form.Item name="name" label={t('toolsPanel.hookName')} rules={[{required: true}]}>
            <Input placeholder="auto-lint" />
          </Form.Item>
          <Form.Item name="command" label={t('toolsPanel.shellCommand')} rules={[{required: true}]}>
            <Input placeholder={t('toolsPanel.black21True')} />
          </Form.Item>
          <Form.Item name="matcher" label={t('toolsPanel.matcherOptional')}>
            <Input placeholder={t('toolsPanel.pyOrLeaveEmpty')} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

// ============= Memory =============
const MemoryTab: React.FC<{ projectId: string }> = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const [keys, setKeys] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<any[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [form] = Form.useForm();
  const [msgApi, ctx] = message.useMessage();

  const load = useCallback(async () => {
    try {
      const r = await api.get<{keys: string[]}>('/borrowed/memory/list?scope=project');
      setKeys(r.data.keys || []);
    } catch {/* ignore */}
  }, []);

  useEffect(() => { load(); }, [load]);

  const search = async () => {
    if (!query.trim()) return;
    try {
      const r = await api.post<{results: any[]}>('/borrowed/memory/recall',
        { query, scope: 'project', limit: 10 });
      setResults(r.data.results || []);
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'failed');
    }
  };

  const remember = async (values: any) => {
    try {
      await api.post('/borrowed/memory/remember', { ...values, scope: 'project' });
      msgApi.success('Remembered');
      setShowAdd(false);
      form.resetFields();
      load();
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'failed');
    }
  };

  const forget = async (key: string) => {
    try {
      await api.post('/borrowed/memory/forget', { key, scope: 'project' });
      msgApi.success('Forgotten');
      load();
    } catch (e: any) { msgApi.error(e?.response?.data?.detail || 'failed'); }
  };

  return (
    <div style={{ padding: '8px 0' }}>
      {ctx}
      <Title level={5}>{t('toolsPanel.memory2')}</Title>
      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t('toolsPanel.persistentProjectMemoryTheCoderAutoRecallsReleva')}
      </Paragraph>
      <Space.Compact style={{ width: '100%' }}>
        <Input.Search
          placeholder={t('toolsPanel.recallFullTextSearch')} enterButton
          value={query} onChange={e => setQuery(e.target.value)}
          onSearch={search} data-testid="memory-search"
        />
        <Button icon={<PlusOutlined />} onClick={() => setShowAdd(true)}
          data-testid="memory-add">{t('toolsPanel.add')}</Button>
      </Space.Compact>
      {results.length > 0 && (
        <List size="small" style={{ marginTop: 12 }}
          dataSource={results}
          renderItem={(r: any) => (
            <List.Item actions={[
              <Button danger size="small"
                onClick={() => forget(r.key)}>{t('toolsPanel.forget')}</Button>
            ]}>
              <Text strong>{r.key}</Text>
              <div style={{ color: tokens.labelTertiary, fontSize: 12 }}>
                {r.value}
              </div>
            </List.Item>
          )} />
      )}
      {keys.length > 0 && results.length === 0 && (
        <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 12 }}>
          {t('toolsPanel.memory.storedHint', { n: keys.length })}
        </Paragraph>
      )}
      <Modal title={t('toolsPanel.remember')} open={showAdd}
        onCancel={() => setShowAdd(false)}
        onOk={() => form.submit()}>
        <Form form={form} onFinish={remember} layout="vertical">
          <Form.Item name="key" label={t('toolsPanel.key')} rules={[{required: true}]}>
            <Input placeholder="deployment-target" />
          </Form.Item>
          <Form.Item name="value" label={t('toolsPanel.value')} rules={[{required: true}]}>
            <TextArea rows={3} placeholder={t('toolsPanel.productionK8sClusterProdEu1')} />
          </Form.Item>
          <Form.Item name="tags" label={t('toolsPanel.tagsCommaSeparated')}>
            <Input placeholder={t('toolsPanel.infraProd')} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

// ============= IM Platforms =============
const IMTab: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [testText, setTestText] = useState('👋 Kairos test');
  const [testResult, setTestResult] = useState<string>('');
  const [busy, setBusy] = useState<string | null>(null);
  const [msgApi, ctx] = message.useMessage();

  useEffect(() => {
    api.get<{platforms: string[]}>('/borrowed/im/platforms')
      .then(r => setPlatforms(r.data.platforms || []))
      .catch(() => {});
  }, []);

  const test = async (platform: string) => {
    setBusy(platform);
    try {
      const r = await api.post<any>('/borrowed/im/test',
        { platform, text: testText });
      setTestResult(JSON.stringify(r.data, null, 2));
      msgApi.success(`${platform}: ${r.data.ok ? 'OK' : 'failed'}`);
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'failed');
    } finally { setBusy(null); }
  };

  return (
    <div style={{ padding: '8px 0' }}>
      {ctx}
      <Title level={5}>{t('toolsPanel.imPlatforms')}</Title>
      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t('toolsPanel.pushAgentEventsToYourTeamChatConfigureWebhookURL')}
      </Paragraph>
      <Input value={testText} onChange={e => setTestText(e.target.value)}
        style={{ marginBottom: 12 }} />
      <List size="small"
        dataSource={platforms}
        renderItem={(p: string) => (
          <List.Item actions={[
            <Button size="small" icon={<SendOutlined />}
              loading={busy === p} onClick={() => test(p)}
              data-testid={`im-test-${p}`}>{t('toolsPanel.sendTest')}</Button>
          ]}>
            <Space>
              <Tag>{p}</Tag>
              <Text type="secondary">
                {p === 'feishu' ? 'kairos.feishu.FeishuBot (custom robot)' :
                 p === 'dingtalk' ? 'custom robot + optional signing' :
                 p === 'wecom' ? 'group robot webhook' :
                 p === 'slack' ? 'incoming webhook' :
                 p === 'telegram' ? 'Bot API (requires bot token)' :
                 p === 'discord' ? 'webhook' : ''}
              </Text>
            </Space>
          </List.Item>
        )} />
      {testResult && (
        <pre style={{
          background: tokens.bgElevated, padding: 8, fontSize: 11,
          maxHeight: 200, overflow: 'auto', marginTop: 12,
        }}>{testResult}</pre>
      )}
    </div>
  );
};

// ============= Sandbox =============
const SandboxTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const t = useT();
  const [cmd, setCmd] = useState('rm -rf /tmp/test');
  const [level, setLevel] = useState('standard');
  const [result, setResult] = useState<any>(null);
  const [msgApi, ctx] = message.useMessage();

  const check = async () => {
    try {
      const r = await api.post<any>(`/borrowed/${projectId}/sandbox/check`,
        { command: cmd, policy_level: level });
      setResult(r.data);
      msgApi[r.data.allowed ? 'success' : 'warning'](
        r.data.allowed ? 'Command allowed' : 'Command blocked');
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'failed');
    }
  };

  return (
    <div style={{ padding: '8px 0' }}>
      {ctx}
      <Title level={5}>{t('toolsPanel.sandbox')}</Title>
      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t('toolsPanel.dryRunCheckWouldThisCommandBeBlockedByThePolicy')}
      </Paragraph>
      <Space.Compact style={{ width: '100%' }}>
        <Input value={cmd} onChange={e => setCmd(e.target.value)}
          style={{ width: '60%' }} data-testid="sandbox-cmd" />
        <Select value={level} onChange={setLevel} style={{ width: '20%' }}
          data-testid="sandbox-level" options={[
            {value: 'off', label: t('toolsPanel.off')},
            {value: 'standard', label: t('toolsPanel.standard')},
            {value: 'strict', label: t('toolsPanel.strict')},
          ]} />
        <Button type="primary" onClick={check}
          data-testid="sandbox-check">{t('toolsPanel.check')}</Button>
      </Space.Compact>
      {result && (
        <Alert type={result.allowed ? 'success' : 'error'}
          style={{ marginTop: 12 }} showIcon
          message={result.allowed ? 'Command allowed' : 'Command blocked'}
          description={result.reason || `Policy: ${result.policy_level}`} />
      )}
    </div>
  );
};

// ============= Verify =============
const VerifyTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const t = useT();
  const [focus, setFocus] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [msgApi, ctx] = message.useMessage();

  const run = async () => {
    setRunning(true);
    try {
      const r = await api.post<any>(`/borrowed/${projectId}/verify`,
        { focus });
      setResult(r.data);
      msgApi[r.data.ok ? 'success' : 'warning'](
        r.data.ok ? 'Verified' : 'Verification failed');
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'failed');
    } finally { setRunning(false); }
  };

  const exportTraj = async () => {
    try {
      const r = await api.post<any>(`/borrowed/${projectId}/trajectory/export`, {});
      msgApi.success(`Exported ${r.data.rows} rows to ${r.data.path}`);
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'failed');
    }
  };

  return (
    <div style={{ padding: '8px 0' }}>
      {ctx}
      <Title level={5}>{t('toolsPanel.verifyRunTheReviewerAgent')}</Title>
      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t('toolsPanel.manuallyReRunTheReviewerAgentUsefulAfterEditingA')}
      </Paragraph>
      <Input value={focus} onChange={e => setFocus(e.target.value)}
        placeholder={t('toolsPanel.optionalFocusFilePathOrAll')}
        style={{ marginBottom: 8 }} />
      <Space>
        <Button type="primary" icon={<CheckCircleOutlined />}
          loading={running} onClick={run}
          data-testid="verify-run">{t('toolsPanel.runReviewer')}</Button>
        <Button icon={<ExportOutlined />} onClick={exportTraj}
          data-testid="trajectory-export">{t('toolsPanel.exportTrajectory')}</Button>
      </Space>
      {result && (
        <Card size="small" style={{ marginTop: 12 }}
              title={`Verdict: ${result.verdict}`}>
          <Paragraph>{result.summary}</Paragraph>
          {result.findings?.map((f: any, i: number) => (
            <Alert key={i} type="warning"
              message={`${f.severity || 'info'}: ${f.title || ''}`}
              description={f.message || f.detail} />
          ))}
        </Card>
      )}
    </div>
  );
};

// Advanced (Async + A2A + Compaction + Approval + Skills + Hooks + Sandbox).
// R38.6.3: source-attribution stripped (the cloud task / the self-improving agent / Gemini etc).
const AdvancedTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const t = useT();
  const [msgApi, ctx] = message.useMessage();
  const [jobs, setJobs] = useState<any[]>([]);
  const [a2a, setA2a] = useState<any[]>([]);
  const [approval, setApproval] = useState('suggest');
  const [showA2a, setShowA2a] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    try {
      const [j, a, ap] = await Promise.all([
        api.get<{jobs: any[]}>('/borrowed/async/jobs'),
        api.get<{agents: any[]}>('/borrowed/a2a/agents'),
        api.get<{mode: string; label: string}>(`/borrowed/${projectId}/approval`),
      ]);
      setJobs(j.data.jobs || []);
      setA2a(a.data.agents || []);
      setApproval(ap.data.mode || 'suggest');
    } catch {/* ignore */}
  }, [projectId]);

  useEffect(() => { if (projectId) load(); }, [projectId, load]);

  const submitJob = async () => {
    const task = form.getFieldValue('task') as string;
    if (!task) return;
    try {
      await api.post('/borrowed/async/submit',
        { task, project_id: projectId });
      msgApi.success(t('toolsPanel.asyncJobSubmitted'));
      form.resetFields();
      load();
    } catch (e: any) { msgApi.error(e?.response?.data?.detail || 'failed'); }
  };

  const setMode = async (mode: string) => {
    try {
      await api.put(`/borrowed/${projectId}/approval`, { mode });
      setApproval(mode);
      msgApi.success(`<Card size="small" title="Skills" style={{ marginBottom: 8 }}>
        <SkillsTab projectId={projectId} />
      </Card>
      <Card size="small" title="Hooks" style={{ marginBottom: 8 }}>
        <HooksTab projectId={projectId} />
      </Card>
      <Card size="small" title="Sandbox" style={{ marginBottom: 8 }}>
        <SandboxTab projectId={projectId} />
      </Card>
      Approval mode = ${mode}`);
    } catch (e: any) { msgApi.error(e?.response?.data?.detail || 'failed'); }
  };

  const addA2a = async (values: any) => {
    try {
      await api.post('/borrowed/a2a/register', values);
      msgApi.success(t('toolsPanel.a2aRegistered'));
      setShowA2a(false);
      form.resetFields();
      load();
    } catch (e: any) { msgApi.error(e?.response?.data?.detail || 'failed'); }
  };

  return (
    <div style={{ padding: '8px 0' }}>
      {ctx}
      <Title level={5}>{t('toolsPanel.advanced2')}</Title>

      <Card size="small" title={t('toolsPanel.approvalMode')}
            style={{ marginBottom: 12 }}>
        <Radio.Group value={approval} onChange={e => setMode(e.target.value)}>
          <Radio.Button value="suggest">{t('toolsPanel.suggest')}</Radio.Button>
          <Radio.Button value="edit">{t('toolsPanel.edit')}</Radio.Button>
          <Radio.Button value="full-auto">{t('toolsPanel.fullAuto')}</Radio.Button>
        </Radio.Group>
        <Paragraph type="secondary" style={{ fontSize: 11, marginTop: 4 }}>
          {approval === 'suggest' && 'Every file write and shell command needs your OK.'}
          {approval === 'edit' && 'File writes are automatic; shell commands still need your OK.'}
          {approval === 'full-auto' && 'Nothing asks unless it\'s explicitly denied. Network disabled.'}
        </Paragraph>
      </Card>

      <Card size="small" title={t('toolsPanel.asyncTasks')}
            style={{ marginBottom: 12 }}>
        <Form form={form} layout="inline"
          onFinish={submitJob}>
          <Form.Item name="task" style={{ flex: 1, marginInlineEnd: 8 }}>
            <Input placeholder={t('toolsPanel.taskDescriptionRunsInBackground')} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit"
              icon={<SendOutlined />}>{t('toolsPanel.submit')}</Button>
          </Form.Item>
        </Form>
        {jobs.length > 0 && (
          <List size="small" style={{ marginTop: 8 }}
            dataSource={jobs.slice(0, 5)}
            renderItem={(j: any) => (
              <List.Item>
                <Space>
                  <Tag color={j.status === 'done' ? 'green' :
                              j.status === 'failed' ? 'red' : 'blue'}>
                    {j.status}
                  </Tag>
                  <Text ellipsis style={{ maxWidth: 360 }}>{j.task}</Text>
                </Space>
              </List.Item>
            )} />
        )}
      </Card>

      <Card size="small" title={t('toolsPanel.a2aRemoteAgents')}
            style={{ marginBottom: 12 }}>
        <Button size="small" icon={<PlusOutlined />}
          onClick={() => setShowA2a(true)} style={{ marginBottom: 8 }}>
          {t('toolsPanel.registerAgent')}
        </Button>
        {a2a.length > 0 && (
          <List size="small"
            dataSource={a2a}
            renderItem={(a: any) => (
              <List.Item>
                <Space>
                  <Tag color={a.status === 'reachable' ? 'green' : 'red'}>
                    {a.status}
                  </Tag>
                  <Text strong>{a.name}</Text>
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {a.endpoint}
                  </Text>
                </Space>
              </List.Item>
            )} />
        )}
        <Modal title={t('toolsPanel.registerA2AAgent')} open={showA2a}
          onCancel={() => setShowA2a(false)}
          onOk={() => form.submit()}>
          <Form form={form} onFinish={addA2a} layout="vertical">
            <Form.Item name="name" label={t('toolsPanel.name')} rules={[{required: true}]}>
              <Input placeholder="remote-coder" />
            </Form.Item>
            <Form.Item name="endpoint" label={t('toolsPanel.endpointURL')} rules={[{required: true}]}>
              <Input placeholder={t('toolsPanel.httpsAgentExampleComA2a')} />
            </Form.Item>
            <Form.Item name="auth_token" label={t('toolsPanel.authTokenOptional')}>
              <Input.Password />
            </Form.Item>
          </Form>
        </Modal>
      </Card>

      <Card size="small" title={t('toolsPanel.lspCheck')}>
        <LspInline projectId={projectId} />
      </Card>
    </div>
  );
};

const LspInline: React.FC<{ projectId: string }> = ({ projectId }) => {
  const t = useT();
  const [file, setFile] = useState('');
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [msgApi, ctx] = message.useMessage();

  const check = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const r = await api.post<any>(`/borrowed/${projectId}/lsp/check`,
        { file_path: file });
      setResult(r.data);
      if (r.data.diagnostics?.length) {
        msgApi.warning(`${r.data.diagnostics.length} issues`);
      } else {
        msgApi.success(t('toolsPanel.noIssues'));
      }
    } catch (e: any) { msgApi.error(e?.response?.data?.detail || 'failed'); }
    finally { setBusy(false); }
  };

  return (
    <div>
      {ctx}
      <Space.Compact style={{ width: '100%' }}>
        <Input value={file} onChange={e => setFile(e.target.value)}
          placeholder={t('toolsPanel.relativeFilePathEGKairosAgentsBasePy')} />
        <Button type="primary" loading={busy} onClick={check}
          data-testid="lsp-check">{t('toolsPanel.check')}</Button>
      </Space.Compact>
      {result && (
        <div style={{ marginTop: 8 }}>
          {result.diagnostics?.length === 0 ? (
            <Alert type="success" message={`${result.engine}: clean`} />
          ) : (
            result.diagnostics?.map((d: any, i: number) => (
              <Alert key={i} type="error" style={{ marginBottom: 4 }}
                message={`Line ${d.line}: ${d.severity}`}
                description={d.message} />
            ))
          )}
        </div>
      )}
    </div>
  );
};

export default ToolsPanel;
