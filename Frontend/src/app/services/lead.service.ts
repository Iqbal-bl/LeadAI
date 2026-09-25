import { Injectable, inject } from '@angular/core';
import { WebsocketService } from './websocket.service';

/**
 * LeadService delegates to the unified WebsocketService.
 * Maintains backwards compatibility for components that inject LeadService.
 */
@Injectable({
  providedIn: 'root',
})
export class LeadService {
  private ws = inject(WebsocketService);

  public get conversationMessages$() {
    return this.ws.conversationMessages$;
  }

  public get inboxMessages$() {
    return this.ws.inboxMessages$;
  }

  public get callEvents$() {
    return this.ws.callEvents$;
  }

  public get activeConversationId(): string | null {
    return this.ws.currentConversationId;
  }

  public get activeInboxClientId(): string | null {
    return this.ws.currentInboxClientId;
  }

  public connectConversation(conversationId: string): void {
    this.ws.connectConversation(conversationId);
  }

  public disconnectConversation(): void {
    this.ws.disconnectConversation();
  }

  public connectInbox(clientId: string): void {
    this.ws.connectInbox(clientId);
  }

  public disconnectInbox(): void {
    this.ws.disconnectInbox();
  }
}
