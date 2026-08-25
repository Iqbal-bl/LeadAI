import { Component, Input, Output, EventEmitter } from '@angular/core';
import { PromptItem } from '../../prompt-editor.component';
import { SharedModule } from '../../../../../shared/shared.module';

@Component({
  selector: 'app-prompt-grid',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './prompt-grid.component.html'
})
export class PromptGridComponent {
  @Input() prompts: PromptItem[] = [];
  @Input() promptsLoading = false;

  @Output() onEdit = new EventEmitter<PromptItem>();
  @Output() onReset = new EventEmitter<{ prompt: PromptItem; event: Event }>();

  triggerReset(prompt: PromptItem, event: Event): void {
    this.onReset.emit({ prompt, event });
  }
}
