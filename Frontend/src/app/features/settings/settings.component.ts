import { Component, OnInit } from '@angular/core';
import { ThemeService } from '../../shared/services/theme.service';
import { AuthService } from '../../services/auth.service';
import { CompanyService } from '../../services/company.service';
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
  companySettings!: CompanySettings;

  constructor(
    public themeService: ThemeService,
    private authService: AuthService,
    private companyService: CompanyService,
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
        }
      },
    });
  }

  private loadCompanySettings(): void {
    if (this.companyId) {
      this.companyService.getCompanySettings(this.companyId).subscribe({
        next: (settings) => {
          this.companySettings = settings;
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
      this.companySettings.handoff_threshold =
        this.aiConfig.confidenceThreshold;
      this.companySettings.auto_assign_enabled = this.aiConfig.autoHandoff;
      this.companySettings.widget_enabled = this.aiConfig.enableSentiment;

      this.companyService
        .updateCompanySettings(this.companyId, this.companySettings)
        .subscribe({
          next: (updated) => {
            console.log('AI configuration updated successfully', updated);
          },
          error: (err) => {
            console.error('Failed to update AI configuration', err);
          },
        });
    }
  }
}
