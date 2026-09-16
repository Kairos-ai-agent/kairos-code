import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { SettingsDrawer } from '../components/SettingsDrawer';
import { useSettingsStore } from '../stores/settingsStore';

function renderDrawer() {
  return render(
    <ConfigProvider>
      <SettingsDrawer open={true} onClose={() => {}} />
    </ConfigProvider>
  );
}

// The drawer opens on the "Provider" (LLM) tab; the coder-mode controls
// live under the "Mode" tab, so tests must switch to it first.
function openModeTab() {
  fireEvent.click(screen.getByText('Mode'));
}

describe('SettingsDrawer', () => {
  beforeEach(() => {
    // reset the store between tests
    useSettingsStore.setState({
      drawerOpen: false,
      coderMode: 'default',
      voice: {
        ttsProvider: 'edge',
        ttsVoice: 'en-US-AriaNeural',
        sttProvider: 'mock',
        sttLanguage: 'en',
        autoPlay: false,
      },
      mcp: { enabledServers: ['filesystem'], permissionPrompt: true },
      cloud: { s3Bucket: '', s3Region: 'us-east-1', s3Endpoint: '', addressingStyle: 'auto' },
      metrics: { showInFooter: true },
    });
  });

  it('renders the Coder mode tab with three options', () => {
    renderDrawer();
    openModeTab();
    expect(screen.getByTestId('coder-mode-default')).toBeTruthy();
    expect(screen.getByTestId('coder-mode-read_only')).toBeTruthy();
    expect(screen.getByTestId('coder-mode-sandbox')).toBeTruthy();
  });

  it('clicking read_only updates the store', () => {
    renderDrawer();
    openModeTab();
    const before = useSettingsStore.getState().coderMode;
    expect(before).toBe('default');
    fireEvent.click(screen.getByTestId('coder-mode-read_only'));
    expect(useSettingsStore.getState().coderMode).toBe('read_only');
  });

  it('clicking sandbox updates the store', () => {
    renderDrawer();
    openModeTab();
    fireEvent.click(screen.getByTestId('coder-mode-sandbox'));
    expect(useSettingsStore.getState().coderMode).toBe('sandbox');
  });

  it('openDrawer / closeDrawer flip the drawerOpen flag', () => {
    act(() => useSettingsStore.getState().openDrawer());
    expect(useSettingsStore.getState().drawerOpen).toBe(true);
    act(() => useSettingsStore.getState().closeDrawer());
    expect(useSettingsStore.getState().drawerOpen).toBe(false);
  });

  it('setVoice merges partial updates', () => {
    renderDrawer();
    act(() => {
      useSettingsStore.getState().setVoice({ autoPlay: true });
    });
    const v = useSettingsStore.getState().voice;
    expect(v.autoPlay).toBe(true);
    // other fields untouched
    expect(v.ttsVoice).toBe('en-US-AriaNeural');
    expect(v.sttLanguage).toBe('en');
  });

  it('setCloud merges partial updates', () => {
    renderDrawer();
    act(() => {
      useSettingsStore.getState().setCloud({ s3Bucket: 'my-bucket', addressingStyle: 'path' });
    });
    const c = useSettingsStore.getState().cloud;
    expect(c.s3Bucket).toBe('my-bucket');
    expect(c.addressingStyle).toBe('path');
    // other fields preserved
    expect(c.s3Region).toBe('us-east-1');
  });

  it('setMcp toggles enabledServers correctly', () => {
    renderDrawer();
    act(() => {
      useSettingsStore.getState().setMcp({ enabledServers: [] });
    });
    expect(useSettingsStore.getState().mcp.enabledServers).toEqual([]);
    act(() => {
      useSettingsStore.getState().setMcp({ enabledServers: ['filesystem', 'github'] });
    });
    expect(useSettingsStore.getState().mcp.enabledServers).toEqual(['filesystem', 'github']);
  });

  it('picking the Anthropic preset switches the provider slot, not just the URL', async () => {
    renderDrawer();
    const select = screen.getByTestId('llm-preset-select');
    fireEvent.mouseDown(select.querySelector('.ant-select-selector')!);
    const option = await screen.findByTitle('Anthropic (Claude)');
    fireEvent.click(option);

    const p = useSettingsStore.getState().provider;
    expect(p.active).toBe('anthropic');
    expect(p.anthropic.endpointUrl).toBe('https://api.anthropic.com/v1/messages');
    expect(p.anthropic.model).toBe('claude-sonnet-4-5-20250929');
    // Regression guard. An earlier version passed `active` through the form's
    // local onChange, which each form binds to its own slot
    // (setProvider({ openai: {...} })) — so "active" was written *inside*
    // provider.openai as a stray field and provider.active never changed.
    expect((p.openai as unknown as Record<string, unknown>).active).toBeUndefined();
    expect(p.openai.endpointUrl).toBe('https://api.openai.com/v1/chat/completions');
  });
});
