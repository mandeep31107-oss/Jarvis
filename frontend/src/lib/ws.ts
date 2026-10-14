/** WebSocket progress subscription with automatic reconnect. */

import { getToken } from "./api";

export interface ProgressEvent {
  type: string;
  channel?: string;
  stage?: string;
  percent?: number;
  message?: string;
  at?: string;
  [key: string]: unknown;
}

export function connectProjectStream(
  projectId: string,
  onEvent: (event: ProgressEvent) => void,
): () => void {
  let closed = false;
  let socket: WebSocket | null = null;
  let retry = 0;

  const open = () => {
    if (closed) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const token = getToken();
    const url = `${proto}://${window.location.host}/ws/projects/${projectId}${
      token ? `?token=${encodeURIComponent(token)}` : ""
    }`;
    socket = new WebSocket(url);
    socket.onmessage = (message) => {
      try {
        onEvent(JSON.parse(message.data) as ProgressEvent);
      } catch {
        /* ignore malformed frames */
      }
    };
    socket.onclose = () => {
      if (closed) return;
      retry += 1;
      window.setTimeout(open, Math.min(8000, 500 * retry));
    };
    socket.onerror = () => socket?.close();
  };

  open();
  return () => {
    closed = true;
    socket?.close();
  };
}
