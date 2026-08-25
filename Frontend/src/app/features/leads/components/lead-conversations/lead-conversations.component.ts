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
