import { lookup } from 'node:dns/promises';
import { isIP } from 'node:net';

/**
 * Research fetch tool.
 *
 * Treats every web page as untrusted data. It never executes page content and
 * the orchestrator wraps results in <untrusted_content> when passing them to a
 * model, so a malicious page cannot rewrite instructions.
 *
 * SSRF protection: only http(s), max 4 redirects, private/loopback/link-local
 * addresses blocked (including IP literals), optional host allowlist,
 * 512 KB response cap and a hard timeout.
 */

export interface FetchedSource {
  url: string;
  finalUrl: string;
  title: string;
  text: string;
  fetchedAt: string;
  truncated: boolean;
}

export interface FetchFailure {
  url: string;
  error: string;
  code:
    | 'network_disabled'
    | 'invalid_url'
    | 'blocked_host'
    | 'not_allowed'
    | 'http_error'
    | 'timeout'
    | 'too_large'
    | 'unreachable';
}

export type FetchResult = { ok: true; source: FetchedSource } | { ok: false; failure: FetchFailure };

export interface FetchOptions {
  allowOutbound: boolean;
  allowedHosts: string[];
  timeoutMs?: number;
  maxBytes?: number;
  maxRedirects?: number;
}

const DEFAULT_TIMEOUT = 15_000;
const DEFAULT_MAX_BYTES = 512 * 1024;
const MAX_TEXT_CHARS = 20_000;

function isPrivateAddress(address: string): boolean {
  const version = isIP(address);
  if (version === 4) {
    const parts = address.split('.').map(Number);
    const [a = 0, b = 0] = parts;
    if (a === 0 || a === 10 || a === 127) return true;
    if (a === 169 && b === 254) return true; // link-local + cloud metadata
    if (a === 172 && b >= 16 && b <= 31) return true;
    if (a === 192 && b === 168) return true;
    if (a === 100 && b >= 64 && b <= 127) return true; // carrier-grade NAT
    if (a === 198 && (b === 18 || b === 19)) return true;
    return false;
  }
  if (version === 6) {
    const lower = address.toLowerCase();
    if (lower === '::1' || lower === '::') return true;
    if (lower.startsWith('fe80') || lower.startsWith('fc') || lower.startsWith('fd')) return true;
    if (lower.startsWith('::ffff:')) return isPrivateAddress(lower.replace('::ffff:', ''));
    return false;
  }
  return true; // unknown → refuse
}

async function assertPublicHost(hostname: string, allowedHosts: string[]): Promise<void> {
  const host = hostname.toLowerCase();
  if (allowedHosts.length > 0 && !allowedHosts.some((a) => host === a || host.endsWith(`.${a}`))) {
    throw Object.assign(new Error(`Host ${host} is not in RESEARCH_ALLOWED_HOSTS.`), {
      code: 'not_allowed',
    });
  }
  if (isIP(host)) {
    if (isPrivateAddress(host)) {
      throw Object.assign(new Error('Private IP addresses are blocked.'), { code: 'blocked_host' });
    }
    return;
  }
  if (['localhost', 'localhost.localdomain'].includes(host) || host.endsWith('.local') || host.endsWith('.internal')) {
    throw Object.assign(new Error('Local hostnames are blocked.'), { code: 'blocked_host' });
  }
  let addresses: Array<{ address: string }>;
  try {
    addresses = await lookup(host, { all: true });
  } catch {
    throw Object.assign(new Error(`Could not resolve ${host}.`), { code: 'unreachable' });
  }
  if (addresses.length === 0) {
    throw Object.assign(new Error(`Could not resolve ${host}.`), { code: 'unreachable' });
  }
  for (const { address } of addresses) {
    if (isPrivateAddress(address)) {
      throw Object.assign(new Error('Host resolves to a private address; blocked.'), { code: 'blocked_host' });
    }
  }
}

export function htmlToText(html: string): { title: string; text: string } {
  const title = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(html)?.[1]?.trim() ?? '';
  const text = html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ')
    .replace(/<svg[\s\S]*?<\/svg>/gi, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<\/(p|div|section|article|li|h[1-6]|tr|br)>/gi, '\n')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/[ \t\u00a0]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  return { title: decodeEntities(title), text };
}

function decodeEntities(s: string): string {
  return s
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ');
}

export async function fetchSource(rawUrl: string, opts: FetchOptions): Promise<FetchResult> {
  const fetchedAt = new Date().toISOString();
  if (!opts.allowOutbound) {
    return {
      ok: false,
      failure: {
        url: rawUrl,
        code: 'network_disabled',
        error:
          'Outbound network access is disabled (ALLOW_OUTBOUND_FETCH=false). No page was retrieved, so no research was performed.',
      },
    };
  }

  let current: URL;
  try {
    current = new URL(rawUrl);
  } catch {
    return { ok: false, failure: { url: rawUrl, code: 'invalid_url', error: 'Not a valid absolute URL.' } };
  }

  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT;
  const maxBytes = opts.maxBytes ?? DEFAULT_MAX_BYTES;
  const maxRedirects = opts.maxRedirects ?? 4;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    for (let hop = 0; hop <= maxRedirects; hop += 1) {
      if (current.protocol !== 'http:' && current.protocol !== 'https:') {
        return {
          ok: false,
          failure: { url: current.toString(), code: 'invalid_url', error: 'Only http and https are allowed.' },
        };
      }
      try {
        await assertPublicHost(current.hostname, opts.allowedHosts);
      } catch (err) {
        const e = err as Error & { code?: string };
        return {
          ok: false,
          failure: {
            url: current.toString(),
            code: (e.code as FetchFailure['code']) ?? 'blocked_host',
            error: e.message,
          },
        };
      }

      const res = await fetch(current.toString(), {
        method: 'GET',
        redirect: 'manual',
        signal: controller.signal,
        headers: {
          'user-agent': 'JarvisResearch/0.1 (personal assistant; contact owner)',
          accept: 'text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5',
        },
      });

      if (res.status >= 300 && res.status < 400) {
        const location = res.headers.get('location');
        if (!location) {
          return {
            ok: false,
            failure: { url: current.toString(), code: 'http_error', error: `HTTP ${res.status} without location.` },
          };
        }
        current = new URL(location, current);
        continue;
      }

      if (!res.ok) {
        return {
          ok: false,
          failure: {
            url: current.toString(),
            code: 'http_error',
            error: `The site returned HTTP ${res.status}. Nothing was retrieved.`,
          },
        };
      }

      const contentType = res.headers.get('content-type') ?? '';
      const reader = res.body?.getReader();
      if (!reader) {
        return {
          ok: false,
          failure: { url: current.toString(), code: 'unreachable', error: 'Empty response body.' },
        };
      }
      let received = 0;
      let truncated = false;
      const chunks: Uint8Array[] = [];
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) {
          received += value.byteLength;
          if (received > maxBytes) {
            truncated = true;
            chunks.push(value.slice(0, Math.max(0, maxBytes - (received - value.byteLength))));
            await reader.cancel().catch(() => undefined);
            break;
          }
          chunks.push(value);
        }
      }
      const buffer = Buffer.concat(chunks.map((c) => Buffer.from(c)));
      const raw = buffer.toString('utf8');
      const isHtml = /html|xml/i.test(contentType) || /<html/i.test(raw.slice(0, 500));
      const parsed = isHtml ? htmlToText(raw) : { title: '', text: raw.trim() };
      const text = parsed.text.slice(0, MAX_TEXT_CHARS);
      return {
        ok: true,
        source: {
          url: rawUrl,
          finalUrl: current.toString(),
          title: parsed.title || current.hostname,
          text,
          fetchedAt,
          truncated: truncated || parsed.text.length > MAX_TEXT_CHARS,
        },
      };
    }
    return {
      ok: false,
      failure: { url: rawUrl, code: 'http_error', error: 'Too many redirects.' },
    };
  } catch (err) {
    const e = err as Error;
    if (e.name === 'AbortError') {
      return {
        ok: false,
        failure: { url: rawUrl, code: 'timeout', error: `Request exceeded ${timeoutMs} ms.` },
      };
    }
    return {
      ok: false,
      failure: {
        url: rawUrl,
        code: 'unreachable',
        error: `Network request failed: ${e.message}. No page was retrieved.`,
      },
    };
  } finally {
    clearTimeout(timer);
  }
}
