import {
  Component,
  OnInit,
  Input,
  Output,
  EventEmitter,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { InputTextModule } from 'primeng/inputtext';
import { ButtonModule } from 'primeng/button';
import { TooltipModule } from 'primeng/tooltip';
import { AuthService } from '../../../../../services/auth.service';
import { CompanyService } from '../../../../../services/company.service';
import { ToastService } from '../../../../../shared/services/toast.service';

@Component({
  selector: 'app-persona-panel',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    InputTextModule,
    ButtonModule,
    TooltipModule,
  ],
  templateUrl: './persona-panel.component.html',
  styleUrls: ['./persona-panel.component.scss'],
})
export class PersonaPanelComponent implements OnInit {
  private authService = inject(AuthService);
  private companyService = inject(CompanyService);
  private toastService = inject(ToastService);

  @Input() companyName: string = '';
  @Input() agentName: string = '';
  @Output() agentSaved = new EventEmitter<string>();

  companyId: string | null = null;
  initialAgentName: string = '';
  loading = false;
  saving = false;

  ngOnInit(): void {
    this.companyId =
      this.authService.getSelectedCompanyId() ||
      this.authService.getCurrentUser()?.client_id ||
      null;

    this.resolveCompanyName();
    this.loadAgentName();
  }

  private resolveCompanyName(): void {
    if (!this.companyName) {
      const user = this.authService.getCurrentUser();
      if (user) {
        const match = user.accessible_companies?.find(
          (c) => c.id === this.companyId,
        );
        this.companyName = match?.name || user.client_name || '';
      }
    }

    if (!this.companyName && this.companyId) {
      this.companyService.getCompany(this.companyId).subscribe({
        next: (comp) => {
          if (comp?.name) {
            this.companyName = comp.name;
          }
        },
        error: () => {},
      });
    }
  }

  public loadAgentName(): void {
    if (!this.companyId) return;
    this.loading = true;
    this.companyService.getCompanySettings(this.companyId).subscribe({
      next: (settings) => {
        this.agentName = settings.agent_name || '';
        this.initialAgentName = this.agentName;
        this.loading = false;
      },
      error: (err) => {
        console.error('Failed to load company settings for persona panel', err);
        this.loading = false;
      },
    });
  }

  public saveAgentName(): void {
    if (!this.companyId) {
      this.toastService.error('Company workspace not found.', 'Error');
      return;
    }

    this.saving = true;
    const trimmed = (this.agentName || '').trim();

    this.companyService
      .updateCompanySettings(this.companyId, {
        agent_name: trimmed ? trimmed : null,
      })
      .subscribe({
        next: (updated) => {
          this.saving = false;
          this.agentName = updated.agent_name || '';
          this.initialAgentName = this.agentName;
          this.toastService.success(
            `Agent Name updated to "${this.agentName || 'Default'}". Every script and prompt using {agent} will use it automatically.`,
            'Persona Saved',
          );
          this.agentSaved.emit(this.agentName);
        },
        error: (err) => {
          this.saving = false;
          this.toastService.error(
            err?.error?.detail || err?.message || 'Failed to update agent name.',
            'Save Failed',
          );
        },
      });
  }
}
