/** Tests for the chat store (current project / session / messages). */
import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '../stores/chatStore';
import type { Project, Message } from '../types';

const fakeProject: Project = {
  id: 'p1', name: 'demo', description: '',
  workspace: './w', status: 'active',
  task_count: 0, agent_count: 0, created_at: 1.0,
};

function makeMsg(overrides: Partial<Message> = {}): Message {
  return {
    id: overrides.id || 'm1',
    sender: overrides.sender || 'coder',
    receiver: overrides.receiver || 'user',
    topic: overrides.topic || '',
    content: overrides.content ?? 'hi',
    msg_type: overrides.msg_type || 'text',
    timestamp: overrides.timestamp || 1.0,
    metadata: overrides.metadata || {},
  };
}

describe('chatStore', () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  it('starts empty', () => {
    const s = useChatStore.getState();
    expect(s.projects).toEqual([]);
    expect(s.currentProject).toBeNull();
    expect(s.sessions).toEqual([]);
    expect(s.currentMessages).toEqual([]);
  });

  it('setCurrentProject clears sessions and currentSessionId', () => {
    useChatStore.getState().setCurrentProject(fakeProject);
    useChatStore.getState().setSessions([
      { session_id: 's1', round_count: 0, last_round: 0,
        last_score: 0, last_approve: false,
        started_at: 1, last_activity: 1 },
    ]);
    useChatStore.getState().setCurrentSessionId('s1');
    useChatStore.getState().setCurrentProject(null);
    const s = useChatStore.getState();
    expect(s.sessions).toEqual([]);
    expect(s.currentSessionId).toBeNull();
  });

  it('appendMessage adds and dedupes by id', () => {
    const m = makeMsg({ id: 'a' });
    useChatStore.getState().appendMessage(m);
    useChatStore.getState().appendMessage(m);  // duplicate
    expect(useChatStore.getState().currentMessages.map((x) => x.id)).toEqual(['a']);
  });

  it('appendMessage caps history at 500', () => {
    for (let i = 0; i < 600; i++) {
      useChatStore.getState().appendMessage(makeMsg({ id: `m${i}` }));
    }
    expect(useChatStore.getState().currentMessages.length).toBe(500);
    // The most recent 100 are kept.
    expect(useChatStore.getState().currentMessages[0].id).toBe('m100');
  });

  it('toggleSidebar flips sidebarCollapsed', () => {
    expect(useChatStore.getState().sidebarCollapsed).toBe(false);
    useChatStore.getState().toggleSidebar();
    expect(useChatStore.getState().sidebarCollapsed).toBe(true);
    useChatStore.getState().toggleSidebar();
    expect(useChatStore.getState().sidebarCollapsed).toBe(false);
  });

  it('reset clears everything', () => {
    useChatStore.getState().setProjects([fakeProject]);
    useChatStore.getState().setCurrentProject(fakeProject);
    useChatStore.getState().setCurrentSessionId('s1');
    useChatStore.getState().appendMessage(makeMsg());
    useChatStore.getState().reset();
    const s = useChatStore.getState();
    expect(s.projects).toEqual([]);
    expect(s.currentProject).toBeNull();
    expect(s.currentSessionId).toBeNull();
    expect(s.currentMessages).toEqual([]);
  });
});
