/** End-to-end test for the chat store handling a folder-created project. */
import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '../stores/chatStore';
import type { Project } from '../types';

const folderProject: Project = {
  id: 'p-folder',
  name: 'my-app',
  description: 'D:\\projects\\my-app',
  workspace: 'D:\\projects\\my-app',
  work_dir: 'D:\\projects\\my-app',
  status: 'active',
  task_count: 0, agent_count: 0, created_at: 1.0,
};

describe('chat store + folder project', () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  it('setProjects + setCurrentProject exposes the folder project', () => {
    const s = useChatStore.getState();
    s.setProjects([folderProject]);
    s.setCurrentProject(folderProject);
    expect(useChatStore.getState().currentProject?.id).toBe('p-folder');
    expect(useChatStore.getState().currentProject?.work_dir)
      .toBe('D:\\projects\\my-app');
  });

  it('switching from a folder to a real project clears the thread', () => {
    const s = useChatStore.getState();
    s.setProjects([folderProject]);
    s.setCurrentProject(folderProject);
    s.appendMessage({
      id: 'm1', sender: 'user', receiver: 'agent', topic: '',
      content: 'hi', msg_type: 'text', timestamp: 1.0, metadata: {},
    });
    const realProject: Project = { ...folderProject, id: 'p-real', name: 'real' };
    s.setCurrentProject(realProject);
    expect(useChatStore.getState().currentMessages).toEqual([]);
  });
});
