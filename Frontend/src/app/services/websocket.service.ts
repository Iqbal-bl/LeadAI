import { Injectable, inject, OnDestroy } from '@angular/core';
import { Observable, Subject, filter, map } from 'rxjs';
import { AuthService } from './auth.service';
import { environment } from '../../environments/environment';

export interface WebSocketEvent<T = any> {
  type?: string;
  channel?: string;
  data?: T;
  [key: string]: any;
}

/**
 * Unified, generic WebSocket service for the entire application.
 * Manages all WebSocket communication (conversation, calling/voice, inbox, etc.)
 * in a single, shared, robust service.
 */
@Injectable({
  providedIn: 'root',
})
export class WebsocketService implements OnDestroy {
  private authService = inject(AuthService);

  // Active WebSocket connections keyed by unique channel identifier
  private sockets = new Map<string, WebSocket>();
  private messageSubjects = new Map<string, Subject<any>>();
  private reconnectTimers = new Map<string, any>();

  // Global aggregate message stream for all events
  public readonly events$ = new Subject<WebSocketEvent>();

  // Primary shared streams for UI workflows
  public readonly conversationMessages$ = new Subject<any>();
  public readonly inboxMessages$ = new Subject<any>();
  public readonly callEvents$ = new Subject<any>();

  // Active tracking IDs
  private activeConversationId: string | null = null;
  private activeInboxClientId: string | null = null;
  private activeCallSid: string | null = null;

  // ─────────────────────────────────────────────────────────────
  // URL Resolver
  // ─────────────────────────────────────────────────────────────
  public getWsUrl(path: string): string {
    const token = this.authService.getStaffToken();
    const tokenParam = token ? `?token=${encodeURIComponent(token)}` : '';

    if (environment.wsUrl) {
      return `${environment.wsUrl}${path}${tokenParam}`;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host || 'localhost:5050';
    return `${protocol}//${host}${path}${tokenParam}`;
  }

  // ─────────────────────────────────────────────────────────────
  // Core Generic Connection Engine
  // ─────────────────────────────────────────────────────────────

  /**
   * Connect to any WebSocket path generically.
   * If a connection for channelKey already exists and is active, reuses it.
   */
  public connect<T = any>(channelKey: string, path: string): Subject<T> {
    const existingSubject = this.messageSubjects.get(channelKey);
    const existingSocket = this.sockets.get(channelKey);

    if (
      existingSocket &&
      (existingSocket.readyState === WebSocket.OPEN ||
        existingSocket.readyState === WebSocket.CONNECTING)
    ) {
      return existingSubject as Subject<T>;
    }

    // Clean up any stale socket
    this.disconnect(channelKey);

    const subject = new Subject<T>();
    this.messageSubjects.set(channelKey, subject);

    const url = this.getWsUrl(path);
    let ws: WebSocket;

    try {
      ws = new WebSocket(url);
      this.sockets.set(channelKey, ws);
    } catch (err) {
      console.error(`[WebsocketService] Failed to create socket for ${channelKey}:`, err);
      subject.error(err);
      return subject;
    }

    ws.onopen = () => {
      console.log(`[WebsocketService] Connected to ${channelKey} (${path})`);
    };

    ws.onmessage = (event) => {
      let data: any;
      try {
        data = JSON.parse(event.data);
      } catch {
        data = event.data;
      }

      // 1. Channel-specific subject
      subject.next(data);

      // 2. Global aggregate stream
      this.events$.next({
        channel: channelKey,
        data,
        ...(typeof data === 'object' && data !== null ? data : {}),
      });

      // 3. Dispatch to shared application streams
      if (channelKey.startsWith('conversation:') || channelKey === 'conversation') {
        this.conversationMessages$.next(data);

        // If message is calling/voice related (call_status, transcript, turn, etc.)
        if (
          data &&
          (data.type === 'call_status' ||
            data.type === 'status' ||
            data.type === 'transcript' ||
            data.type === 'turn' ||
            data.call_sid)
        ) {
          this.callEvents$.next(data);
        }
      } else if (channelKey.startsWith('inbox:') || channelKey === 'inbox') {
        this.inboxMessages$.next(data);
      } else if (channelKey.startsWith('call:') || channelKey === 'call') {
        this.callEvents$.next(data);
      }
    };

    ws.onerror = (err) => {
      console.error(`[WebsocketService] Error on ${channelKey}:`, err);
      subject.error(err);
    };

    ws.onclose = (event) => {
      console.log(`[WebsocketService] Closed ${channelKey} (clean=${event.wasClean})`);
      this.sockets.delete(channelKey);
    };

    return subject;
  }

  /**
   * Generic event filter by type across a channel or globally.
   */
  public on<T = any>(eventType: string, channelKey?: string): Observable<T> {
    if (channelKey && this.messageSubjects.has(channelKey)) {
      return this.messageSubjects.get(channelKey)!.pipe(
        filter((msg: any) => msg && (msg.type === eventType || msg.event === eventType)),
      );
    }
    return this.events$.pipe(
      filter((evt: any) => evt && (evt.type === eventType || evt.event === eventType)),
      map((evt: any) => (evt.data !== undefined ? evt.data : evt)),
    );
  }

  /**
   * Generic send to an open socket.
   */
  public send(channelKey: string, payload: any): boolean {
    const ws = this.sockets.get(channelKey);
    if (ws && ws.readyState === WebSocket.OPEN) {
      const data = typeof payload === 'string' ? payload : JSON.stringify(payload);
      ws.send(data);
      return true;
    }
    console.warn(`[WebsocketService] Cannot send on ${channelKey}: socket not open`);
    return false;
  }

  /**
   * Disconnect a specific channel.
   */
  public disconnect(channelKey: string): void {
    const ws = this.sockets.get(channelKey);
    if (ws) {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close(1000, 'Normal Closure');
      }
      this.sockets.delete(channelKey);
    }

    const timer = this.reconnectTimers.get(channelKey);
    if (timer) {
      clearTimeout(timer);
      this.reconnectTimers.delete(channelKey);
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Unified Calling & Conversation Socket Methods
  // ─────────────────────────────────────────────────────────────

  /**
   * Connect to conversation WebSocket:
   * /ws/leadai/conversation/{conversation_id}
   *
   * Handles BOTH chat conversation messages AND live call events/transcripts
   * over a single multiplexed channel.
   */
  public connectConversation(conversationId: string): Subject<any> {
    if (
      this.activeConversationId === conversationId &&
      this.sockets.has(`conversation:${conversationId}`)
    ) {
      return this.messageSubjects.get(`conversation:${conversationId}`)!;
    }

    if (this.activeConversationId && this.activeConversationId !== conversationId) {
      this.disconnectConversation();
    }

    this.activeConversationId = conversationId;
    return this.connect(
      `conversation:${conversationId}`,
      `/ws/leadai/conversation/${conversationId}`,
    );
  }

  public disconnectConversation(): void {
    if (this.activeConversationId) {
      this.disconnect(`conversation:${this.activeConversationId}`);
      this.activeConversationId = null;
    }
  }

  /**
   * Connect to a specific call or transcript channel if direct SID connection is used:
   * /ws/{call_sid} or /ws/transcript/{call_sid}
   */
  public connectCall(callSid: string, mode: 'transcript' | 'general' = 'general'): Subject<any> {
    const path = mode === 'transcript' ? `/ws/transcript/${callSid}` : `/ws/${callSid}`;
    this.activeCallSid = callSid;
    return this.connect(`call:${callSid}`, path);
  }

  public disconnectCall(callSid?: string): void {
    const sid = callSid || this.activeCallSid;
    if (sid) {
      this.disconnect(`call:${sid}`);
      if (this.activeCallSid === sid) {
        this.activeCallSid = null;
      }
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Inbox Socket Methods
  // ─────────────────────────────────────────────────────────────

  /**
   * Connect to inbox events WebSocket:
   * /ws/leadai/inbox/{client_id}
   */
  public connectInbox(clientId: string): Subject<any> {
    if (
      this.activeInboxClientId === clientId &&
      this.sockets.has(`inbox:${clientId}`)
    ) {
      return this.messageSubjects.get(`inbox:${clientId}`)!;
    }

    if (this.activeInboxClientId && this.activeInboxClientId !== clientId) {
      this.disconnectInbox();
    }

    this.activeInboxClientId = clientId;
    return this.connect(`inbox:${clientId}`, `/ws/leadai/inbox/${clientId}`);
  }

  public disconnectInbox(): void {
    if (this.activeInboxClientId) {
      this.disconnect(`inbox:${this.activeInboxClientId}`);
      this.activeInboxClientId = null;
    }
  }

  // ─────────────────────────────────────────────────────────────
  // State Accessors & Cleanup
  // ─────────────────────────────────────────────────────────────

  get currentConversationId(): string | null {
    return this.activeConversationId;
  }

  get currentInboxClientId(): string | null {
    return this.activeInboxClientId;
  }

  get currentCallSid(): string | null {
    return this.activeCallSid;
  }

  public disconnectAll(): void {
    for (const key of Array.from(this.sockets.keys())) {
      this.disconnect(key);
    }
    this.activeConversationId = null;
    this.activeInboxClientId = null;
    this.activeCallSid = null;
  }

  ngOnDestroy(): void {
    this.disconnectAll();
  }
}
