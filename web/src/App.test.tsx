// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { HookOptions } from '@elevenlabs/react';
import { App } from './App';

const sdk = vi.hoisted(() => ({ startSession: vi.fn(), endSession: vi.fn() }));
vi.mock('@elevenlabs/react', () => ({ useConversation: () => ({ ...sdk, status: 'disconnected', isSpeaking: false }) }));
const savedTask = { id: 'task-1', title: 'Buy groceries and coffee', created_at: '2026-10-24T00:00:00Z' };
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
