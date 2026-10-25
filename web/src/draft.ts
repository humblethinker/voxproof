export type Draft = { title: string; request_id: string };

// The agent can replace a draft. Only the human can seal it for persistence.
export class DraftController {
  draft: Draft | null = null;
  sealed = false;
  private generation = 0;
  private accepting = false;

  begin() {
    if (this.draft) throw new Error('Confirm or cancel the current draft first.');
    this.accepting = true;
    return ++this.generation;
  }

  update(token: number, title: unknown): string {
    if (token !== this.generation || !this.accepting || this.sealed)
      return 'Draft is closed. Nothing changed. Do not claim it was saved.';
    if (typeof title !== 'string') return 'Please provide a text title.';
    title = title.trim().replace(/\s+/g, ' ');
    if (!(title as string).length || (title as string).length > 200)
      return 'Use a title between 1 and 200 characters.';
    this.draft = { title: title as string, request_id: this.draft?.request_id ?? crypto.randomUUID() };
    return 'Draft updated. It is NOT saved. The user must click Confirm in the app.';
  }

  stop(token?: number) {
    if (token === undefined || token === this.generation) this.accepting = false;
  }

  confirm(): Draft {
    if (!this.draft) throw new Error('There is no draft to save.');
    this.stop();
    this.sealed = true;
    return { ...this.draft };
  }

  cancel() {
    if (this.sealed) throw new Error('Retry the pending save to resolve its outcome.');
    this.stop();
    this.draft = null;
  }

  saved() {
    this.draft = null;
    this.sealed = false;
  }
}
