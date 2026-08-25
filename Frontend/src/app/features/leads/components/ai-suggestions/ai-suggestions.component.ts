import { Component, Input } from '@angular/core';
import { AiSuggestion } from '../../../../models/lead.models';

import { SharedModule } from '../../../../shared/shared.module';

@Component({
  selector: 'app-ai-suggestions',
  standalone: true,
  imports: [SharedModule],
  template: `
    <div class="card-base p-5">
      <h3 class="text-sm font-semibold mb-4 flex items-center gap-2" style="color: var(--app-text);">
        <i class="pi pi-lightbulb text-warning-500"></i> AI Suggestions
      </h3>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div *ngFor="let s of suggestions"
             class="p-3 rounded-xl border transition-all duration-200 hover:shadow-card-hover cursor-pointer group"
             style="background: var(--card-bg); border-color: var(--card-border);">
          <div class="flex items-start gap-3">
            <div class="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5"
                 [ngClass]="{
                   'bg-danger-50 dark:bg-danger-500/10': s.priority === 'High',
                   'bg-warning-50 dark:bg-warning-500/10': s.priority === 'Medium',
                   'bg-info-50 dark:bg-info-500/10': s.priority === 'Low'
                 }">
              <i [class]="s.icon" class="text-sm"
                 [ngClass]="{
                   'text-danger-500': s.priority === 'High',
                   'text-warning-500': s.priority === 'Medium',
                   'text-info-500': s.priority === 'Low'
                 }"></i>
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center justify-between gap-2 mb-1">
                <span class="text-sm font-semibold" style="color: var(--app-text);">{{ s.title }}</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
                      [ngClass]="{
                        'bg-danger-50 text-danger-600 dark:bg-danger-500/10 dark:text-danger-400': s.priority === 'High',
                        'bg-warning-50 text-warning-600 dark:bg-warning-500/10 dark:text-warning-400': s.priority === 'Medium',
                        'bg-info-50 text-info-600 dark:bg-info-500/10 dark:text-info-400': s.priority === 'Low'
                      }">
                  {{ s.priority }}
                </span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--app-text-muted);">{{ s.description }}</p>
              <span class="text-[10px] uppercase tracking-wider font-medium mt-2 block" style="color: var(--app-text-muted);">
                {{ s.type }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  `
})
export class AiSuggestionsComponent {
  @Input() suggestions: AiSuggestion[] = [];
}
