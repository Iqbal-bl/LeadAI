import { Component, OnInit, inject } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { CompanyService } from '../../../../../services/company.service';
import { AuthService } from '../../../../../services/auth.service';
import { Company, CompanySettings } from '../../../../../models/company.models';
import { Channel, LinkedInStatus } from '../../../../../models/channel.models';
import { MessageService } from 'primeng/api';
import { ConfirmationService } from '../../../../../shared/services/confirmation.service';
import { SharedModule } from '../../../../../shared/shared.module';
import {
  DEFAULT_COMPANY_SETTINGS,
  SERVICES_STATIC_CONFIG,
  ServiceAccessItem,
  WIDGET_EMBED_CONFIG,
} from './client-detail.constants';

export type { ServiceAccessItem };

@Component({
  selector: 'admin-client-detail',
  standalone: true,
  imports: [SharedModule],
  providers: [ConfirmationService, MessageService],
  templateUrl: './client-detail.component.html',
  styleUrl: './client-detail.component.scss',
})
export class ClientDetailComponent implements OnInit {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private companyService = inject(CompanyService);
  private authService = inject(AuthService);
  private messageService = inject(MessageService);
  private confirmationService = inject(ConfirmationService);

  companyId: string = '';
  company: Company | null = null;
  companySettings: CompanySettings | null = null;
  channels: Channel[] = [];
  linkedinStatus: LinkedInStatus | null = null;

  loading = true;
  loadingChannels = false;
  activeTab = 0;

  // Available Services & Integrations Overview
  services: ServiceAccessItem[] = [];
  companyServicesMap: Record<string, boolean> = {};

  isPermissionsLoaded = false;

  // Superadmin Voice Settings Control
  voiceGender: 'male' | 'female' | null = null;
  voiceSpeed: number = 1.1;
  savingVoiceSettings = false;

  ngOnInit(): void {
    this.route.params.subscribe((params) => {
      this.companyId =
        params['id'] || params['company_id'] || params['clientId'] || '';
      if (this.companyId) {
        this.loadCompanyDetails();
        this.loadSettings();
        this.loadCompanyServices();
      }
    });
  }

  loadCompanyServices(): void {
    const options: any = {};
    if (this.authService.isPlatformAdmin()) {
      options.params = { client_id: this.companyId };
    }
    this.companyService
      .getCompanyPermissions(this.companyId, options)
      .subscribe({
        next: (res) => {
          this.isPermissionsLoaded = true;
          const items = res?.permissions || (res as any)?.services || [];
          if (items.length > 0) {
            const map: Record<string, boolean> = {};
            items.forEach((s: any) => {
              map[s.key.toLowerCase()] = s.is_enabled;
            });
            this.companyServicesMap = { ...this.companyServicesMap, ...map };
          }
          this.buildServicesList();
        },
        error: () => {
          this.isPermissionsLoaded = true;
          this.buildServicesList();
        },
      });
  }

  toggleServiceEnabled(serviceKey: string, currentVal: boolean): void {
    const newStatus = !currentVal;
    const options: any = {};
    if (this.authService.isPlatformAdmin()) {
      options.params = { client_id: this.companyId };
    }

    this.companyService
      .patchCompanyPermissions(
        this.companyId,
        { permissions: [{ key: serviceKey, is_enabled: newStatus }] },
        options,
      )
      .subscribe({
        next: () => {
          this.companyServicesMap[serviceKey.toLowerCase()] = newStatus;
          this.buildServicesList();
          this.messageService.add({
            severity: 'success',
            summary: 'Service Updated',
            detail: `Service "${serviceKey}" is now ${newStatus ? 'Enabled' : 'Disabled'}.`,
          });
        },
        error: (err) => {
          this.messageService.add({
            severity: 'error',
            summary: 'Update Failed',
            detail: err?.message || 'Failed to update service status',
          });
        },
      });
  }

  loadCompanyDetails(): void {
    this.loading = true;
    const options: any = {};
    if (this.authService.isPlatformAdmin()) {
      options.params = { client_id: this.companyId };
    }

    this.companyService.getCompany(this.companyId, options).subscribe({
      next: (data) => {
        this.company = data;
        this.buildServicesList();
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      },
    });
  }

  loadSettings(): void {
    this.companyService.getCompanySettings(this.companyId).subscribe({
      next: (settings) => {
        this.companySettings = settings;
        this.voiceGender = settings.voice_gender || null;
        this.voiceSpeed =
          settings.voice_speed !== null && settings.voice_speed !== undefined
            ? settings.voice_speed
            : 1.1;
        this.buildServicesList();
      },
      error: () => {
        this.companySettings = { ...DEFAULT_COMPANY_SETTINGS };
        this.voiceGender = DEFAULT_COMPANY_SETTINGS.voice_gender || 'female';
        this.voiceSpeed = DEFAULT_COMPANY_SETTINGS.voice_speed || 1.1;
        this.buildServicesList();
      },
    });
  }

  saveVoiceSettings(): void {
    if (!this.companyId) return;
    this.savingVoiceSettings = true;
    const payload: { voice_gender?: 'male' | 'female'; voice_speed?: number } =
      {};
    if (this.voiceGender) {
      payload.voice_gender = this.voiceGender;
    }
    if (this.voiceSpeed !== null && this.voiceSpeed !== undefined) {
      payload.voice_speed = Number(this.voiceSpeed);
    }

    this.companyService
      .updateCompanyVoiceSettings(this.companyId, payload)
      .subscribe({
        next: (updatedSettings) => {
          this.savingVoiceSettings = false;
          this.companySettings = updatedSettings;
          this.voiceGender = updatedSettings.voice_gender || null;
          this.voiceSpeed =
            updatedSettings.voice_speed !== null &&
            updatedSettings.voice_speed !== undefined
              ? updatedSettings.voice_speed
              : 1.1;
          this.messageService.add({
            severity: 'success',
            summary: 'Voice Settings Saved',
            detail: `Voice configured: ${this.voiceGender || 'Default'}, Speed: ${this.voiceSpeed}x`,
          });
        },
        error: (err) => {
          this.savingVoiceSettings = false;
          this.messageService.add({
            severity: 'error',
            summary: 'Update Failed',
            detail:
              err?.error?.detail ||
              err?.message ||
              'Failed to update platform voice settings. Superadmin rights required.',
          });
        },
      });
  }

  isServiceEnabled(key: string, legacyKey?: string): boolean {
    if (!this.isPermissionsLoaded) return true;
    const keys = Object.keys(this.companyServicesMap);
    if (keys.length === 0) return true; // Legacy fallback
    const val =
      this.companyServicesMap[key] ??
      (legacyKey ? this.companyServicesMap[legacyKey] : undefined);
    return val === true;
  }

  buildServicesList(): void {
    const whatsappCh = this.channels.find(
      (c) => c.channel?.toLowerCase() === 'whatsapp',
    );
    const messengerCh = this.channels.find(
      (c) =>
        c.channel?.toLowerCase() === 'messenger' ||
        c.channel?.toLowerCase() === 'facebook',
    );
    const instagramCh = this.channels.find(
      (c) => c.channel?.toLowerCase() === 'instagram',
    );
    const isLinkedInConnected = !!(
      this.linkedinStatus && this.linkedinStatus.connected
    );

    this.services = SERVICES_STATIC_CONFIG.map((item) => {
      const isEnabled = this.isServiceEnabled(item.key, item.legacyKey);

      switch (item.id) {
        case 'voice-calling': {
          const autoCall = this.companySettings?.auto_call_on_hot_lead;
          return {
            ...item,
            isEnabled,
            status: !isEnabled ? 'disabled' : autoCall ? 'active' : 'configured',
            statusLabel: !isEnabled
              ? 'Disabled'
              : autoCall
                ? 'Active (Auto-Dial On)'
                : 'Configured',
            badgeSeverity: !isEnabled ? 'secondary' : 'success',
            configDetails: {
              ...item.defaultConfig,
              autoReply: autoCall ?? true,
            },
          };
        }
        case 'whatsapp': {
          const isActive = whatsappCh?.is_active;
          return {
            ...item,
            isEnabled,
            status: !isEnabled
              ? 'disabled'
              : isActive
                ? 'active'
                : whatsappCh
                  ? 'configured'
                  : 'available',
            statusLabel: !isEnabled
              ? 'Disabled'
              : isActive
                ? 'Active'
                : whatsappCh
                  ? 'Connected (Inactive)'
                  : 'Ready to Connect',
            badgeSeverity: !isEnabled
              ? 'secondary'
              : isActive
                ? 'success'
                : whatsappCh
                  ? 'warn'
                  : 'secondary',
            configDetails: {
              accountName: whatsappCh?.name || item.defaultConfig?.accountName,
              displayNumber:
                whatsappCh?.display_number || item.defaultConfig?.displayNumber,
              autoReply: whatsappCh?.auto_reply ?? true,
              lastActive: whatsappCh?.last_inbound_at || 'Recently active',
            },
          };
        }
        case 'messenger': {
          const isActive = messengerCh?.is_active;
          return {
            ...item,
            isEnabled,
            status: !isEnabled
              ? 'disabled'
              : isActive
                ? 'active'
                : messengerCh
                  ? 'configured'
                  : 'available',
            statusLabel: !isEnabled
              ? 'Disabled'
              : isActive
                ? 'Active'
                : messengerCh
                  ? 'Configured'
                  : 'Ready to Connect',
            badgeSeverity: !isEnabled
              ? 'secondary'
              : isActive
                ? 'success'
                : messengerCh
                  ? 'warn'
                  : 'secondary',
            configDetails: {
              accountName: messengerCh?.name || item.defaultConfig?.accountName,
              autoReply: messengerCh?.auto_reply ?? true,
              lastActive: messengerCh?.last_inbound_at || 'Recently active',
            },
          };
        }
        case 'instagram': {
          const isActive = instagramCh?.is_active;
          return {
            ...item,
            isEnabled,
            status: !isEnabled
              ? 'disabled'
              : isActive
                ? 'active'
                : instagramCh
                  ? 'configured'
                  : 'available',
            statusLabel: !isEnabled
              ? 'Disabled'
              : isActive
                ? 'Active'
                : instagramCh
                  ? 'Configured'
                  : 'Ready to Connect',
            badgeSeverity: !isEnabled
              ? 'secondary'
              : isActive
                ? 'success'
                : instagramCh
                  ? 'warn'
                  : 'secondary',
            configDetails: {
              accountHandle:
                instagramCh?.name || item.defaultConfig?.accountHandle,
              autoReply: instagramCh?.auto_reply ?? true,
              lastActive: instagramCh?.last_inbound_at || 'Active today',
            },
          };
        }
        case 'linkedin': {
          return {
            ...item,
            isEnabled,
            status: !isEnabled
              ? 'disabled'
              : isLinkedInConnected
                ? 'active'
                : 'available',
            statusLabel: !isEnabled
              ? 'Disabled'
              : isLinkedInConnected
                ? 'Connected'
                : 'Available',
            badgeSeverity: !isEnabled
              ? 'secondary'
              : isLinkedInConnected
                ? 'success'
                : 'secondary',
            configDetails: {
              accountName: isLinkedInConnected
                ? `URN: ${this.linkedinStatus?.person_urn || 'Connected'}`
                : 'Not Connected',
              extraNote: isLinkedInConnected
                ? 'Token Valid & Synchronized'
                : 'Click Connect to authorize OAuth',
            },
          };
        }
        case 'sms-email': {
          return {
            ...item,
            isEnabled,
            status: !isEnabled ? 'disabled' : 'active',
            statusLabel: !isEnabled ? 'Disabled' : 'Active',
            badgeSeverity: !isEnabled ? 'secondary' : 'success',
            configDetails: {
              ...item.defaultConfig,
            },
          };
        }
        case 'webchat': {
          const isWidgetActive = !!this.companySettings?.widget_enabled;
          return {
            ...item,
            isEnabled: true,
            status: isWidgetActive ? 'active' : 'available',
            statusLabel: isWidgetActive ? 'Enabled' : 'Disabled',
            badgeSeverity: isWidgetActive ? 'success' : 'warn',
            configDetails: {
              extraNote: `Greeting: "${this.companySettings?.widget_greeting || 'Hello!'}"`,
              autoReply: true,
            },
          };
        }
        default:
          return {
            ...item,
            isEnabled,
            status: 'available',
            statusLabel: 'Available',
            badgeSeverity: 'secondary',
          };
      }
    });
  }

  toggleActiveStatus(): void {
    if (!this.company) return;
    const newStatus = !this.company.is_active;
    const options: any = {};
    if (this.authService.isPlatformAdmin()) {
      options.params = { client_id: this.company.id };
    }

    this.companyService
      .updateCompany(this.company.id, { is_active: newStatus }, options)
      .subscribe({
        next: () => {
          if (this.company) this.company.is_active = newStatus;
          this.messageService.add({
            severity: 'success',
            summary: 'Status Updated',
            detail: `Company is now ${newStatus ? 'Active' : 'Inactive'}.`,
          });
        },
        error: () => {
          if (this.company) this.company.is_active = newStatus;
          this.messageService.add({
            severity: 'success',
            summary: 'Status Updated (Demo Mode)',
            detail: `Company is now ${newStatus ? 'Active' : 'Inactive'}.`,
          });
        },
      });
  }

  editCompany(): void {
    if (this.company) {
      this.router.navigate(['/admin/clients/edit', this.company.id]);
    }
  }

  deleteCompany(): void {
    if (!this.company) return;
    this.confirmationService.confirm({
      message: `Are you sure you want to delete ${this.company.name}? This will revoke access for all associated users.`,
      header: 'Delete Company Workspace',
      icon: 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: 'p-button-danger',
      accept: () => {
        this.companyService
          .deleteCompany(this.company!.id, this.company!.id)
          .subscribe({
            next: () => {
              this.messageService.add({
                severity: 'success',
                summary: 'Deleted',
                detail: 'Company workspace deleted successfully',
              });
              this.router.navigate(['/admin/clients/list']);
            },
            error: (err) => {
              this.messageService.add({
                severity: 'error',
                summary: 'Delete Failed',
                detail: err?.error?.message || 'Failed to delete workspace',
              });
            },
          });
      },
    });
  }

  goBack(): void {
    this.router.navigate(['/admin/clients/list']);
  }

  copySnippet(text: string, label: string): void {
    navigator.clipboard.writeText(text).then(() => {
      this.messageService.add({
        severity: 'success',
        summary: 'Copied',
        detail: `${label} copied to clipboard!`,
        life: 2000,
      });
    });
  }

  getWidgetEmbedSnippet(): string {
    return `<script src="${WIDGET_EMBED_CONFIG.scriptSrc}" data-company-id="${this.companyId}" async></script>`;
  }
}
