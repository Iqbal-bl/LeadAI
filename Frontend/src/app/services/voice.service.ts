import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import {
  CallStatus,
  CallDetails,
  CallTranscript,
  VoiceTurnResponse,
  CallSyncResponse,
} from '../models/voice.models';

@Injectable({
  providedIn: 'root',
})
export class VoiceService {
  constructor(private apiService: ApiService) {}

  // GET /voice/status (public)
  public getVoiceStatus(): Observable<CallStatus> {
    return this.apiService.get<CallStatus>('voice/status');
  }

  // POST /voice/conversations/{id}/call
  public initiateCall(
    conversationId: string,
    payload: {
      mode: 'ai_voice' | 'agent';
      script_id: string | null;
      override_number: string | null;
    },
  ): Observable<CallDetails> {
    return this.apiService.post<CallDetails>(
      `voice/conversations/${conversationId}/call`,
      payload,
      {
        companyScoped: true,
      },
    );
  }

  // GET /voice/conversations/{id}/calls
  public getCalls(conversationId: string): Observable<CallDetails[]> {
    return this.apiService.get<CallDetails[]>(
      `voice/conversations/${conversationId}/calls`,
      {
        companyScoped: true,
      },
    );
  }

  // POST /voice/calls/{id}/hangup
  public hangupCall(callId: string): Observable<void> {
    return this.apiService.post<void>(`voice/calls/${callId}/hangup`, null, {
      companyScoped: true,
    });
  }

  // POST /voice/calls/{id}/sync
  public syncCall(callId: string): Observable<CallSyncResponse> {
    return this.apiService.post<CallSyncResponse>(
      `voice/calls/${callId}/sync`,
      null,
      {
        companyScoped: true,
      },
    );
  }

  // POST /voice/calls/{id}/turn (synthetic voice loop)
  public submitVoiceTurn(
    callId: string,
    utterance: string,
  ): Observable<VoiceTurnResponse> {
    return this.apiService.post<VoiceTurnResponse>(
      `voice/calls/${callId}/turn`,
      { utterance },
      {
        companyScoped: true,
      },
    );
  }

  // GET /voice/calls/by-sid/{call_sid}/transcript
  public getCallTranscript(callSid: string): Observable<CallTranscript> {
    return this.apiService.get<CallTranscript>(
      `voice/calls/by-sid/${callSid}/transcript`,
      {
        companyScoped: true,
      },
    );
  }
}
