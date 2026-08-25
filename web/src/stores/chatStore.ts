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
 */
import { create } from 'zustand';
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

  setProjects: (p: Project[]) => void;
  setCurrentProject: (p: Project | null) => void;
  setSessions: (s: SessionMeta[]) => void;
  setCurrentSessionId: (id: string | null) => void;
  setCurrentMessages: (m: Message[]) => void;
  appendMessage: (m: Message) => void;
  toggleSidebar: () => void;
  reset: () => void;
}

export const useChatStore = create<ChatStore>((set) => ({
  projects: [],
  currentProject: null,
  sessions: [],
  currentSessionId: null,
  currentMessages: [],
  sidebarCollapsed: false,

  setProjects: (projects) => set({ projects }),
  setCurrentProject: (currentProject) =>
    set({ currentProject, sessions: [], currentSessionId: null,
          currentMessages: [] }),
  setSessions: (sessions) => set({ sessions }),
  setCurrentSessionId: (currentSessionId) => set({ currentSessionId }),
  setCurrentMessages: (currentMessages) => set({ currentMessages }),
  appendMessage: (m) =>
    set((state) => {
      // De-dupe by id (WebSocket may echo what the REST call returned).
      if (state.currentMessages.some((x) => x.id === m.id)) {
        return state;
      }
      // Cap to 500 messages to keep the DOM small.
      const next = [...state.currentMessages, m];
      return { currentMessages: next.length > 500
              ? next.slice(next.length - 500) : next };
    }),
  toggleSidebar: () =>
    set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  reset: () => set({
    projects: [], currentProject: null, sessions: [],
    currentSessionId: null, currentMessages: [], sidebarCollapsed: false,
  }),
}));
