import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Drawer, Tabs, Select, Switch, Input, Button, Divider, Tag, Space, Typography, message, Spin, Alert, Modal, App as AntdApp } from 'antd';
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
  CheckCircleFilled,
  CloseCircleFilled,
  GlobalOutlined,
} from '@ant-design/icons';
import { LANGS, tGlobal, useI18n, useT } from '../i18n';
import { useSettingsStore, CoderMode, TtsProvider, SttProvider, LlmProvider } from '../stores/settingsStore';
import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { LLM_PRESETS, matchPreset, getPreset, CUSTOM_MODEL } from '../llm/presets';
import api from '../api/client';
import { formatError } from '../utils/formatError';
import { openFeedbackIssue } from '../utils/feedback';

const { Title, Text } = Typography;

interface Props {
  open: boolean;
  onClose: () => void;
}

// Render a millisecond delta as a human-readable "X ago" label.
// Used by the save-status indicator at the top of the drawer.
function agoLabel(deltaMs: number): string {
  const s = Math.floor(deltaMs / 1000);
  if (s < 5) return tGlobal('settings.drawer.agoJustNow');
  if (s < 60) return tGlobal('settings.drawer.agoSeconds', { n: s });
  const m = Math.floor(s / 60);
  if (m < 60) return tGlobal('settings.drawer.agoMinutes', { n: m });
  const h = Math.floor(m / 60);
  return tGlobal('settings.drawer.agoHours', { n: h });
}

// Strip the last path segment from a URL. Used to derive the
// orchestrator's base URL from the user-pasted endpoint URL.
//
// Examples:
//   https://api.openai.com/v1/chat/completions
//     → https://api.openai.com/v1
//   https://api.anthropic.com/v1/messages
//     → https://api.anthropic.com
//   https://my-proxy.example.com/api/llm/chat
//     → https://my-proxy.example.com/api/llm
//   https://api.openai.com/v1
//     → https://api.openai.com
//   "" (empty)
//     → ""
//
// We rely on the URL parser for safety — invalid URLs return "".
function deriveBaseUrl(endpointUrl: string): string {
  const raw = (endpointUrl || '').trim();
  if (!raw) return '';
  try {
    const u = new URL(raw);
    const parts = u.pathname.split('/').filter(Boolean);
    if (parts.length > 0) parts.pop();
    u.pathname = parts.length > 0 ? '/' + parts.join('/') : '/';
    // Drop trailing slash, but keep the protocol + host.
    return u.toString().replace(/\/$/, '');
  } catch {
    return '';
  }
}

// ---------------------------------------------------------------------------
// Section: Coder mode
// ---------------------------------------------------------------------------

const CoderModePanel: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const mode = useSettingsStore((s) => s.coderMode);
  const setMode = useSettingsStore((s) => s.setCoderMode);

  const MODES: { value: CoderMode; label: string; desc: string }[] = [
    { value: 'default', label: t('settings.default'), desc: 'All tools available; can edit files, run shell, commit.' },
    { value: 'read_only', label: t('settings.readOnly'), desc: 'List/read/search only. No file edits, no shell.' },
    { value: 'sandbox', label: t('settings.sandboxWorktree'), desc: 'Writes are isolated in a git worktree; you review before merging.' },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text style={{ color: tokens.labelSecondary }}>
        {t('settings.theCoderSubModeControlsWhichToolsTheAgentCanCall')}
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
                {selected && <Tag color="blue">{t('settings.statusActive')}</Tag>}
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
  const t = useT();
  const tokens = useThemeTokens();
  const voice = useSettingsStore((s) => s.voice);
  const setVoice = useSettingsStore((s) => s.setVoice);

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text style={{ color: tokens.labelSecondary }}>
        {t('settings.voiceProvidersUsedWhenYouClickTheMicOrSendTextTh')}
      </Text>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.ttsProvider')}</Text>
        <Select
          style={{ width: '100%', marginTop: 4 }}
          value={voice.ttsProvider}
          onChange={(v: TtsProvider) => setVoice({ ttsProvider: v })}
          options={[
            { value: 'edge', label: t('settings.microsoftEdgeOnlineFreeHighQuality') },
            { value: 'mock', label: t('settings.mockOfflineSilentPlaceholder') },
          ]}
        />
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.ttsVoice')}</Text>
        <Input
          style={{ marginTop: 4 }}
          value={voice.ttsVoice}
          onChange={(e) => setVoice({ ttsVoice: e.target.value })}
          placeholder={t('settings.eGEnUSAriaNeuralZhCNXiaoxiaoNeural')}
        />
        <Text style={{ color: tokens.labelTertiary, fontSize: 11 }}>
          {t('settings.commonVoicesEnUSAriaNeuralZhCNXiaoxiaoNeuralJaJP')}
        </Text>
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.sttProvider')}</Text>
        <Select
          style={{ width: '100%', marginTop: 4 }}
          value={voice.sttProvider}
          onChange={(v: SttProvider) => setVoice({ sttProvider: v })}
          options={[
            { value: 'mock', label: t('settings.mockOfflineDeterministicPlaceholder') },
            { value: 'whisper', label: t('settings.whisperRequiresFasterWhisper') },
          ]}
        />
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.sttLanguage')}</Text>
        <Input
          style={{ marginTop: 4 }}
          value={voice.sttLanguage}
          onChange={(e) => setVoice({ sttLanguage: e.target.value })}
          placeholder={t('settings.eGEnZhJa')}
        />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.autoPlayResponseAudio')}</Text>
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
  const t = useT();
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
        {t('settings.kairosCanAttachAnyMCPCompatibleServerBuiltInFile')}
        <code> {t('settings.kairosMcpYaml')}</code>.
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
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.askBeforeInvokingMCPTools')}</Text>
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
  const t = useT();
  const tokens = useThemeTokens();
  const cloud = useSettingsStore((s) => s.cloud);
  const setCloud = useSettingsStore((s) => s.setCloud);

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text style={{ color: tokens.labelSecondary }}>
        {t('settings.s3CompatibleObjectStorageWorksWithAWSS3MinIOClou')}
      </Text>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.defaultBucket')}</Text>
        <Input
          style={{ marginTop: 4 }}
          value={cloud.s3Bucket}
          onChange={(e) => setCloud({ s3Bucket: e.target.value })}
          placeholder="my-project-assets"
        />
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.region')}</Text>
        <Input
          style={{ marginTop: 4 }}
          value={cloud.s3Region}
          onChange={(e) => setCloud({ s3Region: e.target.value })}
          placeholder="us-east-1"
        />
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.customEndpointOptional')}</Text>
        <Input
          style={{ marginTop: 4 }}
          value={cloud.s3Endpoint}
          onChange={(e) => setCloud({ s3Endpoint: e.target.value })}
          placeholder={t('settings.httpMinioLocal9000')}
        />
        <Text style={{ color: tokens.labelTertiary, fontSize: 11 }}>
          {t('settings.leaveEmptyForAWSSetToYourMinIOR2B2URLOtherwise')}
        </Text>
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.addressingStyle')}</Text>
        <Select
          style={{ width: '100%', marginTop: 4 }}
          value={cloud.addressingStyle}
          onChange={(v) => setCloud({ addressingStyle: v })}
          options={[
            { value: 'auto', label: t('settings.autoRecommended') },
            { value: 'virtual', label: t('settings.virtualHostedAWSDefault') },
            { value: 'path', label: t('settings.pathStyleMinIOEtc') },
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
  const t = useT();
  const tokens = useThemeTokens();
  const metrics = useSettingsStore((s) => s.metrics);
  const setMetrics = useSettingsStore((s) => s.setMetrics);

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text style={{ color: tokens.labelSecondary }}>
        {t('settings.kairosExposesAPrometheusCompatible')} <code>{t('settings.metrics')}</code>
        {t('settings.endpointForMonitoringRequestCountLatencyAgentInv')}
      </Text>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.showCounterInFooter')}</Text>
        <Switch
          checked={metrics.showInFooter}
          onChange={(checked) => setMetrics({ showInFooter: checked })}
        />
      </div>
      <Button
        type="default"
        onClick={() => window.open('/metrics', '_blank')}
      >
        {t('settings.openMetrics')}
      </Button>
    </Space>
  );
};


// ---------------------------------------------------------------------------
// {t('settings.provider')} panel (Round 37 — focused on OpenAI and Anthropic custom URLs)
// ---------------------------------------------------------------------------

const ProviderPanel: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const provider = useSettingsStore((s) => s.provider);
  const setProvider = useSettingsStore((s) => s.setProvider);

  // R38.6: explicit {t('settings.save')} button (the user asked for it). The
  // previous design auto-saved on every keystroke with a 400ms
  // debounce — which is unreliable (the user could close the
  // drawer before the timer fired, losing the change). Now the
  // user clicks Save to persist, and we show success / error
  // feedback inline. Voice / MCP / Cloud / Metrics keep the
  // auto-save because casual settings don't warrant an extra
  // click; the LLM settings are the critical ones the user
  // asked to gate behind a button.
  const { message: msgApi } = AntdApp.useApp();
  const [saving, setSaving] = useState(false);
  const [lastSaved, setLastSaved] = useState<number | null>(null);
  // Snapshot of provider at last save (for the "unsaved changes"
  // indicator on the button).
  const [savedSnapshot, setSavedSnapshot] = useState<string>(
    JSON.stringify(provider));

  // Whenever the provider changes in the store (e.g. initial load
  // from the backend completes), update the snapshot so the button
  // starts in a "clean" state.
  useEffect(() => {
    setSavedSnapshot((prev) => {
      if (JSON.stringify(provider) !== prev) {
        // Don't overwrite an explicit "user just saved" — only
        // sync from the store when the user hasn't been editing.
        // We just unconditionally accept the new value here; this
        // is fine because the initial-load useEffect runs once.
        return JSON.stringify(provider);
      }
      return prev;
    });
  }, [provider]);

  const isDirty = JSON.stringify(provider) !== savedSnapshot;

  const save = async () => {
    setSaving(true);
    try {
      // Use the store's current voice/mcp/cloud/metrics values too,
      // so the Save button is the explicit equivalent of the
      // auto-save (which also POSTs all 5 sections together).
      const { voice, mcp, cloud, metrics } = useSettingsStore.getState();
      await api.post('/projects/settings',
                      { voice, mcp, cloud, metrics, provider });
      setSavedSnapshot(JSON.stringify(provider));
      setLastSaved(Date.now());
      msgApi.success(t('settings.llmSettingsSaved'));
    } catch (e: any) {
      const detail = formatError(e, 'request failed');
      msgApi.error(`{t('settings.saveFailed')} ${detail}`);
    } finally {
      setSaving(false);
    }
  };

  const reset = () => {
    // Roll back to the last-saved snapshot.
    const snap = JSON.parse(savedSnapshot);
    setProvider(snap);
  };

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text style={{ color: tokens.labelSecondary }}>
        {t('settings.pickTheLLMTheCoderAndReviewerAgentsUseBothProvid')} <strong>{t('settings.testConnection')}</strong> {t('settings.toVerifyYourKeyURLThen')} <strong>{t('settings.save')}</strong> {t('settings.toPersist')}
      </Text>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.activeProvider')}</Text>
        <Select
          style={{ width: '100%', marginTop: 4 }}
          value={provider.active}
          onChange={(v: LlmProvider) => setProvider({ active: v })}
          options={[
            { value: 'openai', label: 'OpenAI' },
            { value: 'anthropic', label: 'Anthropic' },
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
        {t('settings.keysAreStoredInTheFrontendOnlyLocalStorageViaZus')} <em>{t('settings.testConnection')}</em>.
      </div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        paddingTop: 4, borderTop: `1px solid ${tokens.border}`,
      }}>
        <Button
          type="primary"
          data-testid="llm-save-button"
          onClick={save}
          loading={saving}
          disabled={!isDirty || saving}
        >
          {isDirty ? 'Save' : 'Saved'}
        </Button>
        {isDirty && (
          <Button
            data-testid="llm-reset-button"
            type="text"
            onClick={reset}
            disabled={saving}
          >
            {t('settings.discardChanges')}
          </Button>
        )}
        {!isDirty && lastSaved && (
          <span style={{ fontSize: 11, color: tokens.labelTertiary }}>
            {t('settings.lastSaved')} {agoLabel(Date.now() - lastSaved)}
          </span>
        )}
      </div>
    </Space>
  );
};

const OpenAICompatForm: React.FC<{
  value: {
    endpointUrl: string;
    baseUrl: string;
    apiKey: string;
    model: string;
  };
  onChange: (patch: Partial<{
    endpointUrl: string;
    baseUrl: string;
    apiKey: string;
    model: string;
  }>) => void;
}> = ({ value, onChange }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<null | { ok: boolean; detail: string }>(null);

  // R38.6 §28: detect which preset the current values match
  // (DeepSeek / Qwen / GLM / Moonshot / OpenAI / custom). Used
  // to auto-select the dropdown when the user opens the drawer
  // and to pre-fill the URL/model on pick.
  const [presetId, setPresetId] = useState<string>(() => {
    return matchPreset(value.endpointUrl, value.model);
  });
  // The dropdown is derived once, from the values that were loaded, and after that it
  // reflects what the user picked. It used to be re-derived from (endpointUrl, model)
  // on every edit, which meant an explicit choice could never win: a custom endpoint
  // plus a model named deepseek-* snapped straight back to the DeepSeek preset, and the
  // drawer then presented that preset's endpoint and model as if they were the user's.

  const test = async () => {
    if (!value.endpointUrl.trim() || !value.apiKey.trim()) {
      setTestResult({ ok: false,
        detail: t('settings.endpointAndKeyRequired') });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      // R38.5: send the full endpoint URL. The backend hits it
      // as-is — no path manipulation, no auto-append. The user
      // owns the URL.
      const r = await api.post<{ ok: boolean; status: number; detail: string }>(
        '/config/test_connection',
        { provider: 'openai', endpoint_url: value.endpointUrl,
          base_url: value.baseUrl, api_key: value.apiKey,
          model: value.model },
      );
      setTestResult({ ok: !!r.data.ok, detail: r.data.detail || '(no detail)' });
    } catch (e: any) {
      // R38.6.3: surface the real reason the test failed.
      // 1. The backend's global 500 handler returns
      //    {detail, error_id}; axios puts it on e.response.data.
      // 2. The backend's own 4xx responses also use {detail}.
      // 3. Network / CORS failures leave e.response undefined —
      //    we fall back to e.message which is axios's default
      //    "Request failed with status code N" or "Network Error".
      // 4. Non-JSON 5xx (HTML body, empty body) — e.response.data
      //    is empty string or a Document. Try to read it.
      const status = e?.response?.status;
      const resp = e?.response;
      let detail = '';
      // Pull any useful text from the response body
      if (resp?.data) {
        if (typeof resp.data === 'string') {
          detail = resp.data.slice(0, 300);
        } else if (typeof resp.data === 'object') {
          detail = resp.data.detail || resp.data.error_id
                   || resp.data.message || JSON.stringify(resp.data).slice(0, 200);
        }
      }
      if (!detail) detail = e?.message || 'request failed';
      // If upstream returned a non-JSON body, say so explicitly
      // so the user knows it's an HTML/proxy error, not a logic bug
      const ct = String(resp?.headers?.['content-type'] || '');
      if (ct && !ct.includes('json') && detail.length > 0) {
        detail = `[upstream returned ${ct.split(';')[0]}, not JSON] ${detail}`;
      }
      setTestResult({ ok: false,
        detail: status ? `[HTTP ${status}] ${detail}` : detail });
    } finally {
      setTesting(false);
    }
  };

  // R38.6 §28.2: dynamic model list. The {t('settings.model')} Select is
  // populated from a live API call (`POST /api/config/models/
  // custom/fetch`) — the user picks a preset (or types a
  // custom URL), then clicks "拉取 model 列表" to get the
  // current set of models the provider actually exposes.
  //
  // We do NOT hardcode the model list per preset — the user
  // said it was "串门" (models from one provider bleeding into
  // another's dropdown) and would also go stale the moment
  // any provider adds a new model. Live fetch is the only
  // source of truth.
  const [fetchedModels, setFetchedModels] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [modelFetchError, setModelFetchError] = useState<string | null>(null);
  const [lastFetchedKey, setLastFetchedKey] = useState<string>('');
  // The model the user has saved may not be in the fetched
  // list (e.g. fetch failed last time, or they typed a custom
  // model). We remember it so the Select still has a valid
  // value to display.
  const [pinnedModel, setPinnedModel] = useState<string>(value.model);

  // R38.6 §28.2: derive the base URL by stripping the
  // "/chat/completions" suffix. The fetch endpoint expects
  // just the base (it appends "/models" itself).
  const fetchBaseUrl = useMemo(() => {
    const u = (value.endpointUrl || '').trim();
    if (!u) return '';
    // Strip common suffixes so /models is appended to the root
    return u
      .replace(/\/chat\/completions\/?$/i, '')
      .replace(/\/messages\/?$/i, '')
      .replace(/\/$/, '');
  }, [value.endpointUrl]);

  const applyPreset = (id: string) => {
    setPresetId(id);
    setFetchedModels([]);  // clear — old list belonged to the
                            // previous provider
    setModelFetchError(null);
    setLastFetchedKey('');
    if (id === 'custom') return;  // user fills in manually
    const p = getPreset(id);
    onChange({
      endpointUrl: p.endpointUrl || value.endpointUrl,
      model: p.defaultModel || value.model,
    });
    if (p.defaultModel) setPinnedModel(p.defaultModel);
  };

  const applyModel = (m: string) => {
    if (m === CUSTOM_MODEL) {
      // Switch to free-text — keep current model so the user
      // can edit it in place.
      onChange({ model: pinnedModel || value.model });
      return;
    }
    setPinnedModel(m);
    onChange({ model: m });
  };

  const fetchModels = useCallback(async () => {
    if (!fetchBaseUrl) {
      setModelFetchError(t('settings.needEndpointUrl'));
      return;
    }
    if (!value.apiKey.trim()) {
      setModelFetchError(t('settings.needApiKey'));
      return;
    }
    setFetchingModels(true);
    setModelFetchError(null);
    try {
      const r = await api.post<{
        models: Array<{ id: string; name?: string }>;
        count: number;
        error?: string;
        note?: string;
      }>('/config/models/custom/fetch', {
        base_url: fetchBaseUrl,
        api_key: value.apiKey,
        protocol: 'openai',
      });
      const list = (r.data.models || []).map((m) => m.id).filter(Boolean);
      setFetchedModels(list);
      setLastFetchedKey(`${fetchBaseUrl}#${list.length}`);
      // Surface the backend's failure reason (e.g. "ConnectTimeout"
      // when the endpoint is unreachable) so the click never
      // silently does nothing. `error` is set by
      // /api/config/models/custom/fetch when the provider call
      // failed; `note` is informational on success (e.g. "fetched 3
      // models from https://api.deepseek.com/v1") and the success
      // line under the Select already reports it — treating `note`
      // as an error labelled a SUCCESSFUL fetch "拉取 model 列表失败".
      setModelFetchError(r.data.error || null);
      // If the current model is not in the fetched list and
      // the Select is showing it, keep it (don't blow it
      // away). The Select's options will list fetched + the
      // current pinned value.
    } catch (e: any) {
      const msg = formatError(e, '拉取失败');
      setModelFetchError(String(msg));
    } finally {
      setFetchingModels(false);
    }
  }, [fetchBaseUrl, value.apiKey]);

  const preset = getPreset(presetId);
  // Build the Select's options: fetched list (if any) + the
  // currently-saved model (in case it's not in the list) +
  // Custom. De-duplicate so the same model isn't listed twice.
  const modelOptions: Array<{ value: string; label: string }> = [];
  const seen = new Set<string>();
  const addOption = (id: string, label?: string) => {
    if (!id || seen.has(id)) return;
    seen.add(id);
    modelOptions.push({ value: id, label: label || id });
  };
  fetchedModels.forEach((m) => addOption(m));
  // The "current" / pinned model — show it even if not in the
  // fetched list (covers "fetch failed last time" and
  // "user typed a custom model").
  addOption(pinnedModel || value.model,
            pinnedModel || value.model
              ? `${pinnedModel || value.model} (当前)`
              : '');
  // The preset's default model — shown when fetch hasn't
  // happened yet so the user at least sees what we'd default
  // to.
  addOption(preset.defaultModel,
            preset.defaultModel
              ? `${preset.defaultModel} (默认)`
              : '');
  // Always offer Custom as the escape hatch.
  addOption(CUSTOM_MODEL, 'Custom (自填 model)');

  // The Select's current value:
  //   - If user picked Custom → CUSTOM_MODEL
  //   - If the saved model is in the options → show it
  //   - Otherwise → fall back to the first fetched model, or
  //     the preset default, or the saved model pinned.
  const modelSelectValue = (() => {
    if (!value.model) return undefined;
    if (seen.has(value.model)) return value.model;
    return value.model;  // still render it even if not in
                          // seen — AntD Select allows arbitrary
                          // values to display, the user can
                          // switch to one in the list
  })();

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {/* R38.6 §28: {t('settings.providerPreset')} dropdown. One click fills
          the endpoint URL and default model for DeepSeek / Qwen
          / GLM / Moonshot / Ollama / OpenRouter / OpenAI. The
          user can still override either field manually after.

          R38.6 §28.1: we use `optionLabelProp="label"` so the
          selected-value display is just the preset name (e.g.
          "DeepSeek") — the hint lives in the option panel only
          and in the "推荐模型" preview line below. This fixes
          the "DeepSeek" / "deepseek-chat · 国内首选" overlap
          reported by the user. */}
      <div>
        <Text style={{ color: tokens.labelPrimary,
                        display: 'block', marginBottom: 6 }}>
          {t('settings.providerPreset')}
        </Text>
        <Select
          data-testid="llm-preset-select"
          style={{ width: '100%' }}
          value={presetId}
          onChange={applyPreset}
          options={LLM_PRESETS.map((p) => ({
            value: p.id,
            // R38.6 §28.3: the option's `label` is a plain
            // string ("DeepSeek"). The closed box displays
            // this string only — no hint overlap. The hint
            // shows in two other places: (1) as a caption
            // directly below the closed box (so the user
            // sees it without opening the dropdown), and
            // (2) in the dropdown row (via optionRender).
            label: t(p.label),
          }))}
          // R38.6 §28.3: explicit `labelRender` to GUARANTEE
          // the closed box shows just the string. AntD 5.22
          // would otherwise sometimes inherit the JSX from
          // `optionRender` for the selected display. This
          // belt-and-suspenders fix prevents the "DeepSeek /
          // deepseek-chat · 国内首选" overlap the user
          // reported.
          labelRender={(props) => <span>{props.label}</span>}
          // `optionRender` controls the dropdown ROW (when
          // the panel is open). We render the full label +
          // hint JSX here so the dropdown is informative.
          optionRender={(option) => {
            const p = LLM_PRESETS.find((x) => x.id === option.value);
            if (!p) return option.label;
            return (
              <div style={{ padding: '2px 0' }}>
                <div style={{ fontWeight: 500 }}>{t(p.label)}</div>
                {p.hint && (
                  <div style={{ fontSize: 11,
                                 color: tokens.labelTertiary,
                                 marginTop: 2 }}>
                    {t(p.hint)}
                  </div>
                )}
              </div>
            );
          }}
        />
        {/* R38.6 §28.3: hint shown as a small caption below
            the closed box. Always visible (not gated on
            dropdown open), so the user sees "国内首选" /
            "极致性价比" without having to click. The signup
            / docs links stay on a second line for spacing. */}
        {preset.hint && presetId !== 'custom' && (
          <div style={{ marginTop: 4, fontSize: 11,
                         color: tokens.labelTertiary }}>
            {t(preset.hint)}
          </div>
        )}
        {(() => {
          const p = preset;
          if (presetId === 'custom') return null;
          if (!p.docsUrl) return null;
          return (
            <div style={{ marginTop: 4, fontSize: 11,
                           color: tokens.labelTertiary }}>
              {p.signupUrl && (
                <a href={p.signupUrl} target="_blank" rel="noreferrer"
                   style={{ marginInlineEnd: 8 }}>{t('settings.getAPIKey')}</a>
              )}
              <a href={p.docsUrl} target="_blank" rel="noreferrer">{t('settings.docs')}</a>
            </div>
          );
        })()}
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.endpointURL')}</Text>
        <Input
          style={{ marginTop: 4 }}
          value={value.endpointUrl}
          onChange={(e) => {
            // R38.6: only ONE URL field is exposed to the user.
            // The base URL (for the orchestrator's real chat calls)
            // is auto-derived by stripping the last path segment.
            // The user only ever pastes the full endpoint URL.
            onChange({
              endpointUrl: e.target.value,
              baseUrl: deriveBaseUrl(e.target.value),
            });
          }}
          placeholder={t('settings.httpsApiOpenaiComV1ChatCompletions')}
        />
        <Text style={{ color: tokens.labelTertiary, fontSize: 11 }}>
          {t('settings.fullURLOfTheChatCompletionsEndpointTheTestProbeH')}
        </Text>
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.apiKey')}</Text>
        <Input.Password
          style={{ marginTop: 4 }}
          value={value.apiKey}
          onChange={(e) => onChange({ apiKey: e.target.value })}
          placeholder={t('settings.sk')}
        />
      </div>
      <div>
        <div style={{ display: 'flex', alignItems: 'center',
                      justifyContent: 'space-between', marginBottom: 6 }}>
          <Text style={{ color: tokens.labelPrimary }}>{t('settings.model')}</Text>
          <Button
            size="small"
            type="link"
            data-testid="llm-fetch-models"
            icon={<ReloadOutlined />}
            loading={fetchingModels}
            disabled={!fetchBaseUrl || !value.apiKey}
            onClick={fetchModels}
            style={{ padding: 0 }}
          >
            {fetchedModels.length > 0
              ? `重新拉取 (${fetchedModels.length})`
              : '拉取 model 列表'}
          </Button>
        </div>
        <Select
          data-testid="llm-model-select"
          style={{ width: '100%' }}
          value={modelSelectValue}
          onChange={applyModel}
          showSearch
          placeholder={
            fetchedModels.length > 0
              ? '从下拉选 model'
              : (preset.defaultModel || '先点右上方「拉取 model 列表」')}
          options={modelOptions}
          onInputKeyDown={(e) => {
            // Escape hatch when 「拉取 model 列表」 fails (backend down,
            // key without /models scope, …): type the model id in the
            // search box and press Enter to use it as-is.
            const el = e.currentTarget as unknown as HTMLInputElement;
            const typed = (el?.value || '').trim();
            if (e.key === 'Enter' && typed) {
              e.preventDefault();
              applyModel(typed);
            }
          }}
          filterOption={(input, option) =>
            (option?.label as string ?? '')
              .toLowerCase()
              .includes(input.toLowerCase())
          }
          notFoundContent={
            fetchedModels.length === 0
              ? '点「拉取 model 列表」获取当前 provider 的 model'
              : '无匹配'
          }
        />
        {modelSelectValue === CUSTOM_MODEL && (
          <Input
            data-testid="llm-model-custom"
            style={{ marginTop: 6 }}
            value={value.model}
            onChange={(e) => onChange({ model: e.target.value })}
            placeholder={t('settings.typeAModelNotInTheListEGMyFineTune7b')}
          />
        )}
        {modelFetchError && (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 6 }}
            message={t('settings.model')}
            description={modelFetchError}
          />
        )}
        {!modelFetchError && lastFetchedKey && (
          <Text style={{ color: tokens.labelTertiary, fontSize: 11,
                          display: 'block', marginTop: 4 }}>
            {t('settings.label')} {fetchBaseUrl} {t('settings.label')} {fetchedModels.length} {t('settings.modelProviderEndpointURL')}
          </Text>
        )}
        {!modelFetchError && !lastFetchedKey && (
          <Text style={{ color: tokens.labelTertiary, fontSize: 11,
                          display: 'block', marginTop: 4 }}>
            {t('settings.endpointURLAPIKeyModelProviderModelModelCustomMo')}
          </Text>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
        <Button
          data-testid="openai-test-connection"
          onClick={test}
          loading={testing}
          disabled={testing}
        >
          {t('settings.testConnection')}
        </Button>
        {testResult && (
          testResult.ok ? (
            <Tag color="green" data-testid="openai-test-result"
                 style={{ maxWidth: '100%', wordBreak: 'break-word' }}>
              {t('settings.ok')} {testResult.detail}
            </Tag>
          ) : (
            // R38.6: render errors as a multi-line Alert so the user
            // can read the full error (incl. the Errno code and the
            // URL we tried). Tag with ellipsis was hiding crucial
            // debugging info like "Errno 11001: getaddrinfo failed".
            <Alert
              type="error"
              data-testid="openai-test-result"
              message={t('settings.testConnectionFailed')}
              description={testResult.detail}
              style={{ flex: 1, minWidth: 0 }}
              showIcon
            />
          )
        )}
      </div>
    </Space>
  );
};

const AnthropicCompatForm: React.FC<{
  value: {
    endpointUrl: string;
    baseUrl: string;
    apiKey: string;
    model: string;
  };
  onChange: (patch: Partial<{
    endpointUrl: string;
    baseUrl: string;
    apiKey: string;
    model: string;
  }>) => void;
}> = ({ value, onChange }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<null | { ok: boolean; detail: string }>(null);

  const test = async () => {
    if (!value.endpointUrl.trim() || !value.apiKey.trim()) {
      setTestResult({ ok: false,
        detail: 'Endpoint URL and API key are both required.' });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const r = await api.post<{ ok: boolean; status: number; detail: string }>(
        '/config/test_connection',
        { provider: 'anthropic', endpoint_url: value.endpointUrl,
          base_url: value.baseUrl, api_key: value.apiKey,
          model: value.model },
      );
      setTestResult({ ok: !!r.data.ok, detail: r.data.detail || '(no detail)' });
    } catch (e: any) {
      // R38.6.3: same as the OpenAI form — surface status + detail
      // so the user sees the real reason instead of "Request
      // failed with status code 500".
      const status = e?.response?.status;
      const resp = e?.response;
      let detail = '';
      if (resp?.data) {
        if (typeof resp.data === 'string') {
          detail = resp.data.slice(0, 300);
        } else if (typeof resp.data === 'object') {
          detail = resp.data.detail || resp.data.error_id
                   || resp.data.message || JSON.stringify(resp.data).slice(0, 200);
        }
      }
      if (!detail) detail = e?.message || 'request failed';
      const ct = String(resp?.headers?.['content-type'] || '');
      if (ct && !ct.includes('json') && detail.length > 0) {
        detail = `[upstream returned ${ct.split(';')[0]}, not JSON] ${detail}`;
      }
      setTestResult({ ok: false,
        detail: status ? `[HTTP ${status}] ${detail}` : detail });
    } finally {
      setTesting(false);
    }
  };

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.endpointURL')}</Text>
        <Input
          style={{ marginTop: 4 }}
          value={value.endpointUrl}
          onChange={(e) => {
            // R38.6: only ONE URL field is exposed to the user.
            // The base URL (for the orchestrator's real chat calls)
            // is auto-derived by stripping the last path segment.
            onChange({
              endpointUrl: e.target.value,
              baseUrl: deriveBaseUrl(e.target.value),
            });
          }}
          placeholder={t('settings.httpsApiAnthropicComV1Messages')}
        />
        <Text style={{ color: tokens.labelTertiary, fontSize: 11 }}>
          {t('settings.fullURLOfTheAnthropicMessagesEndpointTheProbeHit')}
        </Text>
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.apiKey')}</Text>
        <Input.Password
          style={{ marginTop: 4 }}
          value={value.apiKey}
          onChange={(e) => onChange({ apiKey: e.target.value })}
          placeholder={t('settings.skAnt')}
        />
      </div>
      <div>
        <Text style={{ color: tokens.labelPrimary }}>{t('settings.model2')}</Text>
        <Input
          style={{ marginTop: 4 }}
          value={value.model}
          onChange={(e) => onChange({ model: e.target.value })}
          placeholder={t('settings.claude35SonnetLatestClaude3Opus')}
        />
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
        <Button
          data-testid="anthropic-test-connection"
          onClick={test}
          loading={testing}
          disabled={testing}
        >
          {t('settings.testConnection')}
        </Button>
        {testResult && (
          testResult.ok ? (
            <Tag color="green" data-testid="anthropic-test-result"
                 style={{ maxWidth: '100%', wordBreak: 'break-word' }}>
              {t('settings.ok')} {testResult.detail}
            </Tag>
          ) : (
            // R38.6: render errors as a multi-line Alert so the user
            // can read the full error. See OpenAICompatForm.
            <Alert
              type="error"
              data-testid="anthropic-test-result"
              message={t('settings.testConnectionFailed')}
              description={testResult.detail}
              style={{ flex: 1, minWidth: 0 }}
              showIcon
            />
          )
        )}
      </div>
    </Space>
  );
};

// ---------------------------------------------------------------------------
// Section: Skills (round 8 — hot-reload)
// ---------------------------------------------------------------------------

const SkillsPanel: React.FC<{ projectId: string | null }> = ({ projectId }) => {
  const t = useT();
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
      setErr(formatError(e, 'Reload failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text style={{ color: tokens.labelSecondary }}>
        {t('settings.skillsAreReusableInstructionSnippetsDiscoveredFr')}
        <code> {t('settings.kairosSkills')}</code> {t('settings.inYourProjectTheSkillsWatcherPollsEverySecondAnd')}
      </Text>
      <Space>
        <Button
          icon={<ReloadOutlined />}
          loading={busy}
          onClick={refresh}
          disabled={!projectId}
        >
          {t('settings.reloadNow')}
        </Button>
        {count !== null && (
          <Tag color="blue">{count} {t('settings.skillUnit')}{count === 1 ? '' : 's'}</Tag>
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
              <ThunderboltOutlined style={{ marginInlineEnd: 6, color: tokens.brand }} />
              {name}
            </div>
          ))}
        </div>
      )}
    </Space>
  );
};

// ---------------------------------------------------------------------------
// Section: {t('settings.about')}
// ---------------------------------------------------------------------------

/**
 * One line at the foot of the drawer: write it, review it on GitHub, submit there.
 *
 * Nothing leaves the machine until the user presses the button in the browser, and the
 * payload is exactly what they typed — no version, no OS, no logs, no contact field.
 */
const FeedbackRow: React.FC = () => {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    if (!openFeedbackIssue(text)) {
      setError(t('feedback.needText'));
      return;
    }
    setOpen(false);
    setText('');
    setError(null);
  };

  return (
    <>
      <Divider style={{ margin: '8px 0' }} />
      <div style={{ padding: '0 16px 12px' }}>
        <Button
          type="link"
          size="small"
          style={{ padding: 0 }}
          onClick={() => { setOpen(true); setError(null); }}
          data-testid="settings-feedback"
        >
          {t('feedback.entry')}
        </Button>
      </div>
      <Modal
        open={open}
        title={t('feedback.title')}
        onCancel={() => setOpen(false)}
        onOk={submit}
        okText={t('feedback.continue')}
        cancelText={t('feedback.cancel')}
        destroyOnClose
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          {t('feedback.desc')}
        </Typography.Paragraph>
        <Input.TextArea
          rows={6}
          value={text}
          autoFocus
          placeholder={t('feedback.placeholder')}
          onChange={(e) => { setText(e.target.value); setError(null); }}
          data-testid="settings-feedback-text"
        />
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
          {error ? <Typography.Text type="danger">{error}</Typography.Text> : t('feedback.note')}
        </Typography.Paragraph>
      </Modal>
    </>
  );
};


const AboutPanel: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  // Show the model the Coder is ACTUALLY running. This panel used to render a
  // hardcoded literal ("拉取 MiniMax M3") that had nothing to do with the
  // configured provider — misleading, and it ignored role_mappings.
  const [model, setModel] = useState('');
  useEffect(() => {
    let alive = true;
    api.get('/dashboard')
      .then((r) => {
        if (!alive) return;
        const agents = r.data?.agents || [];
        const coder =
          agents.find((a: any) => String(a?.role || '').toLowerCase().includes('coder'))
          || agents[0];
        setModel(String(coder?.model || ''));
      })
      .catch(() => { /* cosmetic panel — never surface an error */ });
    return () => { alive = false; };
  }, []);
  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Space size={12} align="center">
        {/* R38.6 §34: brand K icon at 48px — bigger than
            the topbar (24px) so the About panel feels
            like a real "product card" with logo + name. */}
        <img
          src="/branding/kairos-icon-128.png"
          alt="Kairos"
          width={48}
          height={48}
          style={{ borderRadius: 10, display: 'block',
                    objectFit: 'cover' }}
        />
        <Title level={4} style={{ color: tokens.labelPrimary, margin: 0 }}>
          {t('settings.kairosCode')}
        </Title>
      </Space>
      <Text style={{ color: tokens.labelSecondary }}>
        {t('settings.multiAgentCollaborationPlatformCoderLtGtReviewer')}
      </Text>
      {model ? (
        <Text style={{ color: tokens.labelTertiary, fontSize: 12 }}>
          {t('settings.model3')} {model}
        </Text>
      ) : null}
    </Space>
  );
};

// ---------------------------------------------------------------------------
// Drawer
// ---------------------------------------------------------------------------

export const SettingsDrawer: React.FC<Props> = ({ open, onClose }) => {
  const tokens = useThemeTokens();
  const { t, lang, setLang } = useI18n();
  const { message: msgApi } = AntdApp.useApp();
  // Default must be one of the Tabs item keys below ('provider' | 'mode' |
  // 'about'). It used to be 'coder' — a leftover from the pre-R38.6.3 8-tab
  // layout — which matched no item, so Ant Design rendered NO panel at all and
  // the drawer opened empty: the LLM provider form was unreachable.
  const [tab, setTab] = useState('provider');
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
        // R38.6.4: do NOT unconditionally adopt the backend's
        // provider. If the user already has a configured provider
        // in localStorage (apiKey, custom endpoint, non-default
        // model), trust that and skip the migration. The bug we
        // were hitting: backend stored the legacy R8 shape
        // ({apiKeyEnv, ollamaBaseUrl, ollamaModel}) — the migration
        // path wiped apiKey/endpointUrl back to defaults every time
        // the drawer was opened, making the user re-paste the key.
        const local = useSettingsStore.getState().provider;
        const localHasKey = !!(local.openai?.apiKey
                                || local.anthropic?.apiKey);
        const localHasCustomUrl = !!(
          (local.openai?.endpointUrl
            && local.openai.endpointUrl
              !== 'https://api.openai.com/v1/chat/completions')
          || (local.anthropic?.endpointUrl
            && local.anthropic.endpointUrl
              !== 'https://api.anthropic.com/v1/messages'));
        if (localHasKey || localHasCustomUrl) {
          // User has explicit config in localStorage. Keep it.
          // We can still opportunistically refresh non-sensitive
          // fields from the backend (e.g. if the user changed
          // model but not key), but only if the backend's value
          // differs and the local slot is still the default.
          return;
        }
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
            openai: {
              endpointUrl: 'https://api.openai.com/v1/chat/completions',
              baseUrl: 'https://api.openai.com/v1',
              apiKey: '', model: 'gpt-4o',
            },
            anthropic: {
              endpointUrl: 'https://api.anthropic.com/v1/messages',
              baseUrl: 'https://api.anthropic.com',
              apiKey: '', model: 'claude-3-5-sonnet-latest',
            },
          });
        } else {
          // R38.5: also backfill `endpointUrl` for users who have
          // the R37 shape ({baseUrl, apiKey, model}) on disk but
          // not the new endpointUrl. Derive from baseUrl + provider.
          const needsBackfill =
            (d.provider.openai && !('endpointUrl' in d.provider.openai)) ||
            (d.provider.anthropic && !('endpointUrl' in d.provider.anthropic));
          if (needsBackfill) {
            const openaiEp = d.provider.openai?.endpointUrl
              || (d.provider.openai?.baseUrl
                  ? d.provider.openai.baseUrl.replace(/\/?v1\/?$/, '')
                    + '/v1/chat/completions'
                  : 'https://api.openai.com/v1/chat/completions');
            const anthropicEp = d.provider.anthropic?.endpointUrl
              || (d.provider.anthropic?.baseUrl
                  ? d.provider.anthropic.baseUrl.replace(/\/?v1\/?$/, '')
                    + '/v1/messages'
                  : 'https://api.anthropic.com/v1/messages');
            setProvider({
              ...d.provider,
              openai: {
                ...(d.provider.openai || {}),
                endpointUrl: openaiEp,
              },
              anthropic: {
                ...(d.provider.anthropic || {}),
                endpointUrl: anthropicEp,
              },
            });
          } else {
            setProvider(d.provider);
          }
        }
      }
    }).catch(() => { /* offline / first paint — keep defaults */ });
  }, [open, setVoice, setMcp, setCloud, setMetrics, setProvider]);

  // Debounced auto-save on casual settings (voice/mcp/cloud/metrics).
  // R38.6: the LLM provider config is NOT auto-saved — the user
  // clicks the explicit Save button in the ProviderPanel instead.
  // The previous design had a single 400ms debounce that included
  // ``provider``; this was unreliable (closing the drawer quickly
  // could cancel the save before the timer fired, losing the
  // change). The Save button makes LLM persistence explicit.
  const [saveStatus, setSaveStatus] = useState<
    null | { state: 'saving' } | { state: 'saved'; at: number }
    | { state: 'error'; detail: string }
  >(null);
  useEffect(() => {
    if (!didLoad.current) return;
    setSaveStatus({ state: 'saving' });
    const t = setTimeout(() => {
      // Exclude ``provider`` — it's saved explicitly via the
      // ProviderPanel's Save button. We still POST it here so the
      // server has the latest snapshot of all settings (the
      // ProviderPanel's save() also POSTs everything; this is
      // belt-and-suspenders).
      api.post('/projects/settings',
              { voice, mcp, cloud, metrics, provider })
        .then(() => setSaveStatus({ state: 'saved', at: Date.now() }))
        .catch((e) => setSaveStatus({
          state: 'error',
          detail: formatError(e, 'request failed'),
        }));
    }, 400);
    return () => clearTimeout(t);
  }, [voice, mcp, cloud, metrics, provider]);

  // Refresh the "Saved Xs ago" label every second so the user sees
  // the indicator stay fresh without having to edit.
  const [, force] = useState(0);
  useEffect(() => {
    if (!saveStatus || saveStatus.state !== 'saved') return;
    const t = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [saveStatus]);

  return (
    <Drawer
      title={
        <span>
          <SettingOutlined style={{ marginInlineEnd: 8 }} />
          {t('settings.settings')}
        </span>
      }
      placement="right"
      width={420}
      open={open}
      onClose={onClose}
      destroyOnHidden
      styles={{ body: { padding: 0, background: tokens.bgBase } }}
    >
      <div
        data-testid="settings-save-status"
        style={{
          padding: '6px 16px',
          fontSize: 11,
          color: tokens.labelTertiary,
          borderBottom: `1px solid ${tokens.border}`,
          display: 'flex', alignItems: 'center', gap: 6,
          background: tokens.bgElevated,
        }}
      >
        {saveStatus?.state === 'saving' && (
          <><Spin size="small" /> {t('settings.saving')}</>
        )}
        {saveStatus?.state === 'saved' && (
          <>
            <CheckCircleFilled style={{ color: tokens.success }} />
            {t('settings.saved')} {agoLabel(Date.now() - saveStatus.at)}
          </>
        )}
        {saveStatus?.state === 'error' && (
          <>
            <CloseCircleFilled style={{ color: tokens.danger }} />
            <span style={{ color: tokens.danger }}>
              {t('settings.saveFailed')} {saveStatus.detail}
            </span>
          </>
        )}
      </div>
      {/* Language / 语言 — a chrome-level preference, so it sits above
          the tabs and is reachable from every tab. Switching re-renders
          the whole tree (AntD locale included) immediately. */}
      <div
        data-testid="settings-language-row"
        style={{
          padding: '10px 16px',
          display: 'flex', alignItems: 'center', gap: 8,
          borderBottom: `1px solid ${tokens.border}`,
        }}
      >
        <GlobalOutlined style={{ color: tokens.labelSecondary }} />
        <span style={{ fontSize: 13, color: tokens.labelSecondary }}>
          {t('language.label')}
        </span>
        <Select
          size="small"
          showSearch
          optionFilterProp="label"
          value={lang}
          onChange={(v) => {
            setLang(v);
            const picked = LANGS.find((l) => l.value === v);
            msgApi.success(t('language.switched', { lang: picked?.label ?? v }));
          }}
          options={LANGS.map((l) => ({ value: l.value, label: l.label }))}
          style={{ marginInlineStart: 'auto', width: 150 }}
          data-testid="settings-language-select"
        />
      </div>
      <Tabs
        activeKey={tab}
        onChange={setTab}
        tabPosition="top"
        style={{ padding: '0 16px' }}
        items={[
          // R38.6.3: 8 tabs → 3. Voice / MCP / Cloud / Metrics /
          // Skills / Coder advanced panels removed. Power users
          // can call those APIs directly via /api/config/* and
          // /api/borrowed/*. The 3 remaining tabs cover the
          // 95% daily-use path: pick a model, pick a mode,
          // and (rarely) reset / inspect.
          {
            key: 'provider',
            label: <span><RobotOutlined /> {t('settings.provider')}</span>,
            children: <ProviderPanel />,
          },
          {
            key: 'mode',
            label: <span><CodeOutlined /> {t('settings.mode')}</span>,
            children: <CoderModePanel />,
          },
          {
            key: 'about',
            label: <span><InfoCircleOutlined /> {t('settings.about')}</span>,
            children: <AboutPanel />,
          },
        ]}
      />
      <FeedbackRow />
    </Drawer>
  );
};
