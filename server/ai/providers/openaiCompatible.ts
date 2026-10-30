import {
  AiError,
  type AiProvider,
  type CompletionRequest,
  type CompletionResult,
  type StreamHandlers,
} from '../types.js';

interface ProviderOptions {
  id: string;
  baseUrl: string;
  model: string;
  apiKey: string | null;
  timeoutMs: number;
  maxOutputTokens: number;
}

interface ChatCompletionResponse {
  choices?: Array<{
    message?: { content?: string | null };
    delta?: { content?: string | null };
    finish_reason?: string | null;
  }>;
  usage?: { prompt_tokens?: number; completion_tokens?: number };
}

/**
 * Works with any OpenAI-compatible /chat/completions endpoint: OpenAI, Groq,
 * Together, OpenRouter, vLLM, LM Studio, and Ollama's compatibility API.
 */
export class OpenAiCompatibleProvider implements AiProvider {
  readonly id: string;
  readonly model: string;
  private readonly baseUrl: string;
  private readonly apiKey: string | null;
  private readonly timeoutMs: number;
  private readonly maxOutputTokens: number;

  constructor(opts: ProviderOptions) {
    this.id = opts.id;
    this.baseUrl = opts.baseUrl;
    this.model = opts.model;
    this.apiKey = opts.apiKey;
    this.timeoutMs = opts.timeoutMs;
    this.maxOutputTokens = opts.maxOutputTokens;
  }

  isConfigured(): boolean {
    return this.apiKey !== null || this.id === 'ollama';
  }

  describe(): string {
    if (!this.isConfigured()) return `${this.id}: missing AI_API_KEY`;
    return `${this.id}: ${this.model} @ ${this.baseUrl}`;
  }

  private headers(): Record<string, string> {
    const headers: Record<string, string> = {
      'content-type': 'application/json',
      accept: 'application/json',
    };
    if (this.apiKey) headers.authorization = `Bearer ${this.apiKey}`;
    return headers;
  }

  private body(req: CompletionRequest, stream: boolean): string {
    return JSON.stringify({
      model: this.model,
      messages: req.messages,
      temperature: req.temperature ?? 0.4,
      max_tokens: req.maxTokens ?? this.maxOutputTokens,
      stream,
    });
  }

  private async request(req: CompletionRequest, stream: boolean): Promise<Response> {
    if (!this.isConfigured()) {
      throw new AiError('not_configured', 'AI provider is not configured (missing API key).');
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    if (req.signal) {
      if (req.signal.aborted) controller.abort();
      else req.signal.addEventListener('abort', () => controller.abort(), { once: true });
    }
    try {
      const res = await fetch(`${this.baseUrl}/chat/completions`, {
        method: 'POST',
        headers: this.headers(),
        body: this.body(req, stream),
        signal: controller.signal,
      });
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        const snippet = text.slice(0, 400);
        if (res.status === 401 || res.status === 403) {
          throw new AiError('auth', `Provider rejected the credentials (HTTP ${res.status}).`);
        }
        if (res.status === 429) {
          throw new AiError('rate_limited', 'Provider rate limit reached (HTTP 429).', true);
        }
        throw new AiError(
          'upstream_error',
          `Provider error HTTP ${res.status}: ${snippet || res.statusText}`,
          res.status >= 500,
        );
      }
      return res;
    } catch (err) {
      if (err instanceof AiError) throw err;
      if (err instanceof Error && (err.name === 'AbortError' || err.name === 'TimeoutError')) {
        if (req.signal?.aborted) throw new AiError('aborted', 'Request cancelled.');
        throw new AiError('timeout', `Provider did not respond within ${this.timeoutMs} ms.`, true);
      }
      throw new AiError('upstream_error', `Could not reach provider: ${(err as Error).message}`, true);
    } finally {
      clearTimeout(timeout);
    }
  }

  async complete(req: CompletionRequest): Promise<CompletionResult> {
    const started = Date.now();
    const res = await this.request(req, false);
    const data = (await res.json().catch(() => null)) as ChatCompletionResponse | null;
    const text = data?.choices?.[0]?.message?.content ?? '';
    if (!text.trim()) {
      throw new AiError('bad_response', 'Provider returned an empty completion.');
    }
    return {
      text,
      usage: {
        promptTokens: data?.usage?.prompt_tokens,
        completionTokens: data?.usage?.completion_tokens,
      },
      provider: this.id,
      model: this.model,
      latencyMs: Date.now() - started,
      finishReason: data?.choices?.[0]?.finish_reason ?? undefined,
    };
  }

  async completeStream(req: CompletionRequest, handlers: StreamHandlers): Promise<CompletionResult> {
    const started = Date.now();
    const res = await this.request(req, true);
    if (!res.body) throw new AiError('bad_response', 'Provider returned no response body.');

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let text = '';
    let finishReason: string | undefined;
    let promptTokens: number | undefined;
    let completionTokens: number | undefined;

    const handleLine = (line: string): void => {
      const trimmed = line.trim();
      if (!trimmed.startsWith('data:')) return;
      const payload = trimmed.slice(5).trim();
      if (payload === '[DONE]') return;
      try {
        const json = JSON.parse(payload) as ChatCompletionResponse;
        const choice = json.choices?.[0];
        const delta = choice?.delta?.content ?? '';
        if (delta) {
          text += delta;
          handlers.onDelta(delta);
        }
        if (choice?.finish_reason) finishReason = choice.finish_reason;
        if (json.usage?.prompt_tokens) promptTokens = json.usage.prompt_tokens;
        if (json.usage?.completion_tokens) completionTokens = json.usage.completion_tokens;
      } catch {
        // Ignore keep-alive or partial chunks that are not valid JSON lines.
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) handleLine(line);
    }
    if (buffer) handleLine(buffer);

    if (!text.trim()) {
      throw new AiError('bad_response', 'Provider stream ended without content.');
    }
    return {
      text,
      usage: { promptTokens, completionTokens },
      provider: this.id,
      model: this.model,
      latencyMs: Date.now() - started,
      finishReason,
    };
  }
}
