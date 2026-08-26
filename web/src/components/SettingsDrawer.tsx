import React, { useState } from 'react';
import { Drawer, Tabs, Select, Switch, Input, Button, Divider, Tag, Space, Typography } from 'antd';
import {
  SettingOutlined,
  CodeOutlined,
  AudioOutlined,
  CloudOutlined,
  ToolOutlined,
  LineChartOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons';
import { useSettingsStore, CoderMode, TtsProvider, SttProvider } from '../stores/settingsStore';
import { useThemeTokens } from '../hooks/useThemeTokens';

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
            key: 'about',
            label: <span><InfoCircleOutlined /> About</span>,
            children: <AboutPanel />,
          },
        ]}
      />
    </Drawer>
  );
};
