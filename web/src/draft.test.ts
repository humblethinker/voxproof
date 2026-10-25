import { describe, expect, it } from 'vitest';
import { DraftController } from './draft';

describe('human confirmation boundary', () => {
  it('replaces the title but preserves one request ID on correction', () => {
    const c = new DraftController();
    const token = c.begin();
    c.update(token, 'Buy groceries');
    const id = c.draft!.request_id;
    c.update(token, 'Buy groceries and coffee');
    expect(c.confirm()).toEqual({ title: 'Buy groceries and coffee', request_id: id });
  });
  it('freezes the title and ID across double clicks and failed-save retries', () => {
    const c = new DraftController();
    const token = c.begin();
    c.update(token, 'Buy groceries');
    const first = c.confirm();
    c.update(token, 'A late correction');
    expect(c.confirm()).toEqual(first);
    expect(() => c.cancel()).toThrow();
  });
  it('rejects late calls from cancelled and previous conversations', () => {
    const c = new DraftController();
    const old = c.begin();
    c.update(old, 'Old task');
    c.cancel();
    c.update(old, 'Late task');
    expect(c.draft).toBeNull();
    const current = c.begin();
    c.update(old, 'Still late');
    c.stop(old);
    c.update(current, 'Current task');
    expect(c.draft?.title).toBe('Current task');
  });
  it('keeps a stopped draft available for confirmation', () => {
    const c = new DraftController();
    const token = c.begin();
    c.update(token, '   Buy   coffee  ');
    c.stop();
    c.update(token, 'Late task');
    expect(c.confirm().title).toBe('Buy coffee');
    c.saved();
    expect(c.draft).toBeNull();
    expect(c.sealed).toBe(false);
  });
  it('rejects invalid tool input and confirms nothing without a draft', () => {
    const c = new DraftController();
    const token = c.begin();
    for (const title of [null, 3, '', ' ', 'x'.repeat(201)]) c.update(token, title);
    expect(c.draft).toBeNull();
    expect(() => c.confirm()).toThrow();
  });
});
