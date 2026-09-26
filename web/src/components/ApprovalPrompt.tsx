/**
 * ApprovalPrompt — the gate's question, where a person can see it.
 *
 * `kairos/sentinel.py` returns ASK for actions that are neither clearly safe nor
 * clearly forbidden. Until R2 that verdict had to be resolved by policy — allow
 * when the gate is not strict, refuse when it is — because there was no channel
 * to ask through; the module's own docstring said so. `kairos/approvals.py` plus
 * `Sentinel.authorize_async` turned the verdict into a real question with a
 * deadline, and this is the other half: the tool call is sitting on a future
 * right now, waiting for one of these two buttons.
 *
 * Mounted once, next to the layout, so the question follows the user to whatever
 * page they are on. It disappears entirely when no channel is wired (a CLI run,
 * a test): then the gate decides on its own as it always did, and a banner
 * promising a question nobody can answer would be worse than nothing.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Space, Tag, Typography, App as AntdApp } from 'antd';

import api from '../api/client';
import { useT } from '../i18n';
import { formatError } from '../utils/formatError';

const { Text } = Typography;

interface PendingApproval {
  id: string;
  tool: string;
  resource?: string;
  reason?: string;
  project_id?: string;
  status?: string;
}

interface ApprovalsPayload {
  wired: boolean;
  pending: PendingApproval[];
}

const POLL_MS = 3000;

const ApprovalPrompt: React.FC = () => {
  const t = useT();
  const { message: msgApi } = AntdApp.useApp();
  const [payload, setPayload] = useState<ApprovalsPayload | null>(null);
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);

  useEffect(() => () => { alive.current = false; }, []);

  const load = useCallback(async () => {
    try {
      const res = await api.get('/approvals');
      if (alive.current) setPayload(res.data as ApprovalsPayload);
    } catch {
      // A process without a channel answers 503; that is not an error worth
      // interrupting anyone over -- it just means there is nothing to show.
      if (alive.current) setPayload({ wired: false, pending: [] });
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const answer = async (request: PendingApproval, allow: boolean) => {
    setBusy(true);
    try {
      await api.post('/approvals/' + encodeURIComponent(request.id),
                     { allow, remember: remember && allow });
      msgApi.success(t(allow ? 'approval.allowed' : 'approval.denied'));
      setRemember(false);
      await load();
    } catch (e) {
      msgApi.error(formatError(e));
      await load();
    } finally {
      setBusy(false);
    }
  };

  const pending = payload?.pending || [];
  if (!payload?.wired || pending.length === 0) return null;

  // The oldest question is the one closest to its deadline.
  const first = pending[0];

  return (
    <div
      data-testid="approval-prompt"
      style={{
        position: 'fixed',
        top: 12,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 1100,
        width: 'min(720px, calc(100vw - 32px))',
      }}
    >
      <Alert
        type="warning"
        showIcon
        message={t('approval.title', { tool: first.tool })}
        description={(
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            {first.resource ? (
              <Text>
                <Text strong>{t('approval.resource')}: </Text>
                <Text code>{first.resource}</Text>
              </Text>
            ) : null}
            {first.reason ? (
              <Text type="secondary">
                {t('approval.why')}: {first.reason}
              </Text>
            ) : null}
            {pending.length > 1 ? (
              <Tag>{t('approval.more', { n: pending.length - 1 })}</Tag>
            ) : null}
            <Checkbox
              data-testid="approval-remember"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            >
              {t('approval.remember')}
            </Checkbox>
          </Space>
        )}
        action={(
          <Space direction="vertical">
            <Button
              type="primary"
              size="small"
              loading={busy}
              data-testid="approval-allow"
              onClick={() => answer(first, true)}
            >
              {t('approval.allow')}
            </Button>
            <Button
              danger
              size="small"
              loading={busy}
              data-testid="approval-deny"
              onClick={() => answer(first, false)}
            >
              {t('approval.deny')}
            </Button>
          </Space>
        )}
      />
    </div>
  );
};

export default ApprovalPrompt;
