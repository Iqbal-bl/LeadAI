import { Component, Input, OnDestroy } from '@angular/core';
import { VoiceService } from '../../../../services/voice.service';
import { MessageService } from 'primeng/api';
import { LeadService } from '../../../../services/lead.service';
import { Subscription } from 'rxjs';

import { SharedModule } from '../../../../shared/shared.module';

@Component({
  selector: 'app-calling-panel',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './calling-panel.component.html',
})
export class CallingPanelComponent implements OnDestroy {
  @Input() lead!: any;

  isCallActive = false;
  callStatus = 'Idle';
  callMode: 'ai_voice' | 'agent' = 'ai_voice';
  callTimer = 0;
  totalCalls = 4;

  // Live Subtitles State
  liveSubtitle = '';
  liveSubtitleType = '';

  private currentCallId: string | null = null;
  private timerInterval: any;
  private fallbackTimer: any;
  private wsMsgSub: Subscription | null = null;

  constructor(
    private voiceService: VoiceService,
    private messageService: MessageService,
    private leadService: LeadService,
  ) {}

  ngOnDestroy(): void {
    this.clearTimer();
    this.clearFallbackTimer();
    this.disconnectWebSocket();
  }

  placeCall(mode: 'ai_voice' | 'agent'): void {
    if (!this.lead?.id) return;
    this.isCallActive = true;
    this.callMode = mode;
    this.callStatus = 'Initiating call...';
    this.callTimer = 0;

    const payload = {
      mode: mode,
      script_id: null,
      override_number: null,
    };

    this.voiceService.initiateCall(this.lead.id, payload).subscribe({
      next: (call) => {
        this.currentCallId = call.id;
        this.callStatus = 'Ringing...';

        // Connect to WebSocket and listen for events
        this.connectWebSocket(this.lead.id);

        // Set an 8-second pickup fallback timer in case WS events do not arrive
        this.fallbackTimer = setTimeout(() => {
          if (this.isCallActive && this.callStatus === 'Ringing...') {
            console.log('[CallingPanel] Fallback: Simulating call connection.');
            this.callStatus = 'Connected';
            this.startTimer();
          }
        }, 8000);
      },
      error: (err) => {
        this.disconnectWebSocket();
        this.clearFallbackTimer();
        this.isCallActive = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Call Failed',
          detail: err?.message || 'Carrier rejected outbound line connection.',
        });
      },
    });
  }

  hangup(): void {
    this.clearFallbackTimer();
    this.disconnectWebSocket();
    if (this.currentCallId) {
      this.callStatus = 'Hanging up...';
      this.voiceService.hangupCall(this.currentCallId).subscribe({
        next: () => {
          this.endCallSession();
        },
        error: () => {
          this.endCallSession(); // End locally regardless
        },
      });
    } else {
      this.endCallSession();
    }
  }

  private endCallSession(): void {
    this.clearFallbackTimer();
    this.disconnectWebSocket();
    this.isCallActive = false;
    this.callStatus = 'Idle';
    this.clearTimer();
    this.totalCalls++;
    this.messageService.add({
      severity: 'info',
      summary: 'Call Ended',
      detail: `Call duration: ${this.formatTimer(this.callTimer)}`,
    });
  }

  private connectWebSocket(conversationId: string): void {
    this.disconnectWebSocket();
    if (this.leadService.activeConversationId !== conversationId) {
      this.leadService.connectConversation(conversationId);
    }

    this.wsMsgSub = this.leadService.conversationMessages$.subscribe({
      next: (message) => {
        this.handleWebSocketMessage(message);
      },
      error: (err) => {
        console.error('[CallingPanel] WebSocket error propagation:', err);
      },
    });
  }

  private disconnectWebSocket(): void {
    if (this.wsMsgSub) {
      this.wsMsgSub.unsubscribe();
      this.wsMsgSub = null;
    }
    this.liveSubtitle = '';
    this.liveSubtitleType = '';
  }

  private clearFallbackTimer(): void {
    if (this.fallbackTimer) {
      clearTimeout(this.fallbackTimer);
      this.fallbackTimer = null;
    }
  }

  private handleWebSocketMessage(message: any): void {
    if (!message || typeof message !== 'object') return;

    if (message.type === 'call_status') {
      const status = message.status;
      console.log(
        `[CallingPanel] WebSocket call status update received: ${status}`,
      );

      switch (status) {
        case 'initiated':
          this.callStatus = 'Initiating call...';
          break;
        case 'ringing':
          this.callStatus = 'Ringing...';
          break;
        case 'answered':
          this.clearFallbackTimer();
          this.callStatus = 'Connected';
          this.startTimer();
          break;
        case 'completed':
        case 'busy':
        case 'no-answer':
        case 'failed':
          this.clearFallbackTimer();
          this.callStatus = `Ended (${status})`;
          setTimeout(() => {
            this.endCallSession();
          }, 1500);
          break;
        default:
          this.callStatus = status;
          break;
      }
    } else if (message.type === 'agent' || message.type === 'user') {
      console.log(
        `[CallingPanel] WebSocket transcript update received [${message.type}]: ${message.text}`,
      );
      this.clearFallbackTimer(); // Receiving voice content proves the call is connected/active
      if (this.callStatus !== 'Connected') {
        this.callStatus = 'Connected';
        this.startTimer();
      }
      this.liveSubtitle = message.text;
      this.liveSubtitleType = message.type;
    }
  }

  private startTimer(): void {
    this.clearTimer();
    this.timerInterval = setInterval(() => {
      this.callTimer++;
    }, 1000);
  }

  private clearTimer(): void {
    if (this.timerInterval) {
      clearInterval(this.timerInterval);
      this.timerInterval = null;
    }
  }

  formatTimer(seconds: number): string {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
}
