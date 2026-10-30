export class ApiError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(status: number, payload: unknown) {
    super(
      (payload as { message?: string; error?: string } | null)?.message ??
        (payload as { error?: string } | null)?.error ??
        `Request failed (${status})`,
    );
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

const BASE_HEADERS: Record<string, string> = {
  'content-type': 'application/json',
  // Required by the CSRF guard for state-changing requests.
  'x-jarvis-request': '1',
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    credentials: 'same-origin',
    headers: { ...BASE_HEADERS, ...(init.headers ?? {}) },
  });
  const text = await res.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { error: text.slice(0, 300) };
    }
  }
  if (!res.ok) throw new ApiError(res.status, payload);
  return payload as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? '{}' : JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) => request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

export type ChatStreamEvent =
  | { type: 'run'; data: { runId: string; conversationId: string; stages: string[] } }
  | { type: 'stage'; data: { stage: string; status: string; detail: string } }
  | { type: 'delta'; data: { text: string } }
  | { type: 'validated'; data: { warnings: string[]; corrections: string[]; redactions: number } }
  | { type: 'action'; data: Record<string, unknown> }
  | {
      type: 'done';
      data: {
        runId: string;
        conversationId: string;
        messageId: string;
        status: string;
        agent: { id: string; name: string; accent: string };
        language: string;
        warnings: string[];
        usage: { promptTokens?: number; completionTokens?: number };
        costUsdEstimate: number;
        actions: Array<Record<string, unknown>>;
        pendingApprovals: number;
        text?: string;
      };
    }
  | { type: 'error'; data: { code: string; message: string; hint?: string; runId?: string } };

/** Streams a chat turn over Server-Sent Events using fetch + ReadableStream. */
export async function streamChat(opts: {
  conversationId?: string | null;
  text: string;
  signal?: AbortSignal;
  onEvent: (event: ChatStreamEvent) => void;
}): Promise<void> {
  const res = await fetch('/api/chat/message', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { ...BASE_HEADERS, accept: 'text/event-stream' },
    body: JSON.stringify({ conversationId: opts.conversationId ?? null, text: opts.text }),
    signal: opts.signal,
  });

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '');
    let payload: unknown = null;
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { error: text.slice(0, 300) };
    }
    throw new ApiError(res.status, payload);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const flush = (block: string) => {
    let eventName = 'message';
    const dataLines: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length === 0) return;
    let parsed: unknown = null;
    try {
      parsed = JSON.parse(dataLines.join('\n'));
    } catch {
      return;
    }
    opts.onEvent({ type: eventName, data: parsed } as ChatStreamEvent);
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx = buffer.indexOf('\n\n');
    while (idx !== -1) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (block.trim()) flush(block);
      idx = buffer.indexOf('\n\n');
    }
  }
  if (buffer.trim()) flush(buffer);
}

/** Minimal SSE subscription used by the workflow monitor. */
export function subscribeRunEvents(
  onEvent: (event: { type: string; data: unknown }) => void,
): () => void {
  const controller = new AbortController();
  (async () => {
    try {
      const res = await fetch('/api/runs/stream', {
        credentials: 'same-origin',
        headers: { accept: 'text/event-stream' },
        signal: controller.signal,
      });
      if (!res.ok || !res.body) return;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx = buffer.indexOf('\n\n');
        while (idx !== -1) {
          const block = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          let eventName = 'message';
          const dataLines: string[] = [];
          for (const line of block.split('\n')) {
            if (line.startsWith('event:')) eventName = line.slice(6).trim();
            else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
          }
          if (dataLines.length > 0) {
            try {
              onEvent({ type: eventName, data: JSON.parse(dataLines.join('\n')) });
            } catch {
              /* ignore malformed frame */
            }
          }
          idx = buffer.indexOf('\n\n');
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        // The stream is best-effort; the polling fallback keeps the UI accurate.
      }
    }
  })();
  return () => controller.abort();
}
