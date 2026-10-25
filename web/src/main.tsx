import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { ConversationProvider } from '@elevenlabs/react';
import { App } from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode><ConversationProvider><App /></ConversationProvider></StrictMode>,
);
