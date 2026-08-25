import { Injectable, inject, OnDestroy } from '@angular/core';
import { Subject, Subscription } from 'rxjs';
import { WebsocketService } from './websocket.service';

@Injectable({
  providedIn: 'root',
})
export class LeadService implements OnDestroy {
  private websocketService = inject(WebsocketService);

  // ── Conversation WebSocket ──
  private conversationSub: Subscription | null = null;
  private conversationId: string | null = null;
  public conversationMessages$ = new Subject<any>();

  // ── Inbox WebSocket ──
  private inboxSub: Subscription | null = null;
  private inboxClientId: string | null = null;
  public inboxMessages$ = new Subject<any>();

  // ── Conversation WebSocket Methods ──

  get activeConversationId(): string | null {
    return this.conversationId;
  }

  public connectConversation(conversationId: string): void {
    if (this.conversationId === conversationId && this.conversationSub) {
      console.log(`[LeadService] Already connected to conversation: ${conversationId}`);
      return;
    }

    this.disconnectConversation();
    this.conversationId = conversationId;
    console.log(`[LeadService] Connecting conversation WebSocket: ${conversationId}`);

    this.conversationSub = this.websocketService
      .connectConversation(conversationId)
      .subscribe({
        next: (message) => {
          console.log(`[LeadService] Conversation WS message:`, message);
          this.conversationMessages$.next(message);
        },
        error: (error) => {
          console.error(`[LeadService] Conversation WS error:`, error);
        },
        complete: () => {
          console.log(`[LeadService] Conversation WS completed: ${conversationId}`);
        },
      });
  }

  public disconnectConversation(): void {
    if (this.conversationSub) {
      console.log(`[LeadService] Disconnecting conversation WS: ${this.conversationId}`);
      this.conversationSub.unsubscribe();
      this.conversationSub = null;
    }
    this.conversationId = null;
  }

  // ── Inbox WebSocket Methods ──

  get activeInboxClientId(): string | null {
    return this.inboxClientId;
  }

  public connectInbox(clientId: string): void {
    if (this.inboxClientId === clientId && this.inboxSub) {
      console.log(`[LeadService] Already connected to inbox: ${clientId}`);
      return;
    }

    this.disconnectInbox();
    this.inboxClientId = clientId;
    console.log(`[LeadService] Connecting inbox WebSocket: ${clientId}`);

    this.inboxSub = this.websocketService
      .connectInbox(clientId)
      .subscribe({
        next: (message) => {
          console.log(`[LeadService] Inbox WS message:`, message);
          this.inboxMessages$.next(message);
        },
        error: (error) => {
          console.error(`[LeadService] Inbox WS error:`, error);
        },
        complete: () => {
          console.log(`[LeadService] Inbox WS completed: ${clientId}`);
        },
      });
  }

  public disconnectInbox(): void {
    if (this.inboxSub) {
      console.log(`[LeadService] Disconnecting inbox WS: ${this.inboxClientId}`);
      this.inboxSub.unsubscribe();
      this.inboxSub = null;
    }
    this.inboxClientId = null;
  }

  ngOnDestroy(): void {
    this.disconnectConversation();
    this.disconnectInbox();
  }
}
