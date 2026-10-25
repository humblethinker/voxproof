import { useEffect, useRef, useState } from 'react';
import { useConversation } from '@elevenlabs/react';
import { DraftController } from './draft';
import './style.css';

type Task = { id: string; title: string; created_at: string; request_id: string };
type Message = { role: string; text: string };

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/${path}`, {
    method: body ? 'POST' : 'GET',
    headers: { 'X-VoiceTasks': '1', ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(20000),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : 'The request failed. Please try again.');
  }
  return response.json();
}
export function App() {
  const controller = useRef(new DraftController()).current;
  const [, render] = useState(0);
  const redraw = () => render(n => n + 1);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [starting, setStarting] = useState(false);
  const [saving, setSaving] = useState(false);
  const inFlight = useRef(false);
  const attempt = useRef(0);
  const conversation = useConversation();
  const live = starting || conversation.status === 'connecting' || conversation.status === 'connected';

  useEffect(() => {
    let cancelled = false;
    api<Task[]>('tasks').then(data => {
      if (!cancelled) { setTasks(data); setLoaded(true); }
    }).catch(() => { if (!cancelled) setError('Could not load tasks. Check that the backend is running, then refresh.'); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const preventLoss = (event: BeforeUnloadEvent) => {
      if (controller.sealed) event.preventDefault();
    };
    window.addEventListener('beforeunload', preventLoss);
    return () => window.removeEventListener('beforeunload', preventLoss);
  }, [controller]);

  function stop() {
    attempt.current++;
    controller.stop();
    conversation.endSession();
    setStarting(false);
  }

  async function start() {
    if (live || controller.draft) return;
    const current = ++attempt.current;
    const token = controller.begin();
    setStarting(true); setError(''); setNotice(''); setMessages([]);
    try {
      const { signed_url } = await api<{ signed_url: string }>('voice-session');
      if (attempt.current !== current) return;
      conversation.startSession({
        signedUrl: signed_url, connectionType: 'websocket', stripAudioTags: true,
        clientTools: {
          set_task_draft: ({ title }) => {
            const result = controller.update(token, title);
            redraw();
            return result;
          },
        },
        onConnect: () => { if (attempt.current === current) setStarting(false); },
        onDisconnect: () => {
          controller.stop(token);
          if (attempt.current === current) setStarting(false);
        },
        onError: () => {
          controller.stop(token);
          if (attempt.current === current) {
            setStarting(false);
            setError('Voice connection failed. Allow microphone access and check your connection, then try again.');
          }
        },
        onMessage: message => {
          if (attempt.current === current)
            setMessages(previous => [...previous.slice(-5), { role: message.role, text: message.message }]);
        },
      });
    } catch (reason) {
      if (attempt.current !== current) return;
      controller.stop(token); setStarting(false);
      setError(reason instanceof Error ? reason.message : 'Could not start voice.');
    }
  }

  async function confirm() {
    if (inFlight.current || !controller.draft) return;
    inFlight.current = true;
    const frozen = controller.confirm();
    stop(); setSaving(true); setError(''); redraw();
    try {
      const task = await api<Task>('tasks', frozen);
      setTasks(previous => [task, ...previous.filter(item => item.id !== task.id)]);
      controller.saved(); setNotice('Saved. One less thing to remember.');
    } catch {
      setError('Save could not be confirmed. Retry below; the same request cannot create a duplicate. Keep this tab open until it succeeds.');
    } finally {
      inFlight.current = false; setSaving(false); redraw();
    }
  }

  function cancel() {
    if (controller.sealed) return;
    stop(); controller.cancel(); redraw(); setError(''); setNotice('Draft discarded. Nothing saved.');
  }

  const status = starting || conversation.status === 'connecting' ? 'Connecting…'
    : conversation.status === 'connected' ? (conversation.isSpeaking ? 'Speaking · you can interrupt' : 'Listening to you') : 'Ready when you are';

  return <main>
    <header><a className="brand" href="/" aria-label="VoiceTasks home"><span className="brand-icon">≋</span> VoiceTasks</a><span className="local-badge">A little space for your to-dos</span></header>
    <section className="intro"><p className="eyebrow">THINK OUT LOUD. GET THINGS DONE.</p><h1>Get it off your mind.</h1><p>Say it in your own words. Change it as you go.<br />Confirm when you’re ready.</p></section>
    <section className="workspace" aria-label="Voice task capture">
      <div className="conversation-panel">
        <div className={`voice-mark ${live ? 'active' : ''}`} aria-hidden="true"><i /><i /><i /><i /><i /></div>
        <p className="voice-status" role="status">{status}</p>
        {live ? <button className="primary" onClick={stop}>Stop conversation</button>
          : <button className="primary" onClick={start} disabled={!!controller.draft || !loaded}>Start talking <span aria-hidden="true">↗</span></button>}
        <p className="hint">Try “Add buy groceries.”<br />Then “Actually, groceries and coffee.”</p>
        <div className="transcript" aria-label="Conversation transcript" aria-live="polite">
          {messages.map((message, index) => <p key={index}><span>{message.role === 'user' ? 'You' : 'VoiceTasks'}</span>{message.text}</p>)}
        </div>
      </div>
      <div className="draft-panel">
        <div className="section-label"><span>YOUR DRAFT</span><span className="draft-badge">{controller.sealed ? 'Save pending' : 'Not saved yet'}</span></div>
        {controller.draft ? <>
          <h2 className="draft-title">{controller.draft.title}</h2>
          <p className="draft-help">{controller.sealed ? 'The title is locked while we confirm the save.' : live ? 'Say a correction to update this draft, or confirm it below.' : 'Review this title, then confirm or discard it.'}</p>
          <div className="actions"><button className="primary" onClick={confirm} disabled={saving}>{saving ? 'Saving…' : controller.sealed ? 'Retry save' : 'Confirm & save'}</button><button className="secondary" onClick={cancel} disabled={controller.sealed}>Cancel</button></div>
        </> : <div className="draft-empty"><span aria-hidden="true">✎</span><h2>Your next task starts here.</h2><p>Once you say it, a draft will appear.<br />Nothing is saved until you confirm.</p></div>}
      </div>
    </section>
    {error && <p className="alert" role="alert">{error}</p>}
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="saved"><div className="saved-heading"><h2>Saved tasks <span>{tasks.length}</span></h2><p>Kept on this computer.</p></div>
      {!loaded ? <p className="empty-list">Loading your tasks…</p> : tasks.length === 0 ? <p className="empty-list">A clear list. Start with one thing.</p> : <ul>{tasks.map(task => <li key={task.id}><span className="task-dot" aria-hidden="true" />{task.title}<time dateTime={task.created_at}>{new Date(task.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</time></li>)}</ul>}
    </section>
    <footer>Microphone audio goes to ElevenLabs during a conversation. Tasks stay in your local database.</footer>
  </main>;
}
