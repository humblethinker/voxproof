// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { HookOptions } from '@elevenlabs/react';
import { App } from './App';

const sdk = vi.hoisted(() => ({ startSession: vi.fn(), endSession: vi.fn() }));
vi.mock('@elevenlabs/react', () => ({ useConversation: () => ({ ...sdk, status: 'disconnected', isSpeaking: false }) }));
const savedTask = { id: 'task-1', title: 'Buy groceries and coffee', created_at: '2026-10-24T00:00:00Z', completed: false };
let posts: Array<{ title: string; request_id: string }>;
let failSave: boolean;

beforeEach(() => {
  posts = []; failSave = false; vi.clearAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body as string); posts.push(body);
      if (failSave) throw new Error('Lost response after server commit');
      return { ok: true, json: async () => ({ ...savedTask, ...body }) };
    }
    return { ok: true, json: async () => url.includes('voice-session') ? { signed_url: 'wss://example.test' } : [] };
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function startDraft() {
  render(<App />);
  await waitFor(() => expect((screen.getByRole('button', { name: /Start talking/ }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: /Start talking/ }));
  await waitFor(() => expect(sdk.startSession).toHaveBeenCalledOnce());
  const session = sdk.startSession.mock.calls[0][0] as HookOptions;
  act(() => {
    session.clientTools!.set_task_draft({ title: 'Buy groceries' });
    session.clientTools!.set_task_draft({ title: 'Buy groceries and coffee' });
  });
  return session;
}

it('only saves after Confirm, freezes corrections, and ignores a double click', async () => {
  const session = await startDraft();
  expect(posts).toHaveLength(0);
  const confirm = screen.getByRole('button', { name: 'Confirm & save' });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  act(() => { session.clientTools!.set_task_draft({ title: 'Too late' }); });
  await screen.findByText('Saved. One less thing to remember.');
  expect(posts).toHaveLength(1);
  expect(posts[0].title).toBe('Buy groceries and coffee');
  expect(sdk.endSession).toHaveBeenCalledOnce();
  expect(screen.getAllByRole('listitem')).toHaveLength(1);
});

it('cancels without a POST and ignores a late tool call', async () => {
  const session = await startDraft();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  act(() => { session.clientTools!.set_task_draft({ title: 'Too late' }); });
  expect(screen.queryByRole('button', { name: 'Confirm & save' })).toBeNull();
  expect(posts).toHaveLength(0);
  expect(sdk.endSession).toHaveBeenCalledOnce();
});

it('retries an ambiguous failed save with exactly the same title and request ID', async () => {
  await startDraft(); failSave = true;
  fireEvent.click(screen.getByRole('button', { name: 'Confirm & save' }));
  await screen.findByRole('alert');
  expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(true);
  failSave = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry save' }));
  await screen.findByText('Saved. One less thing to remember.');
  expect(posts).toHaveLength(2);
  expect(posts[1]).toEqual(posts[0]);
  expect(screen.getAllByRole('listitem')).toHaveLength(1);
});

it('completes, reloads and undoes the same task without creating a new task', async () => {
  let stored = { ...savedTask };
  const updates: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === 'PATCH') {
      const change = JSON.parse(options.body as string);
      updates.push(change); stored = { ...stored, ...change };
      return { ok: true, json: async () => ({ ...stored }) };
    }
    return { ok: true, json: async () => [{ ...stored }] };
  }));
  const first = render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: `Complete: ${savedTask.title}` }));
  await screen.findByRole('button', { name: `Undo completion: ${savedTask.title}`, pressed: true });
  first.unmount(); render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: `Undo completion: ${savedTask.title}` }));
  await screen.findByRole('button', { name: `Complete: ${savedTask.title}`, pressed: false });
  expect(updates).toEqual([{ completed: true }, { completed: false }]);
  expect(screen.getAllByRole('listitem')).toHaveLength(1);
  expect(posts).toHaveLength(0);
});

it('locks an in-flight completion and safely retries a lost response', async () => {
  const updates: unknown[] = [];
  let rejectRequest: (reason: Error) => void = () => {};
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method !== 'PATCH') return { ok: true, json: async () => [savedTask] };
    const change = JSON.parse(options.body as string); updates.push(change);
    if (updates.length === 1) return new Promise((_resolve, reject) => { rejectRequest = reject; });
    return { ok: true, json: async () => ({ ...savedTask, ...change }) };
  }));
  render(<App />);
  const complete = await screen.findByRole('button', { name: `Complete: ${savedTask.title}` });
  fireEvent.click(complete); fireEvent.click(complete);
  expect((complete as HTMLButtonElement).disabled).toBe(true);
  expect(updates).toHaveLength(1);
  await act(async () => { rejectRequest(new Error('Response lost after commit')); });
  await screen.findByRole('alert');
  expect(complete.getAttribute('aria-pressed')).toBe('false');
  fireEvent.click(complete);
  await screen.findByRole('button', { name: `Undo completion: ${savedTask.title}`, pressed: true });
  expect(updates).toEqual([{ completed: true }, { completed: true }]);
  expect(screen.queryByRole('alert')).toBeNull();
});
