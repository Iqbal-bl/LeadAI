import { Injectable } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { ApiService } from './api.service';
import { AuthService } from './auth.service';
import {
  PublicCompany,
  ChatStartPayload,
  ChatSession,
  ChatMessage,
} from '../models/chat.models';

@Injectable({
  providedIn: 'root',
})
export class ChatService {
  constructor(
    private apiService: ApiService,
    private authService: AuthService,
  ) {}

  // GET /public/companies
  public getPublicCompanies(): Observable<PublicCompany[]> {
    return this.apiService.get<PublicCompany[]>('public/companies');
  }

  // POST /public/chat/start
  public startChat(payload: ChatStartPayload): Observable<ChatSession> {
    return this.apiService.post<ChatSession>('public/chat/start', payload).pipe(
      tap((session) => {
        if (session.session_token) {
          this.authService.setWidgetToken(session.session_token);
        }
      }),
    );
  }

  // POST /public/chat/messages
  public sendChatMessage(message: string): Observable<ChatMessage> {
    return this.apiService.post<ChatMessage>('public/chat/messages', {
      message,
    });
  }

  // GET /public/chat/messages (polling)
  public pollChatMessages(): Observable<ChatMessage[]> {
    return this.apiService.get<ChatMessage[]>('public/chat/messages');
  }

  // POST /public/chat/end
  public endChat(): Observable<void> {
    return this.apiService.post<void>('public/chat/end', null).pipe(
      tap(() => {
        this.authService.setWidgetToken('');
      }),
    );
  }
}
