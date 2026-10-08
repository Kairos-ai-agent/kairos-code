/**
 * FullAccessToggle — the global "full access" switch (``settings.fullAccess``).
 *
 * Why it exists: the tool sandbox (``kairos/access_control.py``) refuses every
 * path outside the project root unless ``fullAccess`` is on, and until now the
 * only ways to turn it on were the ``KAIROS_FULL_ACCESS`` env var or a raw
 * ``POST /api/projects/settings``. On a fresh install the field defaults to
 * ``false``, so a user who moved their workspace to another drive saw
 * "cannot read D:/…" with no way to fix it from the UI.
 *
 * Behaviour:
 *   - The initial state is read from the backend (`GET /projects/settings`).
 *     It is never assumed: a failed read shows an error next to a disabled
 *     switch rather than silently rendering as "off".
 *   - Turning it **on** goes through one Popconfirm (it disables the sandbox).
 *     Turning it **off** writes immediately — no confirmation.
 *   - The switch is optimistic while the write is in flight and **rolls back**
 *     to the previous value (with a toast) if the write fails.
 *
 * The judgement is recomputed by the backend on every tool call, so a change
 * here takes effect immediately — no restart.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { App as AntdApp, Popconfirm, Switch, Tooltip } from 'antd';

import api from '../api/client';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { useT } from '../i18n';

type Phase = 'loading' | 'ready' | 'error';

const FullAccessToggle: React.FC = () => {
  const tokens = useThemeTokens();
  const t = useT();
  const { message: msgApi } = AntdApp.useApp();

  const [phase, setPhase] = useState<Phase>('loading');
  const [on, setOn] = useState(false);
  const [saving, setSaving] = useState(false);

  /** Ask the backend for the current value. */
  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const r = await api.get('/projects/settings');
      // Strict === true: only a real boolean from the server counts as "on".
      setOn(((r?.data || {}) as { fullAccess?: unknown }).fullAccess === true);
      setPhase('ready');
    } catch {
      // Never silently show "off" when we simply could not read the state.
      setPhase('error');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  /**
   * Persist `next` and keep the display honest:
   *   - optimistic flip so the UI answers the click immediately,
   *   - adopt the server's echoed value on success (it is the truth),
   *   - roll back to `prev` + toast on failure.
   */
  const apply = useCallback(async (next: boolean) => {
    if (phase !== 'ready' || saving) return;
    const prev = on;
    setSaving(true);
    setOn(next);
    try {
      const r = await api.post('/projects/settings', { fullAccess: next });
      const echoed = ((r?.data || {}) as { fullAccess?: unknown }).fullAccess;
      setOn(typeof echoed === 'boolean' ? echoed : next);
    } catch {
      setOn(prev);
      msgApi.error(t('chat.composer.fullAccessWriteFailed'));
    } finally {
      setSaving(false);
    }
  }, [phase, saving, on, msgApi, t]);

  const ready = phase === 'ready';
  const failed = phase === 'error';
  const tip = failed
    ? t('chat.composer.fullAccessReadFailed')
    : t('chat.composer.fullAccessTip');
  const labelColor = failed
    ? tokens.danger
    : (on ? tokens.labelPrimary : tokens.labelTertiary);

  return (
    <Tooltip title={tip} placement="top">
      <span
        data-testid="composer-full-access"
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          userSelect: 'none',
        }}
      >
        {/* Popconfirm only guards the switch-on path (it lifts the sandbox);
            switching off writes straight away. */}
        <Popconfirm
          title={t('chat.composer.fullAccessConfirm')}
          okText={t('common.confirm')}
          cancelText={t('common.cancel')}
          placement="topRight"
          disabled={!ready || on || saving}
          onConfirm={() => { void apply(true); }}
        >
          <Switch
            size="small"
            checked={on}
            loading={phase === 'loading' || saving}
            disabled={!ready || saving}
            onChange={(next) => {
              // The "on" click is confirmed by the Popconfirm above; only the
              // "off" click acts here.
              if (!next) void apply(false);
            }}
            data-testid="composer-full-access-switch"
            aria-label={t('chat.composer.fullAccess')}
          />
        </Popconfirm>
        <span
          data-testid="composer-full-access-label"
          style={{ fontSize: 11, color: labelColor, whiteSpace: 'nowrap' }}
        >
          {t('chat.composer.fullAccess')}
        </span>
        {failed && (
          <span
            data-testid="composer-full-access-error"
            style={{ fontSize: 11, color: tokens.danger, whiteSpace: 'nowrap' }}
          >
            {t('chat.composer.fullAccessReadFailed')}
          </span>
        )}
      </span>
    </Tooltip>
  );
};

export default FullAccessToggle;
