import React, { useState, useEffect, useRef } from 'react';
import { Drawer, Tabs, Select, Switch, Input, Button, Divider, Tag, Space, Typography, message } from 'antd';
import {
  SettingOutlined,
  CodeOutlined,
  AudioOutlined,
  CloudOutlined,
  ToolOutlined,
  LineChartOutlined,
  InfoCircleOutlined,
  ApiOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { useSettingsStore, CoderMode, TtsProvider, SttProvider, LlmProvider } from '../stores/settingsStore';
import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import api from '../api/client';

const { Title, Text } = Typography;

interface Props {
  open: boolean;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Section: Coder mode
// ---------------------------------------------------------------------------

const CoderModePanel: React.FC = () => {
  const tokens = useThemeTokens();
  const mode = useSettingsStore((s) => s.coderMode);
  const setMode = useSettingsStore((s) => s.setCoderMode);

  const MODES: { value: CoderMode; label: string; desc: string }[] = [
    { value: 'default', label: 'Default', desc: 'All tools available; can edit files, run shell, commit.' },
    { value: 'read_only', label: 'Read-only', desc: 'List/read/search only. No file edits, no shell.' },
    { value: 'sandbox', label: 'Sandbox (worktree)', desc: 'Writes are isolated in a git worktree; you review before merging.' },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text style={{ color: tokens.labelSecondary }}>
        The Coder sub-mode controls which tools the agent can call.
      </Text>
      <div>
        {MODES.map((m) => {
          const selected = mode === m.value;
          return (
            <div
              key={m.value}
              onClick={() => setMode(m.value)}
              data-testid={`coder-mode-${m.value}`}
              style={{
                padding: 12,
                marginBottom: 8,
                borderRadius: 6,
                border: `1px solid ${selected ? tokens.brand : tokens.border}`,
                background: selected ? tokens.bgLay1 : 'transparent',
                cursor: 'pointer',
                transition: 'all 0.15s',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <strong style={{ color: tokens.labelPrimary }}>{m.label}</strong>
                {selected && <Tag color="blue">active</Tag>}
              </div>
              <div style={{ color: tokens.labelSecondary, fontSize: 12, marginTop: 4 }}>
                {m.desc}
              </div>
            </div>
          );
        })}
      </div>
    </Space>
  );
};

// ---------------------------------------------------------------------------
// Section: Voice
// ---------------------------------------------------------------------------

const VoicePanel: React.FC = () => {
  const tokens = useThemeTokens();
  const voice = useSettingsStore((s) => s.voice);
  const setVoice = useSettingsStore((s) => s.setVoice);

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text style={{ color: tokens.labelSecondary }}>
        Voice providers used when you click the mic or send text that
        the agent should speak back.
      </Text>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>TTS provider</Text>
        <Select
          style={{ width: '100%', marginTop: 4 }}
          value={voice.ttsProvider}
          onChange={(v: TtsProvider) => setVoice({ ttsProvider: v })}
          options={[
            { value: 'edge', label: 'Microsoft Edge (online, free, high quality)' },
            { value: 'mock', label: 'Mock (offline, silent placeholder)' },
          ]}
        />
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>TTS voice</Text>
        <Input
          style={{ marginTop: 4 }}
          value={voice.ttsVoice}
          onChange={(e) => setVoice({ ttsVoice: e.target.value })}
          placeholder="e.g. en-US-AriaNeural, zh-CN-XiaoxiaoNeural"
        />
        <Text style={{ color: tokens.labelTertiary, fontSize: 11 }}>
          Common voices: en-US-AriaNeural, zh-CN-XiaoxiaoNeural,
          ja-JP-NanamiNeural, de-DE-KatjaNeural
        </Text>
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>STT provider</Text>
        <Select
          style={{ width: '100%', marginTop: 4 }}
          value={voice.sttProvider}
          onChange={(v: SttProvider) => setVoice({ sttProvider: v })}
          options={[
            { value: 'mock', label: 'Mock (offline, deterministic placeholder)' },
            { value: 'whisper', label: 'Whisper (requires faster-whisper)' },
          ]}
        />
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>STT language</Text>
        <Input
          style={{ marginTop: 4 }}
          value={voice.sttLanguage}
          onChange={(e) => setVoice({ sttLanguage: e.target.value })}
          placeholder="e.g. en, zh, ja"
        />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: tokens.labelPrimary }}>Auto-play response audio</Text>
        <Switch
          checked={voice.autoPlay}
          onChange={(checked) => setVoice({ autoPlay: checked })}
        />
      </div>
    </Space>
  );
};

// ---------------------------------------------------------------------------
// Section: MCP
// ---------------------------------------------------------------------------

const McpPanel: React.FC = () => {
  const tokens = useThemeTokens();
  const mcp = useSettingsStore((s) => s.mcp);
  const setMcp = useSettingsStore((s) => s.setMcp);

  const KNOWN_SERVERS = [
    { name: 'filesystem', desc: 'Built-in filesystem server (sandboxed to project dir).' },
    { name: 'github', desc: 'GitHub MCP (requires GITHUB_TOKEN env).' },
    { name: 'postgres', desc: 'Postgres MCP (requires DATABASE_URL env).' },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text style={{ color: tokens.labelSecondary }}>
        Kairos can attach any MCP-compatible server. Built-in
        filesystem is enabled by default; add a custom one via
        <code> .kairos/mcp.yaml</code>.
      </Text>
      <div>
        {KNOWN_SERVERS.map((s) => {
          const enabled = mcp.enabledServers.includes(s.name);
          return (
            <div
              key={s.name}
              style={{
                padding: 10,
                marginBottom: 6,
                borderRadius: 6,
                border: `1px solid ${tokens.border}`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
              }}
            >
              <div>
                <strong style={{ color: tokens.labelPrimary }}>{s.name}</strong>
                <div style={{ color: tokens.labelSecondary, fontSize: 12 }}>{s.desc}</div>
              </div>
              <Switch
                checked={enabled}
                onChange={(checked) => {
                  const next = checked
                    ? Array.from(new Set([...mcp.enabledServers, s.name]))
                    : mcp.enabledServers.filter((x) => x !== s.name);
                  setMcp({ enabledServers: next });
                }}
              />
            </div>
          );
        })}
      </div>
      <Divider style={{ margin: '8px 0' }} />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: tokens.labelPrimary }}>Ask before invoking MCP tools</Text>
        <Switch
          checked={mcp.permissionPrompt}
          onChange={(checked) => setMcp({ permissionPrompt: checked })}
        />
      </div>
    </Space>
  );
};

// ---------------------------------------------------------------------------
// Section: Cloud (S3)
// ---------------------------------------------------------------------------

const CloudPanel: React.FC = () => {
  const tokens = useThemeTokens();
  const cloud = useSettingsStore((s) => s.cloud);
  const setCloud = useSettingsStore((s) => s.setCloud);

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text style={{ color: tokens.labelSecondary }}>
        S3-compatible object storage. Works with AWS S3, MinIO,
        Cloudflare R2, Backblaze B2, Wasabi, etc. Credentials are
        read from the server environment, not stored here.
      </Text>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>Default bucket</Text>
        <Input
          style={{ marginTop: 4 }}
          value={cloud.s3Bucket}
          onChange={(e) => setCloud({ s3Bucket: e.target.value })}
          placeholder="my-project-assets"
        />
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>Region</Text>
        <Input
          style={{ marginTop: 4 }}
          value={cloud.s3Region}
          onChange={(e) => setCloud({ s3Region: e.target.value })}
          placeholder="us-east-1"
        />
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>Custom endpoint (optional)</Text>
        <Input
          style={{ marginTop: 4 }}
          value={cloud.s3Endpoint}
          onChange={(e) => setCloud({ s3Endpoint: e.target.value })}
          placeholder="http://minio.local:9000"
        />
        <Text style={{ color: tokens.labelTertiary, fontSize: 11 }}>
          Leave empty for AWS; set to your MinIO/R2/B2 URL otherwise.
        </Text>
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>Addressing style</Text>
        <Select
          style={{ width: '100%', marginTop: 4 }}
          value={cloud.addressingStyle}
          onChange={(v) => setCloud({ addressingStyle: v })}
          options={[
            { value: 'auto', label: 'Auto (recommended)' },
            { value: 'virtual', label: 'Virtual-hosted (AWS default)' },
            { value: 'path', label: 'Path-style (MinIO etc.)' },
          ]}
        />
      </div>
    </Space>
  );
};

// ---------------------------------------------------------------------------
// Section: Metrics
// ---------------------------------------------------------------------------

const MetricsPanel: React.FC = () => {
  const tokens = useThemeTokens();
  const metrics = useSettingsStore((s) => s.metrics);
  const setMetrics = useSettingsStore((s) => s.setMetrics);

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text style={{ color: tokens.labelSecondary }}>
        Kairos exposes a Prometheus-compatible <code>/metrics</code>
        endpoint for monitoring request count, latency, agent
        invocations, and loop outcomes.
      </Text>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: tokens.labelPrimary }}>Show counter in footer</Text>
        <Switch
          checked={metrics.showInFooter}
          onChange={(checked) => setMetrics({ showInFooter: checked })}
        />
      </div>
      <Button
        type="default"
        onClick={() => window.open('/metrics', '_blank')}
      >
        Open /metrics
      </Button>
    </Space>
  );
};


// ---------------------------------------------------------------------------
// Provider panel (Round 37 — focused on OpenAI / Anthropic custom URLs)
// ---------------------------------------------------------------------------

const ProviderPanel: React.FC = () => {
  const tokens = useThemeTokens();
  const provider = useSettingsStore((s) => s.provider);
  const setProvider = useSettingsStore((s) => s.setProvider);

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text style={{ color: tokens.labelSecondary }}>
        Pick the LLM the Coder and Reviewer agents use. Both providers
        accept a custom base URL so you can point Kairos at OpenAI,
        Anthropic, or any compatible proxy (Azure, Together, vLLM,
        LiteLLM, etc.). Click <strong>Test connection</strong> to
        verify your key + URL before saving.
      </Text>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>Active provider</Text>
        <Select
          style={{ width: '100%', marginTop: 4 }}
          value={provider.active}
          onChange={(v: LlmProvider) => setProvider({ active: v })}
          options={[
            { value: 'openai', label: 'OpenAI (or any OpenAI-compatible API)' },
            { value: 'anthropic', label: 'Anthropic (or any Anthropic-compatible API)' },
          ]}
        />
      </div>
      {provider.active === 'openai' ? (
        <OpenAICompatForm
          value={provider.openai}
          onChange={(patch) => setProvider({ openai: { ...provider.openai, ...patch } })}
        />
      ) : (
        <AnthropicCompatForm
          value={provider.anthropic}
          onChange={(patch) => setProvider({ anthropic: { ...provider.anthropic, ...patch } })}
        />
      )}
      <div style={{ fontSize: 11, color: tokens.labelTertiary,
                    borderTop: `1px solid ${tokens.border}`, paddingTop: 8 }}>
        Keys are stored in the frontend only (localStorage via
        zustand persist). The backend never sees them unless you
        click <em>Test connection</em>.
      </div>
    </Space>
  );
};

const OpenAICompatForm: React.FC<{
  value: { baseUrl: string; apiKey: string; model: string };
  onChange: (patch: Partial<{ baseUrl: string; apiKey: string; model: string }>) => void;
}> = ({ value, onChange }) => {
  const tokens = useThemeTokens();
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<null | { ok: boolean; detail: string }>(null);

  const test = async () => {
    if (!value.baseUrl.trim() || !value.apiKey.trim()) {
      setTestResult({ ok: false, detail: 'Base URL and API key are both required.' });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const r = await api.post<{ ok: boolean; status: number; detail: string }>(
        '/config/test_connection',
        { provider: 'openai', base_url: value.baseUrl, api_key: value.apiKey,
          model: value.model },
      );
      setTestResult({ ok: !!r.data.ok, detail: r.data.detail || '(no detail)' });
    } catch (e: any) {
      setTestResult({ ok: false,
        detail: e?.response?.data?.detail || e?.message || 'request failed' });
    } finally {
      setTesting(false);
    }
  };

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>Base URL</Text>
        <Input
          style={{ marginTop: 4 }}
          value={value.baseUrl}
          onChange={(e) => onChange({ baseUrl: e.target.value })}
          placeholder="https://api.openai.com/v1"
        />
        <Text style={{ color: tokens.labelTertiary, fontSize: 11 }}>
          Defaults to OpenAI. Point at a proxy (Azure, Together, vLLM, …)
          by setting a different URL.
        </Text>
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>API key</Text>
        <Input.Password
          style={{ marginTop: 4 }}
          value={value.apiKey}
          onChange={(e) => onChange({ apiKey: e.target.value })}
          placeholder="sk-…"
        />
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>Model</Text>
        <Input
          style={{ marginTop: 4 }}
          value={value.model}
          onChange={(e) => onChange({ model: e.target.value })}
          placeholder="gpt-4o, gpt-4o-mini, o1-mini, …"
        />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Button
          data-testid="openai-test-connection"
          onClick={test}
          loading={testing}
          disabled={testing}
        >
          Test connection
        </Button>
        {testResult && (
          <Tag color={testResult.ok ? 'green' : 'red'}
               data-testid="openai-test-result"
               style={{ maxWidth: 280, overflow: 'hidden',
                       textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
               title={testResult.detail}>
            {testResult.ok ? 'OK · ' : 'Fail · '}
            {testResult.detail}
          </Tag>
        )}
      </div>
    </Space>
  );
};

const AnthropicCompatForm: React.FC<{
  value: { baseUrl: string; apiKey: string; model: string };
  onChange: (patch: Partial<{ baseUrl: string; apiKey: string; model: string }>) => void;
}> = ({ value, onChange }) => {
  const tokens = useThemeTokens();
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<null | { ok: boolean; detail: string }>(null);

  const test = async () => {
    if (!value.baseUrl.trim() || !value.apiKey.trim()) {
      setTestResult({ ok: false, detail: 'Base URL and API key are both required.' });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const r = await api.post<{ ok: boolean; status: number; detail: string }>(
        '/config/test_connection',
        { provider: 'anthropic', base_url: value.baseUrl, api_key: value.apiKey,
          model: value.model },
      );
      setTestResult({ ok: !!r.data.ok, detail: r.data.detail || '(no detail)' });
    } catch (e: any) {
      setTestResult({ ok: false,
        detail: e?.response?.data?.detail || e?.message || 'request failed' });
    } finally {
      setTesting(false);
    }
  };

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>Base URL</Text>
        <Input
          style={{ marginTop: 4 }}
          value={value.baseUrl}
          onChange={(e) => onChange({ baseUrl: e.target.value })}
          placeholder="https://api.anthropic.com"
        />
        <Text style={{ color: tokens.labelTertiary, fontSize: 11 }}>
          Anthropic-compatible proxies (LiteLLM, AWS Bedrock with
          an adapter) work too — just point at the right host.
        </Text>
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>API key</Text>
        <Input.Password
          style={{ marginTop: 4 }}
          value={value.apiKey}
          onChange={(e) => onChange({ apiKey: e.target.value })}
          placeholder="sk-ant-…"
        />
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>Model</Text>
        <Input
          style={{ marginTop: 4 }}
          value={value.model}
          onChange={(e) => onChange({ model: e.target.value })}
          placeholder="claude-3-5-sonnet-latest, claude-3-opus-…"
        />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Button
          data-testid="anthropic-test-connection"
          onClick={test}
          loading={testing}
          disabled={testing}
        >
          Test connection
        </Button>
        {testResult && (
          <Tag color={testResult.ok ? 'green' : 'red'}
               data-testid="anthropic-test-result"
               style={{ maxWidth: 280, overflow: 'hidden',
                       textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
               title={testResult.detail}>
            {testResult.ok ? 'OK · ' : 'Fail · '}
            {testResult.detail}
          </Tag>
        )}
      </div>
    </Space>
  );
};

// ---------------------------------------------------------------------------
// Section: Skills (round 8 — hot-reload)
// ---------------------------------------------------------------------------

const SkillsPanel: React.FC<{ projectId: string | null }> = ({ projectId }) => {
  const tokens = useThemeTokens();
  const [busy, setBusy] = useState(false);
  const [skills, setSkills] = useState<string[]>([]);
  const [count, setCount] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Auto-load the skill list when the panel mounts / project changes.
  useEffect(() => {
    if (projectId) {
      refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const refresh = async () => {
    if (!projectId) {
      setErr('No project selected');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post(`/projects/${projectId}/skills/reload`);
      const data = r.data || {};
      setCount(typeof data.count === 'number' ? data.count : 0);
      setSkills(Array.isArray(data.names) ? data.names : []);
    } catch (e: any) {
      setErr(e?.response?.data?.detail || e?.message || 'Reload failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text style={{ color: tokens.labelSecondary }}>
        Skills are reusable instruction snippets discovered from
        <code> .kairos/skills/</code> in your project. The
        SkillsWatcher polls every second and reloads on change —
        use this button to force an immediate refresh.
      </Text>
      <Space>
        <Button
          icon={<ReloadOutlined />}
          loading={busy}
          onClick={refresh}
          disabled={!projectId}
        >
          Reload now
        </Button>
        {count !== null && (
          <Tag color="blue">{count} skill{count === 1 ? '' : 's'}</Tag>
        )}
      </Space>
      {err && <Text type="danger" style={{ fontSize: 12 }}>{err}</Text>}
      {skills.length > 0 && (
        <div
          style={{
            maxHeight: 220,
            overflow: 'auto',
            border: `1px solid ${tokens.border}`,
            borderRadius: 6,
            padding: 8,
            background: tokens.bgLay1,
          }}
        >
          {skills.map((name) => (
            <div
              key={name}
              style={{
                padding: '4px 6px',
                fontSize: 12,
                color: tokens.labelPrimary,
                fontFamily: 'ui-monospace, SFMono-Regular, monospace',
              }}
            >
              <ThunderboltOutlined style={{ marginRight: 6, color: tokens.brand }} />
              {name}
            </div>
          ))}
        </div>
      )}
    </Space>
  );
};

// ---------------------------------------------------------------------------
// Section: About
// ---------------------------------------------------------------------------

const AboutPanel: React.FC = () => {
  const tokens = useThemeTokens();
  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Title level={4} style={{ color: tokens.labelPrimary, margin: 0 }}>
        Kairos Code
      </Title>
      <Text style={{ color: tokens.labelSecondary }}>
        Multi-agent collaboration platform. Coder &lt;-&gt; Reviewer loop,
        MCP, agents.md / skills, manifest, sandbox, voice, cloud.
      </Text>
      <Text style={{ color: tokens.labelTertiary, fontSize: 12 }}>
        Model: MiniMax M3
      </Text>
    </Space>
  );
};

// ---------------------------------------------------------------------------
// Drawer
// ---------------------------------------------------------------------------

export const SettingsDrawer: React.FC<Props> = ({ open, onClose }) => {
  const tokens = useThemeTokens();
  const [tab, setTab] = useState('coder');
  // Use the current project (from chat store) for the Skills tab.
  const currentProject = useChatStore((s) => s.currentProject);
  const projectId = currentProject?.id ?? null;

  // Debounced sync to /api/projects/settings so the user's knobs
  // survive a server restart. Coder mode is synced separately
  // (per-project) via the /coder_mode endpoint.
  const voice = useSettingsStore((s) => s.voice);
  const mcp = useSettingsStore((s) => s.mcp);
  const cloud = useSettingsStore((s) => s.cloud);
  const metrics = useSettingsStore((s) => s.metrics);
  const provider = useSettingsStore((s) => s.provider);
  const setVoice = useSettingsStore((s) => s.setVoice);
  const setMcp = useSettingsStore((s) => s.setMcp);
  const setCloud = useSettingsStore((s) => s.setCloud);
  const setMetrics = useSettingsStore((s) => s.setMetrics);
  const setProvider = useSettingsStore((s) => s.setProvider);

  // Initial load (only once per open, to avoid clobbering user edits
  // mid-session if the backend is briefly unreachable). R37: also
  // gracefully handle the old (R8) provider shape that the backend
  // might still have on disk (apiKeyEnv/ollamaBaseUrl/...); the new
  // shape is { active, openai:{...}, anthropic:{...} }.
  const didLoad = useRef(false);
  useEffect(() => {
    if (!open || didLoad.current) return;
    didLoad.current = true;
    api.get('/projects/settings').then((r) => {
      const d = r.data || {};
      if (d.voice) setVoice(d.voice);
      if (d.mcp) setMcp(d.mcp);
      if (d.cloud) setCloud(d.cloud);
      if (d.metrics) setMetrics(d.metrics);
      if (d.provider) {
        // Migrate legacy {apiKeyEnv, ollamaBaseUrl, ollamaModel} shape
        // if the user has it on disk.
        if ('apiKeyEnv' in d.provider || 'ollamaBaseUrl' in d.provider) {
          // Treat the old apiKeyEnv as a hint for the active
          // provider's key. If it was OPENAI_API_KEY the key was the
          // env-var NAME not the value, so we can't recover it —
          // the user has to re-paste the key. Leave apiKey blank.
          const active = d.provider.active || 'openai';
          setProvider({
            active: active === 'anthropic' ? 'anthropic' : 'openai',
            openai: { baseUrl: 'https://api.openai.com/v1',
                      apiKey: '', model: 'gpt-4o' },
            anthropic: { baseUrl: 'https://api.anthropic.com',
                          apiKey: '', model: 'claude-3-5-sonnet-latest' },
          });
        } else {
          setProvider(d.provider);
        }
      }
    }).catch(() => { /* offline / first paint — keep defaults */ });
  }, [open, setVoice, setMcp, setCloud, setMetrics, setProvider]);

  // Debounced save on any change to voice/mcp/cloud/metrics/provider.
  useEffect(() => {
    if (!didLoad.current) return;
    const t = setTimeout(() => {
      api.post('/projects/settings', { voice, mcp, cloud, metrics, provider })
        .catch(() => message.error('Failed to save settings'));
    }, 400);
    return () => clearTimeout(t);
  }, [voice, mcp, cloud, metrics, provider]);

  return (
    <Drawer
      title={
        <span>
          <SettingOutlined style={{ marginRight: 8 }} />
          Settings
        </span>
      }
      placement="right"
      width={420}
      open={open}
      onClose={onClose}
      destroyOnClose
      styles={{ body: { padding: 0, background: tokens.bgBase } }}
    >
      <Tabs
        activeKey={tab}
        onChange={setTab}
        tabPosition="top"
        style={{ padding: '0 16px' }}
        items={[
          {
            key: 'coder',
            label: <span><CodeOutlined /> Coder</span>,
            children: <CoderModePanel />,
          },
          {
            key: 'voice',
            label: <span><AudioOutlined /> Voice</span>,
            children: <VoicePanel />,
          },
          {
            key: 'mcp',
            label: <span><ToolOutlined /> MCP</span>,
            children: <McpPanel />,
          },
          {
            key: 'cloud',
            label: <span><CloudOutlined /> Cloud</span>,
            children: <CloudPanel />,
          },
          {
            key: 'metrics',
            label: <span><LineChartOutlined /> Metrics</span>,
            children: <MetricsPanel />,
          },
          {
            key: 'provider',
            label: <span><RobotOutlined /> LLM Models</span>,
            children: <ProviderPanel />,
          },
          {
            key: 'skills',
            label: <span><ThunderboltOutlined /> Skills</span>,
            children: <SkillsPanel projectId={projectId} />,
          },
          {
            key: 'about',
            label: <span><InfoCircleOutlined /> About</span>,
            children: <AboutPanel />,
          },
        ]}
      />
    </Drawer>
  );
};
