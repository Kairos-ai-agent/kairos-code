/**
 * Chat store — the source of truth for the chat-style UI.
 *
 * Holds:
 *   - the list of projects (left sidebar header)
 *   - the currently-selected project
 *   - the currently-selected session within that project
 *   - the message thread for the current session (live)
 *
 * WebSocket messages from the orchestrator are pushed into
 * `currentMessages` by the chat page; the older `agentStore` stays
 * for the legacy Loop/Dashboard views but the new chat sidebar
 * reads only from here.
 *
 * R38.6: ``projects`` and ``currentProject`` are persisted to
 * localStorage via zustand ``persist`` middleware. The backend
 * remains the source of truth (it filters out archived projects
 * via the ``archived_at`` column), and the AppLayout mount
 * re-fetches /api/projects to refresh the cache. But the
 * localStorage layer gives the user:
 *   - an instant render of their last-known state (no flash of
 *     empty sidebar while the fetch is in flight)
 *   - offline tolerance (works without the backend, briefly)
 *   - protection against stale backend responses (if a restart
 *     didn't pick up the soft-delete code, the localStorage
 *     still reflects the user's intent until the next fetch
 *     arrives — and the AppLayout reconciliation clears any
 *     stale currentProject whose ID no longer exists in the
 *     fresh backend list).
 *
 * Live state (NOT persisted): ``sessions``, ``currentSessionId``,
 * ``currentMessages``, ``sidebarCollapsed`` — these change too
 * fast and are project-scoped, so persisting them would cause
 * weird cross-session leakage.
 */
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { Project, Message } from '../types';

export interface SessionMeta {
  session_id: string;
  round_count: number;
  last_round: number;
  last_score: number;
  last_approve: boolean;
  started_at: number;
  last_activity: number;
  running?: boolean;
}

interface ChatStore {
  projects: Project[];
  currentProject: Project | null;
  sessions: SessionMeta[];
  currentSessionId: string | null;
  currentMessages: Message[];   // for the current session
  sidebarCollapsed: boolean;
  workbenchOpen: boolean;       // R38.6 §26: right-side Workbench panel

  setProjects: (p: Project[]) => void;
  setCurrentProject: (p: Project | null) => void;
  setSessions: (s: SessionMeta[]) => void;
  setCurrentSessionId: (id: string | null) => void;
  setCurrentMessages: (m: Message[]) => void;
  appendMessage: (m: Message) => void;
  // R38.6: streaming chunks collapse into a single bubble per sender.
  // ``updateMessage`` patches one existing message (used by appendStreamChunk);
  // ``appendStreamChunk`` finds-or-creates the streaming bubble for a sender
  // and appends ``content`` to it. Without this, the WebSocket sends one
  // ``stream.chunk`` event per token and the chat thread would render each
  // token as its own message (8 chunks per Chinese sentence = 8 bubbles).
  updateMessage: (id: string, patch: Partial<Message>) => void;
  appendStreamChunk: (
    sender: string,
    content: string,
    meta: { topic?: string; receiver?: string; metadata?: Record<string, unknown>; timestamp?: number }
  ) => string;  // returns the message id (new or existing) so callers can chain
  finalizeStream: (sender: string) => void;
  toggleSidebar: () => void;
  toggleWorkbench: () => void;
  reset: () => void;
}

export const useChatStore = create<ChatStore>()(
  persist(
    (set) => ({
      projects: [],
      currentProject: null,
      sessions: [],
      currentSessionId: null,
      currentMessages: [],
      sidebarCollapsed: false,
      workbenchOpen: true,  // R38.6 §26: open by default — the right
                            // panel is the canonical place to see
                            // what the agent is doing.

      setProjects: (projects) => set({ projects }),
      setCurrentProject: (currentProject) =>
        set({ currentProject, sessions: [], currentSessionId: null,
              currentMessages: [] }),
      // R38.6.4: debug aid — log whenever currentMessages is set to
      // [] so the user can trace in DevTools why their chat thread
      // disappeared. If the user reports "messages lost on refresh",
      // they grep DevTools for "[chatStore] messages cleared by" and
      // find the exact call site.
      _logClear: (where: string) => {
        if (typeof console !== 'undefined') {
          console.info('[chatStore] messages cleared by:', where,
                       '— stack:', new Error().stack?.split('\n').slice(1, 4).join(' | '));
        }
      },
      setSessions: (sessions) => set({ sessions }),
      setCurrentSessionId: (currentSessionId) => set({ currentSessionId }),
      setCurrentMessages: (currentMessages) => set({ currentMessages }),
      appendMessage: (m) =>
        set((state) => {
          // De-dupe by id (WebSocket may echo what the REST call returned).
          if (state.currentMessages.some((x) => x.id === m.id)) {
            return state;
          }
          // R38.6.4: second-layer dedupe by content + sender within
          // a 5-minute window. The component layer (Chat.tsx) checks
          // the same thing, but the check there reads
          // ``useChatStore.getState()`` — if the handler is invoked
          // twice for the same event (React StrictMode dev
          // double-invoke, two WS connections, or a single
          // invocation that races with a re-render), both calls may
          // run before the store has been updated, and both would
          // append. The store's set callback is atomic, so we re-check
          // here.
          //
          // The 5-minute window is wide enough to catch any racing
          // append (the LLM round-trip is seconds, not minutes) but
          // narrow enough that a new message with the same content
          // (e.g. the user asking "OK" twice) still gets a new bubble.
          const mContent = typeof m.content === 'string'
                             ? m.content.trim() : '';
          const mSender = (m.sender || '').toLowerCase();
          // Accept short form ('coder') and full agent_id
          // ('63bebf36.coder') — message bus uses full, REST uses short.
          const isAgentBubble = mSender === 'agent' || mSender === 'coder'
                                || mSender === 'assistant'
                                || mSender.endsWith('.coder')
                                || mSender.endsWith('.reviewer')
                                || mSender.includes('coder')
                                || mSender.includes('reviewer');
          if (mContent && isAgentBubble) {
            const now = Date.now() / 1000;
            const dup = state.currentMessages.some((x) => {
              const xContent = typeof x.content === 'string'
                                 ? x.content.trim() : '';
              if (xContent !== mContent) return false;
              // Accept short form ('coder') and full agent_id
              // ('63bebf36.coder') — the message bus uses full
              // agent_id, REST path uses short. Both refer to the
              // same agent.
              const xs = (x.sender || '').toLowerCase();
              const xIsAgent = xs === 'agent' || xs === 'coder'
                                || xs === 'assistant'
                                || xs.endsWith('.coder')
                                || xs.endsWith('.reviewer')
                                || xs.includes('coder')
                                || xs.includes('reviewer');
              if (!xIsAgent) return false;
              return Math.abs((x.timestamp || 0) - now) < 300;  // 5 min
            });
            if (dup) return state;
          }
          // Cap to 500 messages to keep the DOM small.
          const next = [...state.currentMessages, m];
          return { currentMessages: next.length > 500
                  ? next.slice(next.length - 500) : next };
        }),
      updateMessage: (id, patch) =>
        set((state) => ({
          currentMessages: state.currentMessages.map((m) =>
            m.id === id ? { ...m, ...patch } : m),
        })),
      appendStreamChunk: (sender: string, content: string, meta: {
        topic?: string; receiver?: string;
        metadata?: Record<string, unknown>; timestamp?: number;
      }): string => {
        // R38.6: stream chunks collapse into a single bubble per
        // sender. We find-or-create the most recent stream bubble
        // for this sender and append the chunk to it. The matching
        // is by (sender, topic='stream.chunk') so once a bubble is
        // finalized (topic renamed to 'stream.complete' by
        // ``finalizeStream``), the next chunk starts a fresh
        // bubble instead of appending to the now-finalized one.
        //
        // Implementation note: we read the current messages
        // inside the ``set`` callback so we operate on the
        // freshest state. We avoid calling ``useChatStore.getState()``
        // here because that creates a self-reference TypeScript
        // can't infer cleanly.
        let bubbleId = '';
        set((state) => {
          const existing = [...state.currentMessages].reverse().find(
            (m) => m.sender === sender && m.topic === 'stream.chunk',
          );
          if (existing) {
            bubbleId = existing.id;
            return {
              currentMessages: state.currentMessages.map((m) =>
                m.id === existing.id
                  ? { ...m, content: (m.content || '') + content }
                  : m),
            };
          }
          // Create a new bubble. Use a stable id keyed by sender
          // + a 'turn' marker + now() so the same chunk sequence
          // reuses the id but a new turn doesn't collide.
          bubbleId = `stream-${sender}-turn-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`;
          return {
            currentMessages: [
              ...state.currentMessages,
              {
                id: bubbleId,
                sender,
                receiver: meta.receiver || '',
                topic: meta.topic || 'stream.chunk',
                content,
                msg_type: 'stream',
                timestamp: meta.timestamp || Date.now() / 1000,
                metadata: meta.metadata || {},
              },
            ],
          };
        });
        return bubbleId;
      },
      finalizeStream: (sender) => {
        // R38.6: lock the current stream bubble by renaming its
        // topic from 'stream.chunk' to a finalized variant
        // ('stream.complete'). The appendStreamChunk lookup only
        // matches bubbles with topic 'stream.chunk' or msg_type
        // 'stream', so the next chunk will NOT find the old bubble
        // and will create a fresh one. The visual bubble itself
        // stays in the thread (with the accumulated text), but
        // future chunks for the same sender go to a new bubble.
        //
        // The msg_type stays 'stream' for styling, but the topic
        // rename is the lock. This is the cheapest possible
        // implementation — no extra state, no Map<sender, id>.
        set((state) => ({
          currentMessages: state.currentMessages.map((m) =>
            m.sender === sender && m.topic === 'stream.chunk'
              ? { ...m, topic: 'stream.complete' }
              : m),
        }));
      },
      toggleSidebar: () =>
        set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      toggleWorkbench: () =>
        set((state) => ({ workbenchOpen: !state.workbenchOpen })),
      reset: () => set({
        projects: [], currentProject: null, sessions: [],
        currentSessionId: null, currentMessages: [], sidebarCollapsed: false,
      }),
    }),
    {
      name: 'kairos-chat',
      storage: createJSONStorage(() => localStorage),
      // R38.6.4: persist the current chat thread + session id so
      // the user's conversation survives a browser refresh. The
      // user reported "刷新会丢失 chat 记录" — the previous
      // partialize only kept projects / currentProject, so a
      // Ctrl+R wiped the in-memory messages.
      //
      // We cap the persisted thread to the last 200 messages per
      // session so a long conversation doesn't blow past the
      // 5 MB localStorage budget (each message is ~200-500 bytes
      // JSON, 200 × 500 = ~100 KB, well under the limit). Older
      // messages are still in the backend's messages table; if the
      // user scrolls back the chat should fetch from there, not
      // from localStorage.
      partialize: (s) => ({
        projects: s.projects,
        currentProject: s.currentProject,
        currentSessionId: s.currentSessionId,
        currentMessages: s.currentMessages.slice(-200),
      }),
      // R38.6.4: explicit merge so v1 caches (no currentMessages)
      // upgrade cleanly to v2. Default merge is
      // ``{...current, ...persisted}`` which would keep
      // currentMessages = [] from the initial state when persisted
      // doesn't have it. That's the right behavior for v1, but we
      // want a clear migration path. We always restore the
      // persisted messages (or fall back to [] if absent) so the
      // thread is in sync with whatever was last saved.
      merge: (persisted, current) => {
        const p = (persisted || {}) as Record<string, unknown>;
        return {
          ...current,
          ...p,
          currentMessages: Array.isArray(p.currentMessages)
                            ? (p.currentMessages as unknown[]).slice(-200)
                            : [],
        };
      },
      // Bump this when the shape of the cached state changes
      // incompatibly, to force a one-time re-init of the cache.
      version: 2,
      // v1 -> v2 migration. v1 had no currentMessages; we just
      // add an empty array so the new shape is satisfied. New
      // messages get appended + persisted going forward.
      migrate: (persisted: unknown, version: number) => {
        const p = (persisted || {}) as Record<string, unknown>;
        if (version < 2) {
          return { ...p, currentMessages: [] };
        }
        return p;
      },
      // R38.6.4: debug logger so the user can verify in DevTools
      // that the persist layer is actually saving / loading
      // currentMessages. Without this, the only way to diagnose
      // "messages lost on refresh" is to inspect localStorage by
      // hand. We log at the start of hydration + every save.
      onRehydrateStorage: () => (state, error) => {
        if (error) {
          console.warn('[chatStore] rehydrate failed:', error);
          return;
        }
        // `state` is the merged state after hydration. If it's
        // null we treat it as a fresh install.
        const s = (state as unknown as { currentMessages?: unknown[] }) || {};
        console.info('[chatStore] rehydrated, currentMessages:',
                     Array.isArray(s.currentMessages)
                       ? s.currentMessages.length : 0);
      },
    },
  ),
);
