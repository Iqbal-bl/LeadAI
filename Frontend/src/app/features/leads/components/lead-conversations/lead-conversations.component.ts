import {
  Component,
  Input,
  Output,
  EventEmitter,
  OnChanges,
  SimpleChanges,
  ViewChild,
  ElementRef,
} from '@angular/core';
import { SharedModule } from '../../../../shared/shared.module';

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

  @Output() sendReply = new EventEmitter<string>();
  @Output() previewTranscript = new EventEmitter<any>();

  replyMessage = '';
  displayFullSize = false;
  isMinimized = false;

  @ViewChild('chatContainer') chatContainer!: ElementRef;

  maximizeChat(): void {
    this.displayFullSize = true;
  }

  minimizeChat(): void {
    this.isMinimized = !this.isMinimized;
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['conversations']) {
      this.scrollToBottom();
    }
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

  isCustomer(msg: any): boolean {
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

  isAgent(msg: any): boolean {
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

  getSenderLabel(msg: any): string {
    if (this.isCustomer(msg)) {
      return msg.leadName || 'Customer';
    }
    if (this.isAgent(msg)) {
      return 'Agent';
    }
    return 'AI Assistant';
  }

  isAi(msg: any): boolean {
    if (!msg) return false;
    return (
      !this.isCustomer(msg) &&
      !this.isAgent(msg) &&
      msg.sender !== 'system'
    );
  }

  getConfidencePercent(val: any): number | null {
    if (val === null || val === undefined || val === '') return null;
    const num = Number(val);
    if (isNaN(num)) return null;
    return num <= 1 ? Math.round(num * 100) : Math.round(num);
  }

  getConfidenceBadgeClass(val: any): string {
    const pct = this.getConfidencePercent(val);
    if (pct === null) return '';
    if (pct >= 75) {
      return 'text-emerald-700 bg-emerald-50 dark:bg-emerald-950/60 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800';
    }
    if (pct >= 40) {
      return 'text-amber-700 bg-amber-50 dark:bg-amber-950/60 dark:text-amber-300 border border-amber-200 dark:border-amber-800';
    }
    return 'text-rose-700 bg-rose-50 dark:bg-rose-950/60 dark:text-rose-300 border border-rose-200 dark:border-rose-800';
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

  getStatusColor(summary: string): string {
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
}
