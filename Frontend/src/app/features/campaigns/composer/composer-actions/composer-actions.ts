import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-composer-actions',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './composer-actions.html',
  styleUrl: './composer-actions.scss',
})
export class ComposerActions {
  @Input() isPublishing = false;

  @Input() publishMode: 'now' | 'schedule' = 'now';

  @Output() publish = new EventEmitter<void>();

  @Output() saveDraft = new EventEmitter<void>();

  @Output() cancel = new EventEmitter<void>();

  onPublish(): void {
    this.publish.emit();
  }

  onSaveDraft(): void {
    this.saveDraft.emit();
  }

  onCancel(): void {
    this.cancel.emit();
  }
}
