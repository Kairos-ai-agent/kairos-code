/**
 * SkillSearchPalette — Ctrl+K command palette for skill search.
 *
 * Press Ctrl+K anywhere in the app to open a modal with a
 * search input. The results come from /api/skill_search/search
 * (R17 FTS5 + R23 API endpoint). Each result shows the skill
 * name, priority, source path, and a snippet. Clicking a result
 * copies its name to the clipboard (the most common use: paste
 * into a tool_use argument).
 */
import React, { useEffect, useState, useCallback } from 'react';
import { Modal, Input, List, Tag, Empty, Spin, Tooltip } from 'antd';
import { SearchOutlined, ThunderboltOutlined, CopyOutlined } from '@ant-design/icons';

import api from '../api/client';
import { formatError } from '../utils/formatError';

interface SearchResult {
  name: string;
  source_path: string;
  priority: number;
  score: number;
  snippet: string;
}

export const SKILL_SEARCH_PALETTE_EVENT = 'kairos:open_skill_search';

export const openSkillSearchPalette = () => {
  // The Loop page listens for this event and toggles the modal
  window.dispatchEvent(new CustomEvent(SKILL_SEARCH_PALETTE_EVENT));
};

const SkillSearchPalette: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Global Ctrl+K binding
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Ctrl+K (or Cmd+K on Mac)
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Custom event for programmatic open
  useEffect(() => {
    const onOpen = () => setOpen(true);
    window.addEventListener(SKILL_SEARCH_PALETTE_EVENT, onOpen);
    return () => window.removeEventListener(
      SKILL_SEARCH_PALETTE_EVENT, onOpen,
    );
  }, []);

  // Debounced search
  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      setErr(null);
      return;
    }
    setLoading(true);
    setErr(null);
    const handle = setTimeout(async () => {
      try {
        const r = await api.get<{
          query: string; count: number; results: SearchResult[];
        }>('/skill_search/search', { params: { q: query, limit: 15 } });
        setResults(r.data.results || []);
      } catch (e: any) {
        setErr(formatError(e, 'Search failed'));
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 200);
    return () => clearTimeout(handle);
  }, [query]);

  const copyName = useCallback((name: string) => {
    navigator.clipboard?.writeText(name).catch(() => {});
  }, []);

  return (
    <Modal
      open={open}
      onCancel={() => { setOpen(false); setQuery(''); setResults([]); }}
      footer={null}
      width={640}
      title={
        <span>
          <ThunderboltOutlined /> Skill search
          <span style={{ marginLeft: 8, fontSize: 11, color: '#999' }}>
            Ctrl+K
          </span>
        </span>
      }
      destroyOnHidden
    >
      <Input
        size="large"
        prefix={<SearchOutlined />}
        placeholder="Search skills (e.g. pytest, react, json)..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        autoFocus
      />
      <div style={{ marginTop: 12, minHeight: 200 }}>
        {err && <div style={{ color: '#cf1322', padding: 8 }}>{err}</div>}
        {loading && <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin />
        </div>}
        {!loading && !err && query && results.length === 0 && (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                 description={`No skills match "${query}"`} />
        )}
        {!loading && !err && !query && (
          <div style={{ color: '#999', fontSize: 12, padding: 12 }}>
            Type to search the skill library (FTS5-backed).
          </div>
        )}
        {results.length > 0 && (
          <List
            size="small"
            dataSource={results}
            renderItem={(r) => (
              <List.Item
                style={{ cursor: 'pointer' }}
                onClick={() => copyName(r.name)}
                actions={[
                  <Tooltip title="Copy name" key="copy">
                    <CopyOutlined onClick={() => copyName(r.name)} />
                  </Tooltip>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <span>
                      <code style={{ fontSize: 13 }}>{r.name}</code>{' '}
                      <Tag color="blue" style={{ fontSize: 10 }}>
                        prio {r.priority?.toFixed(2) ?? '?'}
                      </Tag>
                      {r.score > 0 && (
                        <Tag style={{ fontSize: 10 }}>
                          score {r.score.toFixed(2)}
                        </Tag>
                      )}
                    </span>
                  }
                  description={
                    <div style={{ fontSize: 11 }}>
                      {r.snippet && (
                        <div style={{ color: '#666' }}>
                          {r.snippet.slice(0, 140)}
                          {r.snippet.length > 140 && '...'}
                        </div>
                      )}
                      {r.source_path && (
                        <div style={{ color: '#bbb', marginTop: 2,
                                      fontFamily: 'monospace', fontSize: 10 }}>
                          {r.source_path}
                        </div>
                      )}
                    </div>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </div>
    </Modal>
  );
};

export default SkillSearchPalette;

