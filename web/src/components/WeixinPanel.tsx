/**
 * WeixinPanel — the native WeChat channel, as a right-hand drawer off the rail.
 *
 * The WeChat channel is different from the bot connector next to it: there is no
 * external process. The server talks to WeChat's iLink gateway, holds the bot
 * token, and hands the browser only a QR image and a desensitized account list.
 * So the panel owes the user three plain things:
 *
 *   1. A big, scannable QR (the server renders the PNG — no QR library here).
 *   2. What is happening right now (waiting / scanned / needs a code / expired),
 *      never a blank card, whatever status the server invents next.
 *   3. The accounts that are bound, with a way to remove one.
 *
 * Polling deliberately waits for the previous status call to return before
 * scheduling the next one (`login/status` is a server-side long poll), so the
 * panel cannot pile requests on top of each other.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert, App as AntdApp, Button, Divider, Drawer, Input, Modal, Space, Spin,
  Tag, Typography,
} from 'antd';
import {
  DeleteOutlined, MobileOutlined, ReloadOutlined,
} from '@ant-design/icons';

import { useT } from '../i18n';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { formatError } from '../utils/formatError';
import {
  deleteWeixinAccount,
  listWeixinAccounts,
  weixinLoginStatus,
  weixinQrPng,
  type WeixinAccount,
  type WeixinLoginState,
} from '../api/client';

const { Text } = Typography;

/**
 * Gap between status polls. The server long-polls (one request already waits
 * for a change), so this is only the pause between a reply and the next ask —
 * not a busy loop.
 */
export const WEIXIN_POLL_MS = 2500;

/** Statuses after which polling stops on its own. */
const TERMINAL = new Set(['confirmed', 'expired']);

/** status → i18n key. An unrecognized status falls through to a "waiting"
 *  label, so a new server value can never blank the panel. */
const STATUS_KEY: Record<string, string> = {
  wait: 'weixin.status.wait',
  scaned: 'weixin.status.scaned',
  confirmed: 'weixin.status.confirmed',
  expired: 'weixin.status.expired',
  need_verifycode: 'weixin.status.needVerifycode',
  verify_code_blocked: 'weixin.status.verifyBlocked',
  scaned_but_redirect: 'weixin.status.redirect',
  binded_redirect: 'weixin.status.binded',
  error: 'weixin.status.error',
};

/** Exported so the mapping is pinned by a test, not just by the code. */
export function weixinStatusKey(status: string): string {
  return STATUS_KEY[status] || 'weixin.status.unknown';
}

/** Account status → i18n key. Anything not online/error reads as offline. */
export function weixinAccountStatusKey(status: string): string {
  if (status === 'online') return 'weixin.accountOnline';
  if (status === 'error') return 'weixin.accountError';
  return 'weixin.accountOffline';
}

/** jsdom has no `URL.createObjectURL`; fall back to "" so the panel still
 *  renders (an <img> with no src) instead of throwing. */
function objectUrl(blob: Blob): string {
  try {
    if (typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
      return URL.createObjectURL(blob);
    }
  } catch {
    /* jsdom without createObjectURL */
  }
  return '';
}

export const WeixinPanel: React.FC<{ active?: boolean }> = ({ active = true }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const { message, modal } = AntdApp.useApp();

  const [qrcode, setQrcode] = useState<string | null>(null);
  const [qrUrl, setQrUrl] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const [verifyCode, setVerifyCode] = useState('');
  const [accounts, setAccounts] = useState<WeixinAccount[]>([]);
  const [busy, setBusy] = useState(false);
  const [loadingAccounts, setLoadingAccounts] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(false);
  // Bumped when the user submits a pairing code, to re-arm the poll loop.
  const [resumeKey, setResumeKey] = useState(0);

  // Latest values, read by the polling loop without making it re-subscribe on
  // every render (a `t` that changes identity each render would otherwise feed
  // an endless immediate-poll loop when no I18nProvider is mounted).
  const tRef = useRef(t);
  tRef.current = t;
  const messageRef = useRef(message);
  messageRef.current = message;
  const statusRef = useRef(status);
  statusRef.current = status;
  const verifyRef = useRef(verifyCode);
  verifyRef.current = verifyCode;
  const qrUrlRef = useRef<string | null>(null);

  const loadAccounts = useCallback(async () => {
    setLoadingAccounts(true);
    try {
      const r = await listWeixinAccounts();
      setAccounts(r.accounts || []);
    } catch (e) {
      setError(formatError(e, tRef.current('weixin.error.accounts')));
    } finally {
      setLoadingAccounts(false);
    }
  }, []);

  // Only load while the drawer is actually open.
  useEffect(() => {
    if (active) void loadAccounts();
  }, [active, loadAccounts]);

  // Object URLs leak unless revoked. Revoke the previous one on replace, and
  // the last one on unmount.
  const showQr = useCallback((blob: Blob) => {
    const url = objectUrl(blob);
    if (qrUrlRef.current) {
      try { URL.revokeObjectURL(qrUrlRef.current); } catch { /* ignore */ }
    }
    qrUrlRef.current = url || null;
    setQrUrl(url || null);
  }, []);
  useEffect(() => () => {
    if (qrUrlRef.current) {
      try { URL.revokeObjectURL(qrUrlRef.current); } catch { /* ignore */ }
    }
  }, []);

  /** GET the PNG (server renders it) and start watching it. */
  const start = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const { blob, qrcode: id } = await weixinQrPng();
      showQr(blob);
      setQrcode(id || null);
      setStatus('wait');
      setVerifyCode('');
      setResumeKey((k) => k + 1);
    } catch (e) {
      setError(formatError(e, tRef.current('weixin.error.qr')));
      setQrcode(null);
      setStatus('');
    } finally {
      setBusy(false);
    }
  }, [showQr]);

  /** Apply one status reply: label it, and act on the two edges that matter. */
  const applyState = useCallback((st: WeixinLoginState) => {
    let next = String(st.status || '');
    if (st.expired && next !== 'confirmed') next = 'expired';
    setStatus(next);
    if (st.error) setError(st.error);
    if (next === 'confirmed') {
      messageRef.current.success(tRef.current('weixin.status.confirmed'));
      void loadAccounts();
    } else if (next === 'binded_redirect') {
      // Already bound on an earlier run — refresh so it shows in the list.
      void loadAccounts();
    }
  }, [loadAccounts]);

  // The poll loop. Depends only on (qrcode, active, resumeKey): everything else
  // is read through refs, so a re-render cannot restart or duplicate the loop.
  useEffect(() => {
    if (!active || !qrcode) return;
    const first = statusRef.current;
    if (TERMINAL.has(first) || first === 'need_verifycode') return;

    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      if (cancelled) return;
      const current = statusRef.current;
      if (TERMINAL.has(current) || current === 'need_verifycode') return;
      try {
        const st = await weixinLoginStatus(qrcode, verifyRef.current || undefined);
        if (cancelled) return;
        applyState(st);
        const after = String(st.status || '');
        if (TERMINAL.has(after) || after === 'need_verifycode') return;
      } catch (e) {
        if (!cancelled) {
          setError(formatError(e, tRef.current('weixin.error.status')));
        }
      }
      if (!cancelled) timer = window.setTimeout(tick, WEIXIN_POLL_MS);
    };
    // Poll once immediately; the server's own long poll provides the wait.
    timer = window.setTimeout(tick, 0);
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [qrcode, active, resumeKey, applyState]);

  const resetQr = () => {
    setQrcode(null);
    setQrUrl(null);
    setStatus('');
    setVerifyCode('');
  };

  const removeAccount = (account: WeixinAccount) => {
    modal.confirm({
      title: `${t('weixin.title')} · ${account.name || account.id}`,
      content: t('weixin.deleteConfirm'),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      okButtonProps: { danger: true },
      onOk: async () => {
        await deleteWeixinAccount(account.id);
        await loadAccounts();
      },
    });
  };

  const box: React.CSSProperties = {
    border: `1px solid ${tokens.border}`,
    background: tokens.bgLay1,
    borderRadius: 8,
    padding: 12,
  };

  const expired = status === 'expired';

  return (
    <div
      data-testid="weixin-panel"
      style={{ display: 'flex', flexDirection: 'column', gap: 16 }}
    >
      <Text style={{ color: tokens.labelSecondary }}>{t('weixin.intro')}</Text>

      {/* The QR: click to generate, scan, watch the status line. */}
      <div
        style={{
          ...box, display: 'flex', flexDirection: 'column',
          alignItems: 'center', gap: 12, padding: 16,
        }}
      >
        {!qrcode ? (
          <>
            <Button
              type="primary"
              size="large"
              data-testid="weixin-generate"
              loading={busy}
              onClick={start}
            >
              {t('weixin.generate')}
            </Button>
            <Text style={{ color: tokens.labelTertiary, fontSize: 11, textAlign: 'center' }}>
              {t('weixin.scanHint')}
            </Text>
          </>
        ) : (
          <>
            {qrUrl ? (
              <img
                data-testid="weixin-qr"
                src={qrUrl}
                alt={t('weixin.title')}
                width={220}
                height={220}
                onClick={() => setZoom(true)}
                style={{
                  background: '#fff', padding: 8, borderRadius: 8,
                  border: `1px solid ${tokens.border}`, cursor: 'zoom-in',
                }}
              />
            ) : (
              <Spin />
            )}
            <Text
              data-testid="weixin-status"
              style={{ color: tokens.labelSecondary, textAlign: 'center' }}
            >
              {t(weixinStatusKey(status))}
            </Text>
            <Space>
              {expired && (
                <Button
                  size="small"
                  type="primary"
                  data-testid="weixin-regenerate"
                  loading={busy}
                  onClick={start}
                >
                  {t('weixin.regenerate')}
                </Button>
              )}
              {status !== 'confirmed' && (
                <Button size="small" data-testid="weixin-cancel" onClick={resetQr}>
                  {t('common.cancel')}
                </Button>
              )}
            </Space>
            {qrUrl && (
              <Text style={{ color: tokens.labelTertiary, fontSize: 11 }}>
                {t('weixin.zoomHint')}
              </Text>
            )}
          </>
        )}
      </div>

      {/* Pairing code: shown when the phone asks for the digits. */}
      {status === 'need_verifycode' && (
        <div
          data-testid="weixin-verify"
          style={{ ...box, display: 'flex', flexDirection: 'column', gap: 8 }}
        >
          <Text style={{ color: tokens.labelPrimary }}>{t('weixin.verifyLabel')}</Text>
          <Space wrap>
            <Input
              data-testid="weixin-verify-input"
              value={verifyCode}
              placeholder={t('weixin.verifyPlaceholder')}
              style={{ width: 180 }}
              onChange={(e) => setVerifyCode(e.target.value)}
            />
            <Button
              type="primary"
              data-testid="weixin-verify-submit"
              disabled={!verifyCode.trim()}
              onClick={() => {
                setError(null);
                // Re-arm the loop: flip the label back to "scanned" so the
                // guard no longer treats this as "waiting for the user", and
                // bump the key so the effect re-subscribes and sends the code.
                setStatus('scaned');
                setResumeKey((k) => k + 1);
              }}
            >
              {t('weixin.verifySubmit')}
            </Button>
          </Space>
        </div>
      )}

      {error && (
        <Alert
          type="error"
          showIcon
          data-testid="weixin-error"
          message={error}
        />
      )}

      <Divider style={{ margin: 0 }} />

      {/* Bound accounts. */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ fontWeight: 600, color: tokens.labelPrimary }}>
            {t('weixin.accounts')}
          </span>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            data-testid="weixin-add"
            loading={busy}
            onClick={() => {
              resetQr();
              void start();
            }}
          >
            {t('weixin.addAccount')}
          </Button>
        </div>
        {loadingAccounts ? (
          <Spin size="small" />
        ) : accounts.length === 0 ? (
          <Text data-testid="weixin-no-accounts" style={{ color: tokens.labelTertiary }}>
            {t('weixin.noAccounts')}
          </Text>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {accounts.map((a) => (
              <div
                key={a.id}
                data-testid={`weixin-account-${a.id}`}
                style={{ ...box, display: 'flex', alignItems: 'center', gap: 10 }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ color: tokens.labelPrimary }}>
                    {a.name || a.id}
                    <span
                      style={{
                        fontFamily: 'monospace', fontSize: 11,
                        color: tokens.labelTertiary, marginLeft: 8,
                      }}
                    >
                      {a.id}
                    </span>
                  </div>
                  <Space size={6} style={{ marginTop: 4 }}>
                    <Tag color={a.status === 'online' ? 'green'
                      : a.status === 'error' ? 'red' : undefined}>
                      {t(weixinAccountStatusKey(a.status))}
                    </Tag>
                    {a.running ? <Tag color="blue">{t('weixin.accountOnline')}</Tag> : null}
                  </Space>
                </div>
                <Button
                  danger
                  size="small"
                  icon={<DeleteOutlined />}
                  data-testid={`weixin-account-delete-${a.id}`}
                  onClick={() => removeAccount(a)}
                />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Tap the code to enlarge it for a phone camera. */}
      <Modal
        open={zoom}
        footer={null}
        title={t('weixin.zoomTitle')}
        onCancel={() => setZoom(false)}
        destroyOnHidden
      >
        {qrUrl && (
          <img
            data-testid="weixin-qr-zoom"
            src={qrUrl}
            alt={t('weixin.title')}
            style={{ width: '100%', background: '#fff' }}
          />
        )}
      </Modal>
    </div>
  );
};

/** The rail entry point: a right-hand drawer holding the panel. */
export const WeixinDrawer: React.FC<{ open: boolean; onClose: () => void }> = ({
  open, onClose,
}) => {
  const t = useT();
  const tokens = useThemeTokens();
  return (
    <Drawer
      title={
        <span>
          <MobileOutlined style={{ marginInlineEnd: 8 }} />
          {t('weixin.title')}
        </span>
      }
      placement="right"
      width={420}
      open={open}
      onClose={onClose}
      destroyOnHidden
      styles={{ body: { padding: 16, background: tokens.bgBase } }}
    >
      <WeixinPanel active={open} />
    </Drawer>
  );
};
