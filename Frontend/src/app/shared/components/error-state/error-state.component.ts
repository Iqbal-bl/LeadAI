import { Component, Input, Output, EventEmitter } from '@angular/core';

@Component({
  selector: 'app-error-state',
  standalone: false,
  template: `
    <div class="py-12 px-4 text-center">
      <div class="w-16 h-16 rounded-2xl bg-danger-50 dark:bg-danger-500/10 flex items-center justify-center mx-auto mb-4 border border-danger-200 dark:border-danger-800">
        <i class="pi pi-exclamation-triangle text-2xl text-danger-500"></i>
      </div>
      <h3 class="text-base font-semibold" style="color: var(--app-text);">{{ title }}</h3>
      <p class="text-xs mt-1 max-w-sm mx-auto" style="color: var(--app-text-muted);">{{ message }}</p>
      <div class="mt-4">
        <p-button label="Retry Connection" icon="pi pi-refresh" severity="danger" [outlined]="true" size="small" (click)="retry.emit()"></p-button>
      </div>
    </div>
  `
})
export class ErrorStateComponent {
  @Input() title: string = 'Something Went Wrong';
  @Input() message: string = 'Unable to fetch data from server. Please check your connection and try again.';
  @Output() retry = new EventEmitter<void>();
}
