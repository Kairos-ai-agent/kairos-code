/**
 * R3 — the Artifacts page.
 *
 * A produced thing is only worth keeping if a person can read it and answer it.
 * These tests pin the three ways that can break: the list does not load, the
 * detail does not show the body of what was produced, or a comment goes nowhere.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConfigProvider, App as AntdApp } from 'antd';
import { MemoryRouter } from 'react-router-dom';

const get = vi.fn();
const post = vi.fn();
vi.mock('../api/client', () => ({
  default: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
  },
}));

import Artifacts from '../pages/Artifacts';

const renderAt = (path: string) => render(
  <ConfigProvider>
    <AntdApp>
      <MemoryRouter initialEntries={[path]}>
        <Artifacts />
      </MemoryRouter>
    </AntdApp>
  </ConfigProvider>,
);

const plan = {
  id: 'a1', project_id: 'p1', kind: 'plan', title: 'Round 1 plan',
  body: '- step one\n- step two', round_no: 1, created_at: 100,
  meta: { chars: 20 },
};

const shot = {
  id: 'a2', project_id: 'p1', kind: 'screenshot', title: 'Screenshot',
  path: 'E:/tmp/data/shots/shot-1.png', round_no: 1, created_at: 200, meta: {},
};

const routes = (artifacts: unknown[], comments: unknown[] = []) => (url: string) => {
  if (url === '/projects') return Promise.resolve({ data: [{ id: 'p1', name: 'demo' }] });
  if (url.includes('/comments')) return Promise.resolve({ data: { comments } });
  if (url.includes('/artifacts')) return Promise.resolve({
    data: { project_id: 'p1', count: (artifacts as unknown[]).length, artifacts },
  });
  return Promise.resolve({ data: {} });
};

describe('Artifacts page (R3)', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it('renders without a project and does not fetch artifacts', async () => {
    get.mockResolvedValue({ data: [] });
    renderAt('/artifacts');

    await waitFor(() => expect(get).toHaveBeenCalledWith('/projects'));
    expect(screen.getByTestId('artifacts-page')).toBeInTheDocument();
    expect(get).not.toHaveBeenCalledWith(expect.stringContaining('/artifacts'));
  });

  it('lists what the project produced', async () => {
    get.mockImplementation(routes([plan, shot]));
    renderAt('/artifacts?project=p1');

    await waitFor(() => expect(screen.getByTestId('artifact-row-a1')).toBeInTheDocument());
    expect(screen.getByTestId('artifact-row-a2')).toBeInTheDocument();
    expect(screen.getByText('Round 1 plan')).toBeInTheDocument();
  });

  it('shows the body of the selected artifact', async () => {
    get.mockImplementation(routes([plan]));
    renderAt('/artifacts?project=p1');

    await waitFor(() => expect(screen.getByTestId('artifact-row-a1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('artifact-row-a1'));

    await waitFor(() => expect(screen.getByTestId('artifact-body')).toBeInTheDocument());
    expect(screen.getByTestId('artifact-body')).toHaveTextContent('- step one');
  });

  it('serves a screenshot through the file endpoint', async () => {
    get.mockImplementation(routes([shot]));
    renderAt('/artifacts?project=p1');

    await waitFor(() => expect(screen.getByTestId('artifact-row-a2')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('artifact-row-a2'));

    await waitFor(() => expect(screen.getByTestId('artifact-image')).toBeInTheDocument());
    expect(screen.getByTestId('artifact-image')).toHaveAttribute(
      'src', '/api/artifacts/a2/file');
  });

  it('asks the API for one kind when the filter changes', async () => {
    get.mockImplementation(routes([plan]));
    renderAt('/artifacts?project=p1');

    await waitFor(() => expect(get).toHaveBeenCalledWith(
      '/projects/p1/artifacts'));

    // antd renders the options in a portal, so the trigger has to be the select
    // itself and the option is found where antd actually puts it. Labels are
    // translated, so match on text and take the option node, not the tag.
    const select = screen.getByTestId('artifacts-kind-select');
    fireEvent.mouseDown(select.querySelector('.ant-select-selector') as Element);
    await waitFor(() => {
      const options = Array.from(document.querySelectorAll('.ant-select-item-option'));
      expect(options.length).toBeGreaterThan(0);
    });
    const planOption = Array.from(document.querySelectorAll('.ant-select-item-option'))
      .find((el) => (el.textContent || '').trim().toLowerCase() === 'plan');
    expect(planOption).toBeTruthy();
    fireEvent.click(planOption as Element);

    await waitFor(() => expect(get).toHaveBeenCalledWith(
      '/projects/p1/artifacts?kind=plan'));
  });

  it('posts a comment and shows the thread that comes back', async () => {
    get.mockImplementation((url: string) => {
      if (url === '/projects') return Promise.resolve({ data: [{ id: 'p1', name: 'demo' }] });
      if (url.includes('/comments')) return Promise.resolve({
        data: { comments: [{ id: 1, author: 'user', body: 'why this order?', created_at: 1 }] },
      });
      return Promise.resolve({ data: { artifacts: [plan] } });
    });
    post.mockResolvedValue({ data: { id: 1 } });
    renderAt('/artifacts?project=p1');

    await waitFor(() => expect(screen.getByTestId('artifact-row-a1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('artifact-row-a1'));
    await waitFor(() => expect(screen.getByTestId('artifact-comment-input')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('artifact-comment-input'),
                     { target: { value: 'why this order?' } });
    fireEvent.click(screen.getByTestId('artifact-comment-send'));

    await waitFor(() => expect(post).toHaveBeenCalledWith(
      '/artifacts/a1/comments', { body: 'why this order?' }));
    await waitFor(() => expect(screen.getByTestId('comment-1')).toBeInTheDocument());
  });
});
