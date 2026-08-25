import { Component, Input } from '@angular/core';
import { Lead } from '../../../../models/lead.models';

import { SharedModule } from '../../../../shared/shared.module';

@Component({
  selector: 'app-contact-card',
  standalone: true,
  imports: [SharedModule],
  template: `
    <div class="card-base p-5">
      <h3 class="text-sm font-semibold mb-4 flex items-center gap-2" style="color: var(--app-text);">
        <i class="pi pi-id-card text-brand-500"></i> Contact Details
      </h3>
      <div class="space-y-3">
        <a class="flex items-center gap-3 text-sm p-2 rounded-lg hover:bg-black/5 dark:hover:bg-white/5 transition-colors cursor-pointer group">
          <div class="w-8 h-8 rounded-lg flex items-center justify-center bg-info-50 dark:bg-info-500/10">
            <i class="pi pi-phone text-info-500 text-xs"></i>
          </div>
          <div>
            <div class="text-[10px] uppercase tracking-wider font-medium" style="color: var(--app-text-muted);">Phone</div>
            <div class="text-sm font-medium" style="color: var(--app-text);">{{ lead.phone }}</div>
          </div>
        </a>
        <a class="flex items-center gap-3 text-sm p-2 rounded-lg hover:bg-black/5 dark:hover:bg-white/5 transition-colors cursor-pointer group">
          <div class="w-8 h-8 rounded-lg flex items-center justify-center bg-success-50 dark:bg-success-500/10">
            <i class="pi pi-envelope text-success-500 text-xs"></i>
          </div>
          <div>
            <div class="text-[10px] uppercase tracking-wider font-medium" style="color: var(--app-text-muted);">Email</div>
            <div class="text-sm font-medium" style="color: var(--app-text);">{{ lead.email }}</div>
          </div>
        </a>
        <a class="flex items-center gap-3 text-sm p-2 rounded-lg hover:bg-black/5 dark:hover:bg-white/5 transition-colors cursor-pointer group">
          <div class="w-8 h-8 rounded-lg flex items-center justify-center bg-warning-50 dark:bg-warning-500/10">
            <i class="pi pi-building text-warning-500 text-xs"></i>
          </div>
          <div>
            <div class="text-[10px] uppercase tracking-wider font-medium" style="color: var(--app-text-muted);">Company</div>
            <div class="text-sm font-medium" style="color: var(--app-text);">{{ lead.company }}</div>
          </div>
        </a>
        <a class="flex items-center gap-3 text-sm p-2 rounded-lg hover:bg-black/5 dark:hover:bg-white/5 transition-colors cursor-pointer group">
          <div class="w-8 h-8 rounded-lg flex items-center justify-center bg-brand-50 dark:bg-brand-500/10">
            <i class="pi pi-clock text-brand-500 text-xs"></i>
          </div>
          <div>
            <div class="text-[10px] uppercase tracking-wider font-medium" style="color: var(--app-text-muted);">Preferred Contact</div>
            <div class="text-sm font-medium" style="color: var(--app-text);">Weekdays, 9 AM - 5 PM EST</div>
          </div>
        </a>
      </div>
    </div>
  `
})
export class ContactCardComponent {
  @Input() lead!: Lead;
}
