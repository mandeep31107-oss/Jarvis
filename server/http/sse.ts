import type { Response } from 'express';

/** Minimal Server-Sent Events writer with safe JSON framing. */
export class SseStream {
  private closed = false;

  constructor(private readonly res: Response) {
    res.status(200).setHeader('Content-Type', 'text/event-stream; charset=utf-8');
    res.setHeader('Cache-Control', 'no-cache, no-transform');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('X-Accel-Buffering', 'no');
    res.flushHeaders?.();
  }

  send(event: string, data: unknown): void {
    if (this.closed) return;
    try {
      this.res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    } catch {
      this.closed = true;
    }
  }

  comment(text: string): void {
    if (this.closed) return;
    try {
      this.res.write(`: ${text}\n\n`);
    } catch {
      this.closed = true;
    }
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    try {
      this.res.end();
    } catch {
      /* already ended */
    }
  }

  get isClosed(): boolean {
    return this.closed || this.res.writableEnded;
  }
}
