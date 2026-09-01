/**
 * BrowserPanel — R38.6 §32. 5th tab in the Workbench.
 *
 * Renders a Playwright-controlled headless Chromium session
 * scoped to the current project. The user can:
 *  - type a URL in the address bar and hit Enter to navigate
 *  - see a live screenshot of the current page (auto-refreshed
 *    every 1.5s while the tab is visible)
 *  - click on the screenshot to send a mouse click at that
 *    (x, y) — coordinates are translated from image-space to
 *    viewport-space using the screenshot's known width
 *  - type into the focused element (after a click) via the
 *    Type input
 *  - browse back/forward, reload, close the browser, view
 *    console messages
 *
 * Why screenshot + click-overlay (and not a real iframe)?
 *  Many sites block iframe embedding via X-Frame-Options /
 *  CSP. A real iframe also can't see the cookies / localStorage
 *  the user logged into (we want a headless browser that can
 *  log in). Playwright gives us both — a real session plus
 *  full interactivity.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Button, Input, Space, Tooltip, Tag, Spin, message, Empty, Tabs,
  List, Typography, Switch, Select, Modal, InputNumber,
} from 'antd';
import {
  ReloadOutlined, ArrowLeftOutlined, ArrowRightOutlined,
  PoweroffOutlined, GlobalOutlined, AimOutlined,
  ConsoleSqlOutlined, CameraOutlined, EditOutlined, CodeOutlined,
} from '@ant-design/icons';

import api from '../api/client';
import { formatError } from '../utils/formatError';
import { useThemeTokens } from '../hooks/useThemeTokens';

const { Text } = Typography;

// 1.5s refresh keeps the screenshot live without thrashing the
// backend. The user can also force-refresh with the camera icon.
const REFRESH_MS = 1500;

interface PageInfo {
  url: string;
  title: string;
  status?: number | null;
  ok?: boolean;
  error?: string;
  viewport?: { width: number; height: number };
  console_count?: number;
}

const BrowserPanel: React.FC<{ projectId: string }> = ({ projectId }) => {
  const tokens = useThemeTokens();
  const [page, setPage] = useState<PageInfo>({
    url: '', title: '', viewport: { width: 1280, height: 800 },
  });
  const [urlBar, setUrlBar] = useState('');
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [clickMode, setClickMode] = useState(false);
  const [clickFeedback, setClickFeedback] = useState<{ x: number; y: number; ts: number } | null>(null);
  const [imgDims, setImgDims] = useState<{ w: number; h: number }>({ w: 1280, h: 800 });
  const [console, setConsole] = useState<Array<{ type: string; text: string; ts: number }>>([]);
  const [msgApi, contextHolder] = message.useMessage();
  const imgRef = useRef<HTMLImageElement>(null);
  // AntD's <Input> uses its own ref type (InputRef) which is
  // structurally wider than HTMLInputElement. We don't actually
  // use the ref anywhere yet, so `any` is fine.
  // (Declared later, in the viewport block — keep this comment
  // so the next reader doesn't move it back here.)

  const apiBase = `/api/browser/${projectId}`;

  // Fetch current page metadata (URL, title, viewport).
  const refreshPage = useCallback(async () => {
    try {
      const r = await api.get<PageInfo>(`${apiBase}/current`);
      setPage(r.data);
      setUrlBar((cur) => (cur === '' || cur === page.url ? r.data.url : cur));
    } catch {
      // No browser yet — that's fine, the user will navigate first.
    }
  }, [apiBase, page.url]);

  // Fetch a fresh screenshot.
  const refreshShot = useCallback(async () => {
    try {
      // Cache-bust so the browser doesn't reuse a stale image
      const r = await api.get<Blob>(`${apiBase}/screenshot?ts=${Date.now()}`, {
        responseType: 'blob',
      });
      const url = URL.createObjectURL(r.data);
      // Revoke the previous one to avoid memory leak
      setScreenshot((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return url;
      });
    } catch {
      // The browser may not be open yet. Show empty state.
    }
  }, [apiBase]);

  // Fetch console messages.
  const refreshConsole = useCallback(async () => {
    try {
      const r = await api.get<{ messages: Array<{ type: string; text: string; ts: number }> }>(
        `${apiBase}/console?limit=80`,
      );
      setConsole(r.data.messages);
    } catch {
      // ignore
    }
  }, [apiBase]);

  // Auto-refresh loop
  useEffect(() => {
    if (!autoRefresh) return;
    refreshPage();
    refreshShot();
    const t = setInterval(() => {
      refreshPage();
      refreshShot();
    }, REFRESH_MS);
    return () => clearInterval(t);
  }, [autoRefresh, refreshPage, refreshShot]);

  // Revoke object URLs on unmount
  useEffect(() => {
    return () => {
      if (screenshot) URL.revokeObjectURL(screenshot);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Navigate to a URL
  const doNavigate = useCallback(async (raw: string) => {
    const url = raw.trim();
    if (!url) return;
    setLoading(true);
    try {
      const r = await api.post<PageInfo>(`${apiBase}/navigate`, { url });
      setPage((p) => ({ ...p, ...r.data }));
      // Force a fresh shot
      setTimeout(refreshShot, 300);
      if (r.data.error) {
        msgApi.warning(`Navigated with error: ${r.data.error}`);
      }
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'navigate failed');
    } finally {
      setLoading(false);
    }
  }, [apiBase, msgApi, refreshShot]);

  // Click handler on the screenshot. Translates image-relative
  // coords back to viewport coords (1:1 in our default config,
  // but explicit so the user sees what's happening if the
  // viewport is resized).
  const handleImageClick = useCallback(async (e: React.MouseEvent<HTMLImageElement>) => {
    if (!clickMode) return;
    const img = imgRef.current;
    if (!img) return;
    const rect = img.getBoundingClientRect();
    // Ratio of displayed-image px to intrinsic (viewport) px
    const sx = imgDims.w / rect.width;
    const sy = imgDims.h / rect.height;
    const vx = Math.round((e.clientX - rect.left) * sx);
    const vy = Math.round((e.clientY - rect.top) * sy);
    setClickFeedback({ x: vx, y: vy, ts: Date.now() });
    try {
      await api.post(`${apiBase}/click`, { x: vx, y: vy });
      // Click resets the type target — let the user re-focus
      // by typing or pressing Tab.
      msgApi.success(`Clicked (${vx}, ${vy})`);
      setTimeout(refreshShot, 200);
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'click failed');
    }
  }, [clickMode, imgDims, apiBase, msgApi, refreshShot]);

  // Type into the focused element
  const doType = useCallback(async (text: string) => {
    if (!text) return;
    try {
      await api.post(`${apiBase}/type`, { text });
      setTimeout(refreshShot, 200);
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'type failed');
    }
  }, [apiBase, msgApi, refreshShot]);

  // Press a key (Enter, Tab, Escape, etc.)
  const doPress = useCallback(async (key: string) => {
    try {
      await api.post(`${apiBase}/press`, { key });
      setTimeout(refreshShot, 200);
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'press failed');
    }
  }, [apiBase, msgApi, refreshShot]);

  // Nav buttons
  const doBack = useCallback(async () => {
    try { await api.post(`${apiBase}/back`, {}); setTimeout(refreshShot, 300); } catch {}
  }, [apiBase, refreshShot]);
  const doForward = useCallback(async () => {
    try { await api.post(`${apiBase}/forward`, {}); setTimeout(refreshShot, 300); } catch {}
  }, [apiBase, refreshShot]);
  const doReload = useCallback(async () => {
    setLoading(true);
    try { await api.post(`${apiBase}/reload`, {}); setTimeout(refreshShot, 600); }
    finally { setLoading(false); }
  }, [apiBase, refreshShot]);
  const doClose = useCallback(() => {
    Modal.confirm({
      title: 'Close browser session?',
      content: 'This will discard the cookies and local storage for this project. You can re-open it by navigating to a new URL.',
      okText: 'Close',
      okType: 'danger',
      onOk: async () => {
        try {
          await api.post(`${apiBase}/close`, {});
          setScreenshot(null);
          setPage({ url: '', title: '', viewport: { width: 1280, height: 800 } });
          setUrlBar('');
          msgApi.success('Browser closed');
        } catch (e: any) {
          msgApi.error(e?.response?.data?.detail || 'close failed');
        }
      },
    });
  }, [apiBase, msgApi]);

  // Run a JS expression (useful for the agent or for the user
  // to inspect the DOM)
  const [evalOpen, setEvalOpen] = useState(false);
  const [evalExpr, setEvalExpr] = useState('document.title');
  const [evalResult, setEvalResult] = useState<unknown>(null);
  const doEval = useCallback(async () => {
    try {
      const r = await api.post<{ result: unknown }>(`${apiBase}/evaluate`,
                                                    { expression: evalExpr });
      setEvalResult(r.data.result);
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'evaluate failed');
    }
  }, [apiBase, evalExpr, msgApi]);

  // Resize viewport
  const typeInputRef = useRef<any>(null);

  // Resize viewport
  const [vpSize, setVpSize] = useState<'desktop' | 'laptop' | 'tablet' | 'phone'>('laptop');
  const vpPresets: Record<string, { w: number; h: number }> = {
    desktop: { w: 1920, h: 1080 },
    laptop: { w: 1280, h: 800 },
    tablet: { w: 768, h: 1024 },
    phone: { w: 375, h: 667 },
  };
  const setViewport = useCallback(async (size: 'desktop' | 'laptop' | 'tablet' | 'phone') => {
    setVpSize(size);
    const { w, h } = vpPresets[size];
    try {
      await api.post(`${apiBase}/viewport`, { width: w, height: h });
      setImgDims({ w, h });
      setTimeout(refreshShot, 300);
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'viewport failed');
    }
  }, [apiBase, msgApi, refreshShot]);

  // Sub-tab: screenshot / console / evaluate
  const [subTab, setSubTab] = useState<'view' | 'console' | 'eval'>('view');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {contextHolder}

      {/* Address bar */}
      <div style={{ padding: 6, borderBottom: `1px solid ${tokens.border}` }}>
        <Space.Compact style={{ width: '100%' }}>
          <Tooltip title="Back">
            <Button size="small" icon={<ArrowLeftOutlined />} onClick={doBack} />
          </Tooltip>
          <Tooltip title="Forward">
            <Button size="small" icon={<ArrowRightOutlined />} onClick={doForward} />
          </Tooltip>
          <Tooltip title="Reload">
            <Button size="small" icon={<ReloadOutlined />} onClick={doReload}
                    loading={loading} />
          </Tooltip>
          <Input
            size="small"
            value={urlBar}
            onChange={(e) => setUrlBar(e.target.value)}
            onPressEnter={() => doNavigate(urlBar)}
            placeholder="https://example.com"
            prefix={<GlobalOutlined />}
            data-testid="browser-url"
          />
          <Button size="small" type="primary" onClick={() => doNavigate(urlBar)}>
            Go
          </Button>
        </Space.Compact>
        <div style={{ marginTop: 4, display: 'flex', alignItems: 'center',
                      justifyContent: 'space-between', gap: 4 }}>
          <Text type="secondary" style={{ fontSize: 11 }}
                ellipsis={{ tooltip: page.title || '(no page)' }}>
            {page.title || '(no page loaded)'}
          </Text>
          <Space size={2}>
            <Tooltip title="Click mode (click on the screenshot)">
              <Switch size="small" checked={clickMode}
                      onChange={setClickMode}
                      checkedChildren={<AimOutlined />} />
            </Tooltip>
            <Select size="small" value={vpSize} onChange={setViewport}
                    style={{ width: 90 }} data-testid="browser-viewport"
                    options={[
                      { value: 'desktop', label: '1920×1080' },
                      { value: 'laptop', label: '1280×800' },
                      { value: 'tablet', label: '768×1024' },
                      { value: 'phone', label: '375×667' },
                    ]} />
            <Tooltip title="Auto-refresh every 1.5s">
              <Switch size="small" checked={autoRefresh}
                      onChange={setAutoRefresh} />
            </Tooltip>
            <Tooltip title="Close browser session">
              <Button size="small" danger icon={<PoweroffOutlined />}
                      onClick={doClose} />
            </Tooltip>
          </Space>
        </div>
      </div>

      {/* Sub-tabs: View / Console / Evaluate */}
      <Tabs size="small" activeKey={subTab} onChange={(k) => setSubTab(k as any)}
            style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
            tabBarStyle={{ marginBottom: 0, paddingLeft: 8 }}
            items={[
              {
                key: 'view',
                label: <span><CameraOutlined /> View</span>,
                children: (
                  <div style={{ height: '100%', overflow: 'auto',
                                background: '#222', padding: 4,
                                position: 'relative' }}>
                    {screenshot ? (
                      <div style={{ position: 'relative', display: 'inline-block' }}>
                        <img
                          ref={imgRef}
                          src={screenshot}
                          alt={page.title || 'browser screenshot'}
                          onClick={handleImageClick}
                          onLoad={(e) => {
                            const img = e.currentTarget;
                            setImgDims({ w: img.naturalWidth, h: img.naturalHeight });
                          }}
                          style={{
                            display: 'block',
                            maxWidth: '100%',
                            cursor: clickMode ? 'crosshair' : 'default',
                            border: `1px solid ${tokens.border}`,
                          }}
                          data-testid="browser-screenshot"
                        />
                        {clickFeedback && Date.now() - clickFeedback.ts < 1500 && (
                          <div
                            style={{
                              position: 'absolute',
                              left: clickFeedback.x * (imgDims.w / imgRef.current!.clientWidth) - 10,
                              top: clickFeedback.y * (imgDims.h / imgRef.current!.clientHeight) - 10,
                              width: 20, height: 20, borderRadius: '50%',
                              background: 'rgba(255, 0, 0, 0.4)',
                              border: '2px solid red',
                              pointerEvents: 'none',
                            }} />
                        )}
                      </div>
                    ) : (
                      <div style={{ padding: 24, textAlign: 'center' }}>
                        <Empty
                          image={Empty.PRESENTED_IMAGE_SIMPLE}
                          description={
                            <div>
                              <div>No page loaded</div>
                              <div style={{ fontSize: 11, color: '#999', marginTop: 4 }}>
                                Type a URL above and press Enter
                              </div>
                            </div>
                          }
                        />
                      </div>
                    )}
                  </div>
                ),
              },
              {
                key: 'console',
                label: <span><ConsoleSqlOutlined /> Console</span>,
                children: (
                  <div style={{ height: '100%', overflow: 'auto', padding: 4 }}>
                    {console.length === 0 ? (
                      <Empty description="No console messages yet" />
                    ) : (
                      <List
                        size="small"
                        dataSource={console}
                        renderItem={(m) => (
                          <List.Item style={{ padding: '2px 4px',
                                              borderBottom: `1px solid ${tokens.border}` }}>
                            <Tag color={
                              m.type === 'error' ? 'red' :
                              m.type === 'warning' ? 'orange' :
                              m.type === 'log' ? 'blue' : 'default'
                            } style={{ marginRight: 4 }}>{m.type}</Tag>
                            <Text style={{ fontSize: 11, fontFamily: 'monospace' }}>
                              {m.text}
                            </Text>
                          </List.Item>
                        )}
                      />
                    )}
                    <div style={{ marginTop: 4, textAlign: 'right' }}>
                      <Button size="small" onClick={refreshConsole}
                              icon={<ConsoleSqlOutlined />}>Refresh</Button>
                    </div>
                  </div>
                ),
              },
              {
                key: 'eval',
                label: <span><CodeOutlined /> JS</span>,
                children: (
                  <div style={{ padding: 6 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      Run a JavaScript expression on the current page
                      and inspect the result.
                    </Text>
                    <Input.TextArea
                      rows={3}
                      value={evalExpr}
                      onChange={(e) => setEvalExpr(e.target.value)}
                      style={{ fontFamily: 'monospace', fontSize: 11, marginTop: 4 }}
                      placeholder="document.title"
                    />
                    <Button size="small" type="primary" onClick={doEval}
                            style={{ marginTop: 4 }}>Run</Button>
                    {evalResult !== null && (
                      <pre style={{
                        marginTop: 6, padding: 6,
                        background: tokens.bgElevated, border: `1px solid ${tokens.border}`,
                        fontSize: 11, maxHeight: 200, overflow: 'auto',
                        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                      }}>
                        {typeof evalResult === 'string'
                          ? evalResult
                          : JSON.stringify(evalResult, null, 2)}
                      </pre>
                    )}
                  </div>
                ),
              },
            ]}
      />

      {/* Type input — always visible at the bottom */}
      <div style={{ padding: 4, borderTop: `1px solid ${tokens.border}`,
                    background: tokens.bgElevated }}>
        <Space.Compact style={{ width: '100%' }}>
          <Input
            size="small"
            ref={typeInputRef}
            placeholder="Type text (focuses the element after a click)"
            onPressEnter={(e) => {
              doType((e.target as HTMLInputElement).value);
              (e.target as HTMLInputElement).value = '';
            }}
            data-testid="browser-type"
          />
          <Button size="small" icon={<EditOutlined />}
                  onClick={() => doPress('Tab')}>Tab</Button>
          <Button size="small" icon={<EditOutlined />}
                  onClick={() => doPress('Enter')}>↵</Button>
          <Button size="small" icon={<EditOutlined />}
                  onClick={() => doPress('Escape')}>Esc</Button>
        </Space.Compact>
        <Text type="secondary" style={{ fontSize: 10, display: 'block', marginTop: 2 }}>
          Click on the page first, then type. Cookies/login persist between visits.
        </Text>
      </div>
    </div>
  );
};

export default BrowserPanel;

