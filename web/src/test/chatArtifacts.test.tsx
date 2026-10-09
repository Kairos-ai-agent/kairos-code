/**
 * Chat artifacts — handing the agent's produced files to the user in the thread.
 *
 * The contract these pin (frozen by the backend):
 *   - `POST /projects/{id}/chat` returns `artifacts` (always present, [] when
 *     the turn produced nothing) and the same array is written onto the reply's
 *     `metadata.artifacts`, so a refresh restores the very same cards from the
 *     persisted history;
 *   - the download endpoint is
 *     `GET /api/projects/{id}/artifacts/download?path=<relative path>`.
 *
 * What must not break: a turn with no products shows no cards; a missing or
 * misshapen `metadata.artifacts` (real data comes out of the DB, written by
 * whatever backend build was live then) degrades to "no cards" and never
 * throws; and the download URL is URL-encoded, because the whole relative path
 * is one query value.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const get = vi.fn();
const post = vi.fn();
vi.mock('../api/client', () => ({
  default: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
  },
  // The Chat page imports these; the component-under-test does not, but the
  // module must still export them or the page's import fails.
  connectWebSocket: vi.fn(),
  onWebSocketMessage: vi.fn(() => () => {}),
  onWebSocketState: vi.fn(() => () => {}),
}));

import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ConfigProvider, App as AntdApp } from 'antd';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

import ChatThread from '../components/ChatThread';
import Chat from '../pages/Chat';
import { useChatStore } from '../stores/chatStore';
import { normalizeArtifacts, artifactDownloadUrl, isPreviewable } from '../utils/artifacts';
import type { Message, Project } from '../types';

const PROJECT: Project = {
  id: 'p1', name: 'demo', description: '', workspace: 'D:/demo',
  status: 'active', task_count: 0, agent_count: 0, created_at: 0,
};

const msg = (over: Partial<Message>): Message => ({
  id: Math.random().toString(36).slice(2),
  sender: 'agent',
  receiver: 'user',
  topic: 'agent.chat',
  content: '',
  msg_type: 'text',
  timestamp: 1000,
  metadata: {},
  ...over,
} as Message);

const question = (text: string) =>
  msg({ sender: 'user', topic: 'user.message', content: text });

/** A reply carrying the artifacts the backend put on its metadata. */
const reply = (text: string, artifacts?: unknown) =>
  msg({ topic: 'agent.chat', content: text, metadata: { artifacts } });

const renderThread = (messages: Message[]) => render(<ChatThread messages={messages} />);

const cardNames = () =>
  screen.getAllByTestId('chat-artifact-name').map((el) => el.textContent);

describe('ChatThread artifacts', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    // Inside act(): a mounted ChatArtifacts subscribes to the project id, and a
    // store write while it is still on screen has to be flushed as a React
    // update, not applied behind its back.
    act(() => { useChatStore.setState({ currentProject: PROJECT }); });
  });

  afterEach(() => {
    act(() => { useChatStore.setState({ currentProject: null }); });
  });

  it('renders one card per artifact, with name, size and the frozen download URL', () => {
    renderThread([
      question('build the report'),
      reply('done', [
        { path: 'out/report.md', name: 'report.md', size: 2048, mime: 'text/markdown' },
        { path: 'assets/shot-1.png', name: 'shot-1.png', size: 1536, mime: 'image/png' },
      ]),
    ]);

    expect(screen.getAllByTestId('chat-artifact').length).toBe(2);
    expect(cardNames()).toEqual(['report.md', 'shot-1.png']);
    expect(screen.getAllByTestId('chat-artifact-size')[0].textContent).toBe('2.0 KB');
    expect(screen.getAllByTestId('chat-artifact-size')[1].textContent).toBe('1.5 KB');

    const links = screen.getAllByTestId('chat-artifact-download');
    expect(links[0]).toHaveAttribute(
      'href', '/api/projects/p1/artifacts/download?path=out%2Freport.md');
    expect(links[1]).toHaveAttribute(
      'href', '/api/projects/p1/artifacts/download?path=assets%2Fshot-1.png');

    // Preview is offered for text/markdown only — the PNG is download-only.
    expect(screen.getAllByTestId('chat-artifact-preview').length).toBe(1);
  });

  it('URL-encodes the whole relative path (slashes, spaces, #, non-ASCII)', () => {
    renderThread([
      question('q'),
      reply('done', [{
        path: 'out/我的 report #1.md', name: '我的 report #1.md',
        size: 10, mime: 'text/markdown',
      }]),
    ]);

    expect(screen.getByTestId('chat-artifact-download')).toHaveAttribute(
      'href',
      '/api/projects/p1/artifacts/download?path='
        + 'out%2F%E6%88%91%E7%9A%84%20report%20%231.md',
    );
  });

  it('renders no cards and does not crash when the turn produced nothing', () => {
    renderThread([question('q'), reply('just an answer')]);

    expect(screen.queryByTestId('chat-artifacts')).toBeNull();
    expect(screen.queryByTestId('chat-artifact')).toBeNull();
    expect(screen.getByTestId('assistant-bubble').textContent).toContain('just an answer');
  });

  it('degrades silently when metadata.artifacts is missing or misshapen', () => {
    // Not an array at all.
    renderThread([question('q1'), reply('a', 'this is not a list')]);
    expect(screen.queryByTestId('chat-artifact')).toBeNull();

    // An array of junk: no usable `path` anywhere → no cards, no throw.
    renderThread([
      question('q2'),
      reply('b', [{}, null, 42, 'x', { path: '' }, { path: '   ' }]),
    ]);
    expect(screen.queryByTestId('chat-artifact')).toBeNull();
  });

  it('keeps a card whose entry is missing name / size / mime (historical rows)', () => {
    renderThread([question('q'), reply('done', [{ path: 'out/notes.txt' }])]);

    expect(cardNames()).toEqual(['notes.txt']);          // name from the path
    expect(screen.getByTestId('chat-artifact-size').textContent).toBe('0 B');
    // No mime, but it is a .txt by extension → the mime path stays text-only,
    // so no preview button is invented from the extension (except .md).
    expect(screen.queryByTestId('chat-artifact-preview')).toBeNull();
  });

  it('fetches a preview from the frozen endpoint and shows the text', async () => {
    get.mockResolvedValue({ data: '# Notes\nsecond line' });
    renderThread([
      question('q'),
      reply('done', [{ path: 'out/notes.md', name: 'notes.md', size: 20, mime: 'text/markdown' }]),
    ]);

    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-artifact-preview'));
      // Let the (immediately-resolving) preview promise land while React is
      // still inside act, so its state update is flushed here, not after.
      await new Promise((r) => setTimeout(r, 0));
    });

    await waitFor(() => expect(screen.getByTestId('chat-artifact-preview-text')).toBeTruthy());
    expect(get).toHaveBeenCalledTimes(1);
    expect(get.mock.calls[0][0]).toBe('/projects/p1/artifacts/download');
    expect(get.mock.calls[0][1]).toMatchObject({ params: { path: 'out/notes.md' } });
    expect(screen.getByTestId('chat-artifact-preview-text').textContent)
      .toContain('second line');
  });

  it('a failed preview degrades to a hint instead of throwing', async () => {
    get.mockRejectedValue(new Error('410 gone'));
    renderThread([
      question('q'),
      reply('done', [{ path: 'out/notes.md', name: 'notes.md', size: 20, mime: 'text/markdown' }]),
    ]);

    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-artifact-preview'));
      // Let the (immediately-resolving) preview promise land while React is
      // still inside act, so its state update is flushed here, not after.
      await new Promise((r) => setTimeout(r, 0));
    });

    await waitFor(() => expect(screen.getByTestId('chat-artifact-preview-error')).toBeTruthy());
    // The card and its download link survive the failure.
    expect(screen.getByTestId('chat-artifact-download')).toBeTruthy();
    expect(screen.getByTestId('chat-artifact-name').textContent).toBe('notes.md');
  });
});

describe('artifact helpers', () => {
  it('normalizeArtifacts keeps only entries with a usable path', () => {
    expect(normalizeArtifacts(undefined)).toEqual([]);
    expect(normalizeArtifacts({ path: 'x' })).toEqual([]);
    expect(normalizeArtifacts([{ path: 'a/b.md', name: 'b.md', size: 5, mime: 'text/markdown' }]))
      .toEqual([{ path: 'a/b.md', name: 'b.md', size: 5, mime: 'text/markdown' }]);
    // Negative / NaN size → 0, blank name → basename, blank mime → ''.
    expect(normalizeArtifacts([{ path: 'x/y.txt', name: '  ', size: NaN }]))
      .toEqual([{ path: 'x/y.txt', name: 'y.txt', size: 0, mime: '' }]);
  });

  it('artifactDownloadUrl encodes both halves', () => {
    expect(artifactDownloadUrl('p 1', 'a/b c.md'))
      .toBe('/api/projects/p%201/artifacts/download?path=a%2Fb%20c.md');
  });

  it('isPreviewable follows the mime, with markdown-by-extension as fallback', () => {
    expect(isPreviewable({ path: 'a.md', name: 'a.md', size: 1, mime: 'text/markdown' })).toBe(true);
    expect(isPreviewable({ path: 'a.txt', name: 'a.txt', size: 1, mime: 'text/plain' })).toBe(true);
    expect(isPreviewable({ path: 'a.md', name: 'a.md', size: 1, mime: '' })).toBe(true);
    expect(isPreviewable({ path: 'a.png', name: 'a.png', size: 1, mime: 'image/png' })).toBe(false);
    expect(isPreviewable({ path: 'a.zip', name: 'a.zip', size: 1, mime: 'application/zip' })).toBe(false);
  });
});

/**
 * The other half of "both sources": the files a turn returns RIGHT NOW, in the
 * `POST /chat` response body. The page has to put them on the reply message it
 * appends, or the cards would only ever appear after a refresh.
 */
describe('Chat page: artifacts from the chat response', () => {
  const ARTIFACTS = [
    { path: 'out/report.md', name: 'report.md', size: 2048, mime: 'text/markdown' },
  ];

  const renderChat = () => render(
    <ConfigProvider>
      <AntdApp>
        <MemoryRouter initialEntries={['/chat']}>
          <Routes>
            <Route path="/chat" element={<Chat />} />
            <Route path="/chat/:sessionId" element={<Chat />} />
          </Routes>
        </MemoryRouter>
      </AntdApp>
    </ConfigProvider>,
  );

  const submit = async (text: string) => {
    renderChat();
    const box = await screen.findByPlaceholderText(/route to chat/i);
    fireEvent.change(box, { target: { value: text } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('composer-send'));
    });
  };

  beforeEach(() => {
    act(() => {
      useChatStore.getState().reset();
      useChatStore.getState().setProjects([PROJECT]);
      useChatStore.getState().setCurrentProject(PROJECT);
    });
    // Every background GET (loop/plan/ask/sessions/rounds/history) is fine with
    // an empty object: each caller has its own `|| []` / `|| {}` guard.
    get.mockResolvedValue({ data: {} });
    post.mockReset();
  });

  it('puts the response artifacts on the reply it appends, and shows a card', async () => {
    post.mockImplementation((url: string) => (
      url === '/projects/p1/chat'
        ? Promise.resolve({ data: { reply: 'written.', mode: 'chat', artifacts: ARTIFACTS } })
        : Promise.resolve({ data: {} })
    ));

    await submit('write the summary into a file');

    const replies = useChatStore.getState().currentMessages
      .filter((m) => (m.sender || '').includes('coder'));
    expect(replies.length).toBe(1);
    expect(replies[0].content).toBe('written.');
    expect(normalizeArtifacts(replies[0].metadata?.artifacts)).toEqual([{
      path: 'out/report.md', name: 'report.md', size: 2048, mime: 'text/markdown',
    }]);

    // …and the card is on screen, pointing at the frozen endpoint.
    expect(screen.getByTestId('chat-artifact-name').textContent).toBe('report.md');
    expect(screen.getByTestId('chat-artifact-download')).toHaveAttribute(
      'href', '/api/projects/p1/artifacts/download?path=out%2Freport.md');
  });

  it('attaches them to the reply the WebSocket already showed, without a duplicate bubble', async () => {
    // The streamed reply lands first (the real order for a chat turn). Its
    // event carries no artifacts.
    act(() => {
      useChatStore.getState().appendMessage({
        id: 'ws-reply-1', sender: 'coder', receiver: 'user', topic: 'agent.chat',
        content: 'written.', msg_type: 'text',
        timestamp: Date.now() / 1000, metadata: {},
      });
    });
    post.mockImplementation((url: string) => (
      url === '/projects/p1/chat'
        ? Promise.resolve({ data: { reply: 'written.', mode: 'chat', artifacts: ARTIFACTS } })
        : Promise.resolve({ data: {} })
    ));

    await submit('write the summary into a file');

    const replies = useChatStore.getState().currentMessages
      .filter((m) => (m.sender || '').includes('coder'));
    // Exactly the one bubble — no second copy from the REST reply…
    expect(replies.length).toBe(1);
    expect(replies[0].id).toBe('ws-reply-1');
    // …and the files are now on it, so the card renders.
    expect(normalizeArtifacts(replies[0].metadata?.artifacts).length).toBe(1);
    expect(screen.getByTestId('chat-artifact-name').textContent).toBe('report.md');
  });

  it('shows no card when the response carries an empty artifacts array', async () => {
    post.mockImplementation((url: string) => (
      url === '/projects/p1/chat'
        ? Promise.resolve({ data: { reply: 'all done.', mode: 'chat', artifacts: [] } })
        : Promise.resolve({ data: {} })
    ));

    await submit('write the summary into a file');

    expect(screen.getByTestId('assistant-bubble').textContent).toContain('all done.');
    expect(screen.queryByTestId('chat-artifact')).toBeNull();
  });

  it('survives an artifacts value that is not an array', async () => {
    post.mockImplementation((url: string) => (
      url === '/projects/p1/chat'
        ? Promise.resolve({ data: { reply: 'done.', mode: 'chat', artifacts: 'oops' } })
        : Promise.resolve({ data: {} })
    ));

    await submit('write the summary into a file');

    expect(screen.queryByTestId('chat-artifact')).toBeNull();
    expect(screen.getByTestId('assistant-bubble').textContent).toContain('done.');
  });
});
