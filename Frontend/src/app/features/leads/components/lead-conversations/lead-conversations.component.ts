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
