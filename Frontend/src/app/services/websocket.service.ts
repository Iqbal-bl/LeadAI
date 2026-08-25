import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { AuthService } from './auth.service';
import { environment } from '../../environments/environment';

@Injectable({
  providedIn: 'root'
})
export class WebsocketService {
  constructor(private authService: AuthService) {}

  private getWsUrl(path: string): string {
    const token = this.authService.getStaffToken();
    const tokenParam = token ? `?token=${encodeURIComponent(token)}` : '';
    
    if (environment.wsUrl) {
      return `${environment.wsUrl}${path}${tokenParam}`;
    }

    // Auto-resolve protocol and host based on browser window
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host || 'localhost:8000';
    
    // Return connection URL
    return `${protocol}//${host}${path}${tokenParam}`;
  }

  /**
   * Connect to the inbox events WebSocket:
   * ws://.../ws/leadai/inbox/{client_id}
   */
  public connectInbox(clientId: string): Observable<any> {
    const wsUrl = this.getWsUrl(`/ws/leadai/inbox/${clientId}`);
    return this.createWsObservable(wsUrl);
  }

  /**
   * Connect to the conversation event stream WebSocket:
   * ws://.../ws/leadai/conversation/{conversation_id}
   */
  public connectConversation(conversationId: string): Observable<any> {
    const wsUrl = this.getWsUrl(`/ws/leadai/conversation/${conversationId}`);
    return this.createWsObservable(wsUrl);
  }

  private createWsObservable(url: string): Observable<any> {
    return new Observable(observer => {
      let ws: WebSocket;
      
      try {
        ws = new WebSocket(url);
      } catch (err) {
        observer.error(err);
        return;
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          observer.next(data);
        } catch (err) {
          // If message is not JSON, yield raw data
          observer.next(event.data);
        }
      };

      ws.onerror = (event) => {
        observer.error(event);
      };

      ws.onclose = (event) => {
        if (event.wasClean) {
          observer.complete();
        } else {
          observer.error(new Error(`WebSocket connection closed unexpectedly: ${event.reason}`));
        }
      };

      // Cleanup function upon unsubscribe
      return () => {
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close();
        }
      };
    });
  }
}
