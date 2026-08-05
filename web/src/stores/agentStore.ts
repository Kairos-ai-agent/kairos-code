import { create } from 'zustand';
import type { AgentState, Message, Project } from '@/types';

interface AgentStore {
  agents: AgentState[];
  messages: Message[];
  projects: Project[];
  currentProject: Project | null;
  selectedAgent: AgentState | null;

  setAgents: (agents: AgentState[]) => void;
  setMessages: (messages: Message[]) => void;
  setProjects: (projects: Project[]) => void;
  setCurrentProject: (project: Project | null) => void;
  setSelectedAgent: (agent: AgentState | null) => void;
  addMessage: (message: Message) => void;
}

export const useAgentStore = create<AgentStore>((set) => ({
  agents: [],
  messages: [],
  projects: [],
  currentProject: null,
  selectedAgent: null,

  setAgents: (agents) => set({ agents }),
  setMessages: (messages) => set({ messages }),
  setProjects: (projects) => set({ projects }),
  setCurrentProject: (project) => set({ currentProject: project }),
  setSelectedAgent: (agent) => set({ selectedAgent: agent }),
  addMessage: (message) =>
    set((state) => ({
      messages: [...state.messages.slice(-99), message],
    })),
}));

// Selectors for granular subscriptions (avoids unnecessary re-renders)
export const useAgents = () => useAgentStore((s) => s.agents);
export const useMessages = () => useAgentStore((s) => s.messages);
export const useSelectedAgent = () => useAgentStore((s) => s.selectedAgent);
