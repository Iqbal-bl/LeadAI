import { Component, Input, Output, EventEmitter } from '@angular/core';

@Component({
  selector: 'app-empty-state',
  standalone: false,
  template: `
    <div class="py-12 px-4 text-center">
      <div class="w-16 h-16 rounded-2xl flex items-center justify-center mx-auto mb-4"
           style="background: var(--app-bg); border: 1px solid var(--card-border);">
        <i [class]="icon" class="text-2xl" style="color: var(--app-text-muted);"></i>
      </div>
      <h3 class="text-base font-semibold" style="color: var(--app-text);">{{ title }}</h3>
      <p class="text-xs mt-1 max-w-sm mx-auto" style="color: var(--app-text-muted);">{{ description }}</p>
      <div *ngIf="actionLabel" class="mt-4">
        <p-button [label]="actionLabel" [icon]="actionIcon" size="small" (click)="action.emit()"
                  [style]="{ background: 'linear-gradient(135deg, #6366f1, #8b5cf6)', border: 'none' }"></p-button>
      </div>
    </div>
  `
})
export class EmptyStateComponent {
  @Input() icon: string = 'pi pi-inbox';
  @Input() title: string = 'No Data Found';
  @Input() description: string = 'There are no items to display at this time.';
  @Input() actionLabel: string = '';
  @Input() actionIcon: string = 'pi pi-plus';
  @Output() action = new EventEmitter<void>();
}
