import { Component, OnInit } from '@angular/core';
import { forkJoin } from 'rxjs';
import { ThemeService } from '../../shared/services/theme.service';
import { AuthService } from '../../services/auth.service';
import { CompanyService } from '../../services/company.service';
import { RoleManagementService } from '../../services/role-management.service';
import { ToastService } from '../../shared/services/toast.service';
import { CompanySettings } from '../../models/company.models';

import { SharedModule } from '../../shared/shared.module';
import { LeadThresholdComponent } from './lead-threshold/lead-threshold.component';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [SharedModule, LeadThresholdComponent],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.scss',
})
export class SettingsComponent implements OnInit {
  activeTab: string = 'profile';
  profile = {
    name: 'Sam Nakamura',
    email: 'sam.n@leadai.com',
    phone: '+1 (555) 666-7777',
    role: 'Admin',
    timeZone: 'EST (UTC-5)',
  };

  aiConfig = {
    model: 'LeadAI-Opus-v4',
    confidenceThreshold: 80,
    autoHandoff: true,
    maxDurationMinutes: 15,
    enableSentiment: true,
  };

  notificationConfig = {
    emailAlerts: true,
    pushNotifications: true,
    aiConfidenceAlerts: true,
    weeklyReport: true,
  };

  companyId: string | null = null;
  companySettings: CompanySettings | null = null;
  agentName: string = '';
  savingAiConfig = false;

  isCompanyAdmin = false;
  loadingPermissions = false;
  managerCanReveal: boolean | undefined = undefined;
  employeeCanReveal: boolean | undefined = undefined;
  savingManager = false;
  savingEmployee = false;

  constructor(
    public themeService: ThemeService,
    private authService: AuthService,
    private companyService: CompanyService,
    private roleManagementService: RoleManagementService,
    private toastService: ToastService,
  ) {}

  ngOnInit(): void {
    this.companyId = this.authService.getSelectedCompanyId();
    this.loadProfile();
    this.loadCompanySettings();
  }

  private loadProfile(): void {
    this.authService.currentUser$.subscribe({
      next: (user) => {
        if (user) {
          this.profile.name = user.full_name;
          this.profile.email = user.email;
          this.profile.role = user.role.toUpperCase();
          this.isCompanyAdmin = this.authService.isCompanyAdmin();
          if (this.isCompanyAdmin) {
            this.loadCompanyRolePermissions();
          }
        }
      },
    });
  }

  public loadCompanyRolePermissions(): void {
    this.loadingPermissions = true;
    forkJoin({
      manager: this.roleManagementService.getCompanyRolePermissions('manager'),
      employee:
        this.roleManagementService.getCompanyRolePermissions('employee'),
    }).subscribe({
      next: ({ manager, employee }) => {
        this.loadingPermissions = false;
        this.managerCanReveal =
          manager?.find((r) => r.permission_key === 'lead.reveal_pii')
            ?.is_granted ?? false;
        this.employeeCanReveal =
          employee?.find((r) => r.permission_key === 'lead.reveal_pii')
            ?.is_granted ?? false;
      },
      error: (err) => {
        this.loadingPermissions = false;
        console.warn('Failed to load company role permissions:', err);
      },
    });
  }

  public setRolePermission(
    role: 'manager' | 'employee',
    isGranted: boolean,
  ): void {
    if (role === 'manager') this.savingManager = true;
    if (role === 'employee') this.savingEmployee = true;

    this.roleManagementService
      .updateCompanyRolePermission(role, {
        permission_key: 'lead.reveal_pii',
        is_granted: isGranted,
      })
      .subscribe({
        next: (res) => {
          if (role === 'manager') {
            this.savingManager = false;
            this.managerCanReveal = res.is_granted;
          } else {
            this.savingEmployee = false;
            this.employeeCanReveal = res.is_granted;
          }
          this.toastService.success(
            `${role === 'manager' ? 'Managers' : 'Employees'} ${
              res.is_granted ? 'can now' : 'can no longer'
            } reveal customer contact details.`,
            'Permission Updated',
          );
        },
        error: (err) => {
          if (role === 'manager') this.savingManager = false;
          if (role === 'employee') this.savingEmployee = false;
          this.toastService.error(
            err?.error?.detail ||
              err?.message ||
              'Failed to update permission.',
            'Update Failed',
          );
        },
      });
  }

  private loadCompanySettings(): void {
    if (this.companyId) {
      this.companyService.getCompanySettings(this.companyId).subscribe({
        next: (settings) => {
          this.companySettings = settings;
          this.agentName = settings.agent_name || '';
          this.aiConfig.confidenceThreshold = settings.handoff_threshold;
          this.aiConfig.autoHandoff = settings.auto_assign_enabled;
          this.aiConfig.enableSentiment = settings.widget_enabled;
        },
        error: (err) => {
          console.error('Failed to load company settings', err);
        },
      });
    }
  }

  toggleTheme(): void {
    this.themeService.toggleTheme();
  }

  saveProfile(): void {
    console.log('Profile saved locally:', this.profile);
  }

  saveAiConfig(): void {
    if (this.companyId && this.companySettings) {
      this.savingAiConfig = true;
      const updatedSettings: CompanySettings = {
        ...this.companySettings,
        handoff_threshold: this.aiConfig.confidenceThreshold,
        auto_assign_enabled: this.aiConfig.autoHandoff,
        widget_enabled: this.aiConfig.enableSentiment,
        agent_name: this.agentName?.trim() ? this.agentName.trim() : null,
      };

      this.companyService
        .updateCompanySettings(this.companyId, updatedSettings)
        .subscribe({
          next: (updated) => {
            this.savingAiConfig = false;
            this.companySettings = updated;
            this.agentName = updated.agent_name || '';
            this.toastService.success(
              'Company settings and Agent Name updated successfully.',
              'Settings Saved',
            );
          },
          error: (err) => {
            this.savingAiConfig = false;
            this.toastService.error(
              err?.error?.detail ||
                err?.message ||
                'Failed to update company settings.',
              'Save Failed',
            );
          },
        });
    }
  }
}
