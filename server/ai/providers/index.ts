import type { AppConfig } from '../../config.js';
import type { AiProvider } from '../types.js';
import { OfflineProvider } from './offline.js';
import { OpenAiCompatibleProvider } from './openaiCompatible.js';

export function createProvider(config: AppConfig): AiProvider {
  if (config.ai.provider === 'offline') return new OfflineProvider();

  return new OpenAiCompatibleProvider({
    id: config.ai.provider,
    baseUrl: config.ai.baseUrl,
    model: config.ai.model,
    apiKey: config.ai.apiKey,
    timeoutMs: config.ai.timeoutMs,
    maxOutputTokens: config.ai.maxOutputTokens,
  });
}

export { OfflineProvider, OpenAiCompatibleProvider };
