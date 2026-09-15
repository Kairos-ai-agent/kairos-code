/**
 * UpdateBanner — "a newer Kairos exists", with one click to install it.
 *
 * R38.9, tier B. The rules this component exists to respect:
 *   - the check is a cached, read-only lookup, and a failure is silent;
 *   - the download happens only on the button, never on startup;
 *   - when this install cannot replace itself — a source checkout, a read-only
 *     directory, macOS — say so and point at the release page instead of
 *     pretending it worked;
 *   - a release without a published checksum never self-replaces.
 */
import React, { useEffect, useState } from 'react';
import { Alert, Button, Space, message } from 'antd';
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons';

import api from '../api/client';
import { useT } from '../i18n';
import { useThemeTokens } from '../hooks/useThemeTokens';

interface Asset {
  name: string;
  url: string;
  size: number;
  sha256: string;
}

export interface UpdateInfo {
  current: string;
  latest: string;
  hasUpdate: boolean;
  enabled: boolean;
  notesUrl: string;
  publishedAt: string;
  asset: Asset | null;
  canSelfUpdate: boolean;
  reason: string;
  error: string | null;
}

const UpdateBanner: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState(false);

  useEffect(() => {
    let alive = true;
    api.get<UpdateInfo>('/update/check')
      .then((r) => { if (alive) setInfo(r.data); })
      .catch(() => { /* an update check must never nag about the network */ });
    return () => { alive = false; };
  }, []);

  if (!info || !info.enabled || !info.hasUpdate) return null;

  const explain = (reason: string) => {
    // Literal keys per branch: a computed key hides typos from check_i18n.mjs.
    switch (reason) {
      case 'not-a-packaged-build': return t('updates.reason.notPackaged');
      case 'macos-notify-only': return t('updates.reason.macos');
      case 'install-dir-not-writable': return t('updates.reason.readOnly');
      case 'no-checksum-published': return t('updates.reason.noChecksum');
      case 'checksum-mismatch': return t('updates.reason.checksumMismatch');
      case 'no-asset-for-platform': return t('updates.reason.noAssetForPlatform');
      default: return t('updates.applyFailed');
    }
  };

  const apply = async () => {
    setBusy(true);
    try {
      const r = await api.post<{ ok: boolean; reason: string }>('/update/apply');
      if (r.data?.ok) {
        setApplied(true);
      } else {
        message.warning(explain(String(r.data?.reason || '')));
      }
    } catch {
      message.error(t('updates.applyFailed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Alert
      data-testid="update-banner"
      type="info"
      showIcon
      style={{ margin: '8px 12px 0', borderRadius: 8 }}
      message={t('updates.available', { version: info.latest, current: info.current })}
      description={
        applied ? (
          <span>{t('updates.applied')}</span>
        ) : !info.canSelfUpdate ? (
          <span style={{ color: tokens.labelTertiary }}>{explain(info.reason)}</span>
        ) : undefined
      }
      action={
        <Space direction="vertical" size={4}>
          <Space size={6}>
            {info.canSelfUpdate && !applied ? (
              <Button
                size="small"
                type="primary"
                icon={<DownloadOutlined />}
                loading={busy}
                onClick={apply}
                data-testid="update-apply"
              >
                {busy ? t('updates.applying') : t('updates.apply')}
              </Button>
            ) : null}
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => window.open(info.notesUrl, '_blank', 'noopener')}
              data-testid="update-notes"
            >
              {t('updates.notes')}
            </Button>
          </Space>
        </Space>
      }
    />
  );
};

export default UpdateBanner;
