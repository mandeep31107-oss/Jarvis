export type ChatRole = 'system' | 'user' | 'assistant';

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

export interface CompletionRequest {
  messages: ChatMessage[];
  temperature?: number;
  maxTokens?: number;
  signal?: AbortSignal;
}

export interface TokenUsage {
  promptTokens?: number;
  completionTokens?: number;
}

export interface CompletionResult {
  text: string;
  usage: TokenUsage;
  provider: string;
  model: string;
  latencyMs: number;
  finishReason?: string;
}

export interface StreamHandlers {
  onDelta: (chunk: string) => void;
}

export interface AiProvider {
  readonly id: string;
  readonly model: string;
  /** True when the provider has everything it needs (API key, endpoint, ...). */
  isConfigured(): boolean;
  /** Human readable note about the provider state, shown in the UI. */
  describe(): string;
  complete(req: CompletionRequest): Promise<CompletionResult>;
  /** Optional real token streaming. Falls back to complete() when absent. */
  completeStream?(req: CompletionRequest, handlers: StreamHandlers): Promise<CompletionResult>;
}

export class AiError extends Error {
  readonly code:
    | 'not_configured'
    | 'timeout'
    | 'rate_limited'
    | 'auth'
    | 'upstream_error'
    | 'aborted'
    | 'bad_response';
  readonly retryable: boolean;

  constructor(code: AiError['code'], message: string, retryable = false) {
    super(message);
    this.name = 'AiError';
    this.code = code;
    this.retryable = retryable;
  }
}
