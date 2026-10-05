import {
  Component,
  Input,
  Output,
  EventEmitter,
  OnChanges,
  SimpleChanges,
  ViewChild,
  ElementRef,
  inject,
} from '@angular/core';
import { SharedModule } from '../../../../shared/shared.module';
import { VoiceService } from '../../../../services/voice.service';
import { CallRecording } from '../../../../models/voice.models';

export interface ProcessedMessage {
  raw: any;
  id: any;
  sender: string;
  summary: string;
  startTime: any;
  statusColor: string;
  isCustomer: boolean;
  isAgent: boolean;
  isAi: boolean;
  senderLabel: string;
  bubbleBg: string;
  textColor: string;
  bubbleBorder: string;
  roundedClass: string;
  headerTextColor: string;
  confidencePercent: number | null;
  confidenceBadgeClass: string;
  confidenceTooltip: string;
  callSid: string | null;
  isCallLastEvent: boolean;
  deliveryStatus: string | null;
  deliveryError?: string;
}

export interface CallRecordingCardState {
  callSid: string;
  loading: boolean;
  recording: CallRecording | null | undefined; // undefined = not loaded, null = none (404), object = loaded
  error: string | null;
  placedTime: string;
  endReason: string;
  subtitle: string;
  expanded: boolean;
}

export interface CallMetadataInfo {
  status?: string;
  duration?: string | number;
  phone?: string;
  language?: string;
  initiatedBy?: string;
  leadStatus?: string;
  leadScore?: number | null;
}

@Component({
  selector: 'app-lead-conversations',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './lead-conversations.component.html',
  styleUrl: './lead-conversations.component.scss',
})
export class LeadConversationsComponent implements OnChanges {
  @Input() conversations: any[] = [];
  @Input() sendingReply = false;
  @Input() showReplyInput = true;
  @Input() replyPlaceholder = 'Type your response to customer...';
  @Input() title = 'Conversations';
  @Input() subtitle = '';
  @Input() icon = 'pi pi-comments';
  @Input() showHeader = true;
  @Input() showMaximize = true;
  @Input() cardMode = true;
  @Input() maxHeight = '380px';
  @Input() recipientName = '';
  @Input() recordingUrl?: string | null;
  @Input() emptyMessage = 'No messages in this conversation yet.';
  @Input() callMetadata?: CallMetadataInfo | null;

  @Output() sendReply = new EventEmitter<string>();
  @Output() previewTranscript = new EventEmitter<any>();

  private voiceService = inject(VoiceService);

  replyMessage = '';
  displayFullSize = false;
  isMinimized = false;

  processedMessages: ProcessedMessage[] = [];
  callRecordingsMap: Record<string, CallRecordingCardState> = {};
  callSids: string[] = [];

  @ViewChild('chatContainer') chatContainer!: ElementRef;

  maximizeChat(): void {
    this.displayFullSize = true;
  }

  minimizeChat(): void {
    this.isMinimized = !this.isMinimized;
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['conversations'] || changes['recordingUrl'] || changes['recipientName']) {
      this.processConversations();
      this.scrollToBottom();
    }
  }

  private processConversations(): void {
    const rawList = this.conversations || [];

    // 1. Identify distinct call SIDs from messages with call_sid / callSid
    const detectedSids = [
      ...new Set(
        rawList
          .filter((m) => !!(m.call_sid || m.callSid))
          .map((m) => (m.call_sid || m.callSid) as string),
      ),
    ];
    this.callSids = detectedSids;

    // 2. Build or update CallRecordingCardState for each distinct call_sid
    detectedSids.forEach((sid) => {
      const msgsForSid = rawList.filter(
        (m) => (m.call_sid || m.callSid) === sid,
      );
      const systemMsgs = msgsForSid.filter((m) => m.sender === 'system');

      let placedTime = '';
      let endReason = '';

      if (systemMsgs.length > 0) {
        const first = systemMsgs[0];
        const dateVal = first.startTime || first.created_at || first.timestamp;
        placedTime = dateVal ? this.formatTime(dateVal) : 'Placed';

        if (systemMsgs.length > 1) {
          endReason = systemMsgs[systemMsgs.length - 1].summary || '';
        } else {
          endReason = first.summary || 'Call event';
        }
      } else if (msgsForSid.length > 0) {
        const first = msgsForSid[0];
        const dateVal = first.startTime || first.created_at || first.timestamp;
        placedTime = dateVal ? this.formatTime(dateVal) : 'Placed';
        endReason = first.summary || 'Call event';
      }

      const subtitle = placedTime && endReason
        ? `${placedTime} → ${endReason}`
        : placedTime || endReason || 'Voice call';

      const explicitRecordingUrl =
        this.recordingUrl ||
        msgsForSid.find((m) => m.recording_url || m.recordingUrl)?.recording_url ||
        msgsForSid.find((m) => m.recording_url || m.recordingUrl)?.recordingUrl;

      if (!this.callRecordingsMap[sid]) {
        this.callRecordingsMap[sid] = {
          callSid: sid,
          loading: false,
          recording: explicitRecordingUrl
            ? ({ url: explicitRecordingUrl, call_sid: sid, expires_in_seconds: 3600 } as any)
            : undefined,
          error: null,
          placedTime,
          endReason,
          subtitle,
          expanded: !!explicitRecordingUrl,
        };
      } else {
        // Update labels while preserving loaded recording state
        this.callRecordingsMap[sid].placedTime = placedTime;
        this.callRecordingsMap[sid].endReason = endReason;
        this.callRecordingsMap[sid].subtitle = subtitle;
        if (explicitRecordingUrl && !this.callRecordingsMap[sid].recording) {
          this.callRecordingsMap[sid].recording = {
            url: explicitRecordingUrl,
            call_sid: sid,
            expires_in_seconds: 3600,
          } as any;
          this.callRecordingsMap[sid].expanded = true;
        }
      }
    });

    // Determine the last message index for each callSid so we can render the recording card at the event
    const lastMsgIndexForSid: Record<string, number> = {};
    rawList.forEach((m, idx) => {
      const sid = m.call_sid || m.callSid;
      if (sid) {
        lastMsgIndexForSid[sid] = idx;
      }
    });

    // 3. Precompute processed message properties to eliminate function calls in the template
    this.processedMessages = rawList.map((msg, idx) => {
      const sid = msg.call_sid || msg.callSid || null;
      const isCust = this.computeIsCustomer(msg);
      const isAg = this.computeIsAgent(msg);
      const isAIAssistant = !isCust && !isAg && msg.sender !== 'system';
      const confPct = this.computeConfidencePercent(msg.confidence);
      const confBadgeClass = this.computeConfidenceBadgeClass(confPct);
      const confTooltip =
        confPct !== null
          ? `AI Confidence: ${confPct}%${msg.model_used ? ' (' + msg.model_used + ')' : ''}`
          : '';

      const summaryText = msg.summary || msg.content || msg.text || msg.message || '';
      const statusCol = this.computeStatusColor(summaryText);

      return {
        raw: msg,
        id: msg.id || idx + 1,
        sender: msg.sender || 'ai',
        summary: summaryText,
        startTime: msg.startTime || msg.created_at || msg.timestamp,
        statusColor: statusCol,
        isCustomer: isCust,
        isAgent: isAg,
        isAi: isAIAssistant,
        senderLabel: isCust ? (msg.leadName || this.recipientName || 'Customer') : isAg ? 'Agent' : 'AI Assistant',
        bubbleBg: isCust ? '#6366f1' : 'var(--card-bg)',
        textColor: isCust ? '#ffffff' : 'var(--app-text)',
        bubbleBorder: isCust ? 'none' : '1px solid var(--app-border)',
        roundedClass: isCust ? 'rounded-tl-none' : 'rounded-tr-none',
        headerTextColor: isCust ? '#e0e7ff' : 'var(--app-text-muted)',
        confidencePercent: confPct,
        confidenceBadgeClass: confBadgeClass,
        confidenceTooltip: confTooltip,
        callSid: sid,
        isCallLastEvent: !!(sid && lastMsgIndexForSid[sid] === idx),
        deliveryStatus: msg.delivery_status || msg.deliveryStatus || null,
        deliveryError: msg.delivery_error || msg.deliveryError || undefined,
      };
    });
  }

  // ── Recording Player Management ──

  public loadRecording(callSid: string, force = false): void {
    if (!callSid) return;
    const state = this.callRecordingsMap[callSid];
    if (!state) return;

    if (!force && state.recording !== undefined && !state.error) {
      state.expanded = !state.expanded;
      return;
    }

    state.loading = true;
    state.error = null;
    state.expanded = true;

    this.voiceService.getCallRecording(callSid).subscribe({
      next: (recording) => {
        state.loading = false;
        state.recording = recording; // null if 404 (unanswered / no recording), object if found
      },
      error: (err) => {
        state.loading = false;
        state.error = err?.message || 'Failed to load recording.';
      },
    });
  }

  public toggleRecordingExpand(callSid: string): void {
    const state = this.callRecordingsMap[callSid];
    if (!state) return;

    if (state.recording === undefined) {
      // Lazy load on first expand
      this.loadRecording(callSid);
    } else {
      state.expanded = !state.expanded;
    }
  }

  public onAudioError(callSid: string): void {
    // When signed MinIO link expires, re-fetch fresh signed URL rather than failing
    const state = this.callRecordingsMap[callSid];
    if (!state) return;

    state.loading = true;
    this.voiceService.getCallRecording(callSid).subscribe({
      next: (recording) => {
        state.loading = false;
        state.recording = recording;
      },
      error: () => {
        state.loading = false;
        state.error = 'Recording link expired and could not be renewed.';
      },
    });
  }

  // ── Helpers ──

  private formatTime(val: any): string {
    try {
      const d = new Date(val);
      if (isNaN(d.getTime())) return '';
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  }

  private computeIsCustomer(msg: any): boolean {
    if (!msg) return false;
    const sender = (msg.sender || '').toLowerCase().trim();
    const type = (msg.type || '').toLowerCase().trim();
    const role = (msg.role || '').toLowerCase().trim();
    return (
      sender === 'customer' ||
      sender === 'user' ||
      sender === 'human' ||
      sender === 'lead' ||
      type === 'human' ||
      type === 'customer' ||
      type === 'user' ||
      role === 'customer' ||
      role === 'user'
    );
  }

  private computeIsAgent(msg: any): boolean {
    if (!msg) return false;
    const sender = (msg.sender || '').toLowerCase().trim();
    const agent = (msg.agent || '').toLowerCase().trim();
    return (
      sender === 'agent' ||
      sender === 'staff' ||
      agent === 'agent' ||
      agent === 'staff'
    );
  }

  private computeConfidencePercent(val: any): number | null {
    if (val === null || val === undefined || val === '') return null;
    const num = Number(val);
    if (isNaN(num)) return null;
    return num <= 1 ? Math.round(num * 100) : Math.round(num);
  }

  private computeConfidenceBadgeClass(pct: number | null): string {
    if (pct === null) return '';
    if (pct >= 75) {
      return 'text-emerald-700 bg-emerald-50 dark:bg-emerald-950/60 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800';
    }
    if (pct >= 40) {
      return 'text-amber-700 bg-amber-50 dark:bg-amber-950/60 dark:text-amber-300 border border-amber-200 dark:border-amber-800';
    }
    return 'text-rose-700 bg-rose-50 dark:bg-rose-950/60 dark:text-rose-300 border border-rose-200 dark:border-rose-800';
  }

  private computeStatusColor(summary: string): string {
    const text = (summary || '').toLowerCase();
    if (
      text.includes('completed') ||
      text.includes('connected') ||
      text.includes('answered') ||
      text.includes('in progress')
    ) {
      return '#10b981'; // Green
    }
    if (text.includes('ringing')) {
      return '#f59e0b'; // Amber
    }
    if (
      text.includes('initiated') ||
      text.includes('calling') ||
      text.includes('dialing')
    ) {
      return '#3b82f6'; // Blue
    }
    if (
      text.includes('failed') ||
      text.includes('busy') ||
      text.includes('unanswered') ||
      text.includes('no-answer')
    ) {
      return '#ef4444'; // Red
    }
    return '#6366f1'; // Indigo
  }

  onSendReply(): void {
    const text = this.replyMessage.trim();
    if (!text) return;
    this.sendReply.emit(text);
    this.replyMessage = '';
  }

  onPreviewTranscript(msg: any): void {
    this.previewTranscript.emit(msg);
  }

  scrollToBottom(): void {
    try {
      setTimeout(() => {
        if (this.chatContainer) {
          const el = this.chatContainer.nativeElement;
          el.scrollTop = el.scrollHeight;
        }
      }, 100);
    } catch (err) {}
  }
}
