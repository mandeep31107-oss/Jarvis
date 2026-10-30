import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { EmptyState, Notice, Panel, Pill, RichText, Spinner } from '../components/ui';
import { api, ApiError, streamChat, type ChatStreamEvent } from '../lib/api';
import { relativeTime } from '../lib/hooks';
import { useVoice } from '../lib/voice';
import type { Capabilities, ChatMessage, ChatMeta, Conversation, VoiceSettings } from '../lib/types';

interface LocalMessage extends Omit<ChatMessage, 'meta_json'> {
  meta?: ChatMeta;
  pending?: boolean;
}

const STAGE_LABELS: Record<string, string> = {
  received: 'Request received',
  context: 'Context retrieved',
  routed: 'Agent selected',
  generating: 'Generating answer',
  validating: 'QA validation',
  finalized: 'Finalised',
};

export default function ChatView({
  voice: voiceSettings,
  capabilities,
  onVoiceChange,
  onAfterTurn,
}: {
  voice: VoiceSettings;
  capabilities: Capabilities | null;
  onVoiceChange: (patch: Partial<VoiceSettings>) => Promise<void> | void;
  onAfterTurn: () => void;
}) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [activeAgent, setActiveAgent] = useState<{ id: string; name: string; accent: string } | null>(null);
  const [stages, setStages] = useState<Array<{ stage: string; status: string; detail: string }>>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);

  const logRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const sendRef = useRef<(text: string) => Promise<void>>(async () => {});

  const voice = useVoice({
    language: voiceSettings.language,
    rate: voiceSettings.rate,
    pitch: voiceSettings.pitch,
    wakePhrase: voiceSettings.wakePhrase,
    wakeEnabled: voiceSettings.wakeEnabled,
    onUtterance: (text) => {
      void sendRef.current(text);
    },
  });

  const loadConversations = useCallback(async () => {
    try {
      const data = await api.get<{ conversations: Conversation[] }>('/chat/conversations');
      setConversations(data.conversations);
      return data.conversations;
    } catch (err) {
      setError({ message: err instanceof Error ? err.message : 'Could not load conversations' });
      return [];
    }
  }, []);

  const loadMessages = useCallback(async (id: string) => {
    setLoadingHistory(true);
    try {
      const data = await api.get<{ conversation: Conversation; messages: ChatMessage[] }>(
        `/chat/conversations/${id}/messages`,
      );
      setMessages(
        data.messages.map((m) => ({
          ...m,
          meta: m.meta_json ? (JSON.parse(m.meta_json) as ChatMeta) : undefined,
        })),
      );
      setConversationId(id);
    } catch (err) {
      setError({ message: err instanceof Error ? err.message : 'Could not load messages' });
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      const list = await loadConversations();
      const first = list[0];
      if (first) await loadMessages(first.id);
    })();
  }, [loadConversations, loadMessages]);

  useEffect(() => {
    const node = logRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages, streamingText, stages]);

  const handleEvent = useCallback((event: ChatStreamEvent) => {
    switch (event.type) {
      case 'run':
        setStages([]);
        break;
      case 'stage':
        setStages((prev) => [...prev, event.data]);
        break;
      case 'delta':
        setStreamingText((prev) => prev + event.data.text);
        break;
      case 'validated':
        setWarnings(event.data.warnings);
        break;
      case 'done': {
        const data = event.data;
        setActiveAgent(data.agent);
        setConversationId(data.conversationId);
        setMessages((prev) => [
          ...prev.map((m) => (m.pending ? { ...m, pending: false } : m)),
          {
            id: data.messageId,
            role: 'assistant',
            content: data.text ?? '',
            agent_id: data.agent.id,
            language: data.language,
            created_at: new Date().toISOString(),
            meta: {
              runId: data.runId,
              warnings: data.warnings,
              actions: data.actions as unknown as ChatMeta['actions'],
              costUsdEstimate: data.costUsdEstimate,
              usage: data.usage,
            },
          },
        ]);
        setStreamingText('');
        if (voiceSettings.mode !== 'text') {
          const finalText = data.text ?? '';
          if (finalText) voice.speak(finalText);
        }
        break;
      }
      case 'error':
        setError({ message: event.data.message, hint: event.data.hint });
        break;
      default:
        break;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voiceSettings.mode]);

  const send = useCallback(
    async (rawText: string) => {
      const text = rawText.trim();
      if (!text || busy) return;
      setError(null);
      setWarnings([]);
      setInput('');
      setBusy(true);
      setStreamingText('');
      setStages([]);
      voice.interrupt();

      const tempMessage: LocalMessage = {
        id: `local-${Date.now()}`,
        role: 'user',
        content: text,
        agent_id: null,
        language: null,
        created_at: new Date().toISOString(),
        pending: true,
      };
      setMessages((prev) => [...prev, tempMessage]);

      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await streamChat({
          conversationId,
          text,
          signal: controller.signal,
          onEvent: handleEvent,
        });
        const list = await loadConversations();
        if (!conversationId && list[0]) setConversationId(list[0].id);
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          setMessages((prev) => prev.filter((m) => m.id !== tempMessage.id || !m.pending));
        } else {
          const message = err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'Failed';
          setError({ message });
          setMessages((prev) => prev.filter((m) => m.id !== tempMessage.id));
        }
      } finally {
        setBusy(false);
        abortRef.current = null;
        onAfterTurn();
      }
    },
    [busy, conversationId, handleEvent, loadConversations, onAfterTurn, voice],
  );

  sendRef.current = send;

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setBusy(false);
    setStreamingText('');
  }, []);

  const newConversation = useCallback(async () => {
    try {
      const data = await api.post<{ conversation: Conversation }>('/chat/conversations', {
        title: 'New conversation',
      });
      setConversations((prev) => [data.conversation, ...prev]);
      setMessages([]);
      setConversationId(data.conversation.id);
      setActiveAgent(null);
      setWarnings([]);
      setError(null);
      textareaRef.current?.focus();
    } catch (err) {
      setError({ message: err instanceof Error ? err.message : 'Could not start a conversation' });
    }
  }, []);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    void send(input);
  };

  const comboHint = useMemo(() => {
    if (!voice.supported) return 'Voice input is unavailable in this browser. Chrome or Edge is required.';
    if (voiceSettings.privacyMode) {
      return 'Microphone is off until you press the orb. Nothing is recorded or uploaded by J.A.R.V.I.S.';
    }
    return 'Microphone may stay on for the wake phrase while this tab is in the foreground.';
  }, [voice.supported, voiceSettings.privacyMode]);

  return (
    <div className="chat">
      <div className="chat__main panel">
        <div className="panel__head">
          <div>
            <h2 className="panel__title">Conversation</h2>
            <p className="panel__subtitle">
              {conversationId ? `${messages.length} message(s) in this thread` : 'A new thread starts when you send a message'}
            </p>
          </div>
          <div className="panel__actions">
            <select
              value={conversationId ?? ''}
              onChange={(e) => {
                if (e.target.value) void loadMessages(e.target.value);
              }}
              style={{ maxWidth: 220 }}
              aria-label="Conversation history"
            >
              <option value="">— history —</option>
              {conversations.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title.slice(0, 40)}
                </option>
              ))}
            </select>
            <button className="btn btn--subtle btn--sm" onClick={() => void newConversation()}>
              New
            </button>
          </div>
        </div>

        <div className="chat__log" ref={logRef} aria-live="polite">
          {loadingHistory && <Spinner label="Loading conversation…" />}
          {!loadingHistory && messages.length === 0 && !streamingText && (
            <EmptyState
              title="Say something, or type"
              body="Try: “Jarvis, I want to start an AI company but I don't know where to begin.” Every reply is routed to a specialist agent and checked before you see it."
            />
          )}

          {messages.map((message) => (
            <MessageBubble key={message.id} message={message} />
          ))}

          {busy && (
            <div className="bubble bubble--assistant">
              <div className="bubble__avatar" aria-hidden="true">J</div>
              <div className="bubble__content">
                <div className="bubble__meta">
                  <span>{activeAgent?.name ?? 'Routing'}</span>
                  <span>{busy ? 'working' : ''}</span>
                </div>
                <div className="bubble__body">
                  {streamingText ? (
                    <RichText text={streamingText} />
                  ) : (
                    <Spinner label={stages[stages.length - 1]?.detail ?? 'Thinking…'} />
                  )}
                  {stages.length > 0 && (
                    <div className="tiny muted-2" style={{ marginTop: 10 }}>
                      {stages.map((s) => `${s.stage}: ${s.detail}`).join(' · ')}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

        {error && (
          <div style={{ padding: '0 16px' }}>
            <Notice tone="red" title="This turn did not complete" onDismiss={() => setError(null)}>
              {error.message}
              {error.hint ? <div className="tiny" style={{ marginTop: 6 }}>{error.hint}</div> : null}
            </Notice>
          </div>
        )}
        {warnings.length > 0 && !busy && (
          <div style={{ padding: '0 16px' }}>
            <Notice tone="amber" title="QA notes on the last answer">
              <ul className="stack" style={{ gap: 3 }}>
                {warnings.map((w) => (
                  <li key={w}>• {w}</li>
                ))}
              </ul>
            </Notice>
          </div>
        )}

        <form className="composer" onSubmit={submit}>
          <div className="composer__row">
            <button
              type="button"
              className={`orb ${voice.listening || voice.wakeListening ? 'orb--active' : ''}`}
              onClick={() => {
                if (voice.wakeListening) {
                  voice.stopWakeListening();
                  return;
                }
                if (voice.listening) {
                  voice.stopListening();
                  return;
                }
                voice.interrupt();
                voice.startListening();
              }}
              aria-label={voice.listening ? 'Stop listening' : 'Start voice input'}
              title={voice.listening ? 'Stop listening' : 'Start voice input'}
            >
              <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
                <rect x="9" y="3" width="6" height="11" rx="3" />
                <path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" />
              </svg>
            </button>

            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void send(input);
                }
              }}
              placeholder="Ask anything. Enter to send, Shift+Enter for a new line."
              rows={2}
              aria-label="Message"
            />

            {busy ? (
              <button type="button" className="btn btn--danger btn--md" onClick={stop}>
                Stop
              </button>
            ) : (
              <button type="submit" className="btn btn--primary btn--md" disabled={!input.trim()}>
                Send
              </button>
            )}
          </div>

          <div className="composer__hint">
            <span>
              {voice.listening ? '🎙 Listening…' : voice.wakeListening ? `🎙 Standby for “${voiceSettings.wakePhrase}”` : comboHint}
            </span>
            {voice.interim && <span className="muted">“{voice.interim}”</span>}
            {voice.speaking && (
              <button type="button" className="btn btn--subtle btn--sm" onClick={voice.interrupt}>
                Interrupt speech
              </button>
            )}
            <span style={{ marginLeft: 'auto' }} className="row">
              <button
                type="button"
                className="btn btn--subtle btn--sm"
                onClick={() => {
                  if (voice.wakeListening) voice.stopWakeListening();
                  else voice.startWakeListening();
                }}
                disabled={!voiceSettings.wakeEnabled}
                title={voiceSettings.wakeEnabled ? 'Listen for the wake phrase' : 'Enable the wake phrase in Settings first'}
              >
                {voice.wakeListening ? 'Wake: on' : 'Wake: off'}
              </button>
              <button
                type="button"
                className="btn btn--subtle btn--sm"
                onClick={() => void onVoiceChange({ mode: voiceSettings.mode === 'text' ? 'both' : 'text' })}
                title="Toggle spoken replies"
              >
                {voiceSettings.mode === 'text' ? '🔇 Voice off' : '🔊 Voice on'}
              </button>
            </span>
          </div>

          {voice.error && (
            <Notice tone="amber" onDismiss={voice.clearError}>
              {voice.error}
            </Notice>
          )}
          <div className="tiny muted-2">
            Voice is a convenience, not an identity check. Speaker verification is not implemented, so
            protected actions always require approval in the Approval centre.
          </div>
        </form>
      </div>

      <div className="stack stack--lg">
        <Panel title="Active agent" subtitle="Selected by keyword routing">
          {activeAgent ? (
            <>
              <div className="row">
                <span
                  className="agent-card__badge"
                  style={{ background: activeAgent.accent, color: activeAgent.accent }}
                  aria-hidden="true"
                />
                <strong>{activeAgent.name}</strong>
              </div>
              <p className="small muted" style={{ marginTop: 8 }}>
                {capabilities?.provider.description ?? 'Provider status unavailable'}
              </p>
            </>
          ) : (
            <p className="small muted">No turn yet in this view. The agent appears once you send a message.</p>
          )}
        </Panel>

        <Panel title="Voice controls" subtitle="Saved to your profile">
          <div className="stack">
            <label className="field">
              <span className="field__label">Language</span>
              <select
                value={voiceSettings.language}
                onChange={(e) => void onVoiceChange({ language: e.target.value as VoiceSettings['language'] })}
              >
                <option value="en-IN">English (India)</option>
                <option value="en-GB">English (UK)</option>
                <option value="en-US">English (US)</option>
                <option value="hi-IN">हिन्दी (India)</option>
              </select>
            </label>
            <label className="field">
              <span className="field__label">Speaking rate — {voiceSettings.rate.toFixed(2)}×</span>
              <input
                type="range"
                min={0.6}
                max={1.6}
                step={0.05}
                value={voiceSettings.rate}
                onChange={(e) => void onVoiceChange({ rate: Number(e.target.value) })}
              />
            </label>
            <label className="field">
              <span className="field__label">Pitch — {voiceSettings.pitch.toFixed(2)}</span>
              <input
                type="range"
                min={0.6}
                max={1.5}
                step={0.05}
                value={voiceSettings.pitch}
                onChange={(e) => void onVoiceChange({ pitch: Number(e.target.value) })}
              />
            </label>
            <label className="field">
              <span className="field__label">Wake phrase</span>
              <input
                value={voiceSettings.wakePhrase}
                onChange={(e) => void onVoiceChange({ wakePhrase: e.target.value })}
                maxLength={40}
              />
              <span className="field__hint">
                Enable it in Settings. Keyword spotting only — anyone nearby can say it.
              </span>
            </label>
          </div>
        </Panel>

        <Panel title="Honest limits">
          <ul className="stack small muted" style={{ gap: 6 }}>
            <li>• Replies come from your configured model. {capabilities?.provider.id === 'offline' ? 'Right now that is the offline template engine.' : ''}</li>
            <li>• No messaging platform is connected.</li>
            <li>• No internet search API is connected; research needs URLs you supply.</li>
            <li>• Nothing can move money.</li>
            <li>• External actions always wait for your approval.</li>
          </ul>
        </Panel>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: LocalMessage }) {
  const isUser = message.role === 'user';
  const meta = message.meta;
  return (
    <div className={`bubble ${isUser ? 'bubble--user' : 'bubble--assistant'}`}>
      <div className="bubble__avatar" aria-hidden="true">
        {isUser ? 'You' : 'J'}
      </div>
      <div className="bubble__content">
        <div className="bubble__meta">
          <span>{isUser ? 'You' : message.agent_id ? message.agent_id.replace(/-/g, ' ') : 'J.A.R.V.I.S.'}</span>
          <span>{relativeTime(message.created_at)}</span>
          {message.language && message.language !== 'en' && <Pill tone="violet">{message.language}</Pill>}
          {message.pending && <Pill tone="amber">sending</Pill>}
        </div>
        <div className="bubble__body">
          {isUser ? <p style={{ whiteSpace: 'pre-wrap' }}>{message.content}</p> : <RichText text={message.content} />}
        </div>

        {meta?.actions && meta.actions.length > 0 && (
          <div className="bubble__actions">
            {meta.actions.map((action, i) => (
              <div key={`${action.actionType}-${i}`} className="row small">
                <Pill
                  tone={
                    action.status === 'pending'
                      ? 'amber'
                      : action.status === 'failed'
                        ? 'red'
                        : action.status === 'rejected'
                          ? 'muted'
                          : 'green'
                  }
                >
                  {action.status.replace(/_/g, ' ')}
                </Pill>
                <span className="muted">{action.summary}</span>
              </div>
            ))}
          </div>
        )}

        {meta?.warnings && meta.warnings.length > 0 && (
          <div className="tiny" style={{ color: 'var(--amber)', marginTop: 6 }}>
            QA: {meta.warnings.join(' · ')}
          </div>
        )}

        {meta?.costUsdEstimate !== undefined && (
          <div className="tiny muted-2" style={{ marginTop: 6 }}>
            {meta.provider ? `${meta.provider}/${meta.model ?? ''} · ` : ''}
            {meta.latencyMs ? `${meta.latencyMs} ms · ` : ''}
            est. ${(meta.costUsdEstimate * 1000).toFixed(4)} ¢ ·{' '}
            {meta.usage ? `${meta.usage.promptTokens ?? 0}+${meta.usage.completionTokens ?? 0} tokens` : ''}
          </div>
        )}
      </div>
    </div>
  );
}
