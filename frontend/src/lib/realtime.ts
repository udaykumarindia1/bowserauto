/**
 * Realtime client for `/api/ws`.
 *
 * One WebSocket per browser tab carries:
 *  - organisation events (task/scene/session/activity/notification updates)
 *  - live browser frames + status for subscribed sessions (CDP screencast relayed by the worker)
 *  - human input events sent back to the browser during takeover
 *
 * The client reconnects with exponential backoff and re-subscribes sessions automatically.
 */

import type { FrameMessage, OrgEvent, RealtimeMessage, SessionActionMessage, SessionStatusMessage } from "./types";

type Listener<T> = (message: T) => void;

export type ConnectionState = "idle" | "connecting" | "open" | "closed" | "error";

interface SessionListeners {
  frame: Set<Listener<FrameMessage>>;
  status: Set<Listener<SessionStatusMessage>>;
  action: Set<Listener<SessionActionMessage>>;
}

const SESSION_MESSAGE_TYPES = new Set(["frame", "status", "action"]);

class RealtimeClient {
  private socket: WebSocket | null = null;
  private state: ConnectionState = "idle";
  private attempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private wanted = false;
  private readonly eventListeners = new Set<Listener<OrgEvent>>();
  private readonly stateListeners = new Set<Listener<ConnectionState>>();
  private readonly errorListeners = new Set<Listener<string>>();
  private readonly sessions = new Map<string, SessionListeners>();
  private queue: string[] = [];
  lastLatencyMs: number | null = null;

  get connectionState(): ConnectionState {
    return this.state;
  }

  connect(): void {
    this.wanted = true;
    if (typeof window === "undefined") return;
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) return;
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${window.location.host}/api/ws`;
    this.setState("connecting");
    const socket = new WebSocket(url);
    this.socket = socket;
    socket.onopen = () => {
      this.attempts = 0;
      this.setState("open");
      for (const sessionId of this.sessions.keys()) {
        socket.send(JSON.stringify({ type: "subscribe_session", session_id: sessionId }));
      }
      for (const pending of this.queue.splice(0)) socket.send(pending);
      this.pingTimer = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "ping", ts: Date.now() }));
      }, 20000);
    };
    socket.onmessage = (event) => this.handle(event.data as string);
    socket.onerror = () => this.setState("error");
    socket.onclose = (event) => {
      if (this.pingTimer) clearInterval(this.pingTimer);
      this.pingTimer = null;
      this.socket = null;
      this.setState("closed");
      if (event.code === 4401 || event.code === 4403) {
        // Not authenticated: do not hammer the server; the auth gate will reconnect after login.
        this.wanted = false;
        window.dispatchEvent(new CustomEvent("ba:unauthorized"));
        return;
      }
      if (this.wanted) this.scheduleReconnect();
    };
  }

  disconnect(): void {
    this.wanted = false;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.socket?.close(1000, "client closed");
    this.socket = null;
    this.setState("idle");
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    const delay = Math.min(15000, 500 * 2 ** Math.min(this.attempts, 5));
    this.attempts += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.wanted) this.connect();
    }, delay);
  }

  private setState(state: ConnectionState): void {
    this.state = state;
    for (const listener of this.stateListeners) listener(state);
  }

  private handle(raw: string): void {
    let message: RealtimeMessage;
    try {
      message = JSON.parse(raw) as RealtimeMessage;
    } catch {
      return;
    }
    if (message.type === "pong") {
      const sent = Number((message as { ts?: number }).ts);
      if (sent) this.lastLatencyMs = Date.now() - sent;
      return;
    }
    if (message.type === "hello" || message.type === "subscribed") return;
    if (message.type === "error") {
      const text = String((message as { message?: string }).message ?? "Realtime error");
      for (const listener of this.errorListeners) listener(text);
      return;
    }
    if (SESSION_MESSAGE_TYPES.has(message.type) && "session_id" in message && typeof message.session_id === "string") {
      const listeners = this.sessions.get(message.session_id);
      if (!listeners) return;
      if (message.type === "frame") for (const l of listeners.frame) l(message as FrameMessage);
      else if (message.type === "status") for (const l of listeners.status) l(message as SessionStatusMessage);
      else if (message.type === "action") for (const l of listeners.action) l(message as SessionActionMessage);
      return;
    }
    const event = message as OrgEvent;
    for (const listener of this.eventListeners) listener(event);
  }

  private send(payload: Record<string, unknown>): void {
    const text = JSON.stringify(payload);
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(text);
    } else {
      if (this.queue.length > 200) this.queue.shift();
      this.queue.push(text);
      this.connect();
    }
  }

  onEvent(listener: Listener<OrgEvent>): () => void {
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }

  onState(listener: Listener<ConnectionState>): () => void {
    this.stateListeners.add(listener);
    listener(this.state);
    return () => this.stateListeners.delete(listener);
  }

  onError(listener: Listener<string>): () => void {
    this.errorListeners.add(listener);
    return () => this.errorListeners.delete(listener);
  }

  subscribeSession(
    sessionId: string,
    handlers: { frame?: Listener<FrameMessage>; status?: Listener<SessionStatusMessage>; action?: Listener<SessionActionMessage> },
  ): () => void {
    let listeners = this.sessions.get(sessionId);
    const fresh = !listeners;
    if (!listeners) {
      listeners = { frame: new Set(), status: new Set(), action: new Set() };
      this.sessions.set(sessionId, listeners);
    }
    if (handlers.frame) listeners.frame.add(handlers.frame);
    if (handlers.status) listeners.status.add(handlers.status);
    if (handlers.action) listeners.action.add(handlers.action);
    if (fresh) this.send({ type: "subscribe_session", session_id: sessionId });
    else if (this.socket?.readyState === WebSocket.OPEN) this.send({ type: "subscribe_session", session_id: sessionId }); // forces a fresh frame
    return () => {
      const current = this.sessions.get(sessionId);
      if (!current) return;
      if (handlers.frame) current.frame.delete(handlers.frame);
      if (handlers.status) current.status.delete(handlers.status);
      if (handlers.action) current.action.delete(handlers.action);
      if (current.frame.size + current.status.size + current.action.size === 0) {
        this.sessions.delete(sessionId);
        if (this.socket?.readyState === WebSocket.OPEN) this.send({ type: "unsubscribe_session", session_id: sessionId });
      }
    };
  }

  /** Forward a human input event to the browser (only honoured during takeover). */
  sendInput(sessionId: string, event: Record<string, unknown>): void {
    this.send({ type: "input", session_id: sessionId, event });
  }
}

let client: RealtimeClient | null = null;

export function realtime(): RealtimeClient {
  if (!client) client = new RealtimeClient();
  return client;
}
