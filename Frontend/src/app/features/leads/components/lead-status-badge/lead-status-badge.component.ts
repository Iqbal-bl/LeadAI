import { Component, Input } from '@angular/core';

import { SharedModule } from '../../../../shared/shared.module';

@Component({
  selector: 'app-lead-status-badge',
  standalone: true,
  imports: [SharedModule],
  template: `
    <p-tag [value]="status" [severity]="getSeverity()" [rounded]="true" [style]="{ 'font-size': '0.8rem', 'padding': '0.35rem 0.75rem' }"></p-tag>
  `
})
export class LeadStatusBadgeComponent {
  @Input() status: string = 'New';

  getSeverity(): "success" | "secondary" | "info" | "warn" | "danger" | "contrast" | undefined {
    const map: Record<string, "success" | "secondary" | "info" | "warn" | "danger" | "contrast"> = {
      'New': 'info', 'Assigned': 'secondary', 'Follow-up': 'warn',
      'Interested': 'success', 'Negotiation': 'contrast',
      'Won': 'success', 'Lost': 'danger', 'Closed': 'secondary',
    };
    return map[this.status] || 'info';
  }
}
