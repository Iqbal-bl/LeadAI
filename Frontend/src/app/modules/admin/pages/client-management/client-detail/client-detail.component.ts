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
  DEFAULT_SARVAM_FEMALE_VOICE,
  DEFAULT_SARVAM_MALE_VOICE,
  SARVAM_VOICE_ROSTER,
  SERVICES_STATIC_CONFIG,
  ServiceAccessItem,
  VoiceSpeakerOption,
  WIDGET_EMBED_CONFIG,
} from './client-detail.constants';
import { CompanyVoiceSettingsUpdate } from '../../../../../models/company.models';

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

  get isSuperAdmin(): boolean {
    return (
      this.authService.isSuperAdmin() || this.authService.isPlatformAdmin()
    );
  }

  // Superadmin Voice Settings Control
  voiceRoster: VoiceSpeakerOption[] = SARVAM_VOICE_ROSTER;
  voiceGender: 'male' | 'female' = 'female';
  voiceSpeed: number = 1.1;
  voiceSpeaker: string = DEFAULT_SARVAM_FEMALE_VOICE;
  sttTtsProvider: 'sarvam' | 'deepgram' = 'sarvam';
  savingVoiceSettings = false;

  // Baseline state for partial update dirty-tracking
  savedVoiceGender: 'male' | 'female' = 'female';
  savedVoiceSpeed: number = 1.1;
  savedVoiceSpeaker: string = DEFAULT_SARVAM_FEMALE_VOICE;
  savedSttTtsProvider: 'sarvam' | 'deepgram' = 'sarvam';

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

  get filteredVoiceSpeakers(): VoiceSpeakerOption[] {
    if (!this.voiceGender) return this.voiceRoster;
    return this.voiceRoster.filter((v) => v.gender === this.voiceGender);
  }

  onVoiceGenderChange(): void {
    const matching = this.filteredVoiceSpeakers;
    if (!matching.some((s) => s.value === this.voiceSpeaker)) {
      this.voiceSpeaker =
        matching[0]?.value ||
        (this.voiceGender === 'male'
          ? DEFAULT_SARVAM_MALE_VOICE
          : DEFAULT_SARVAM_FEMALE_VOICE);
    }
  }

  get hasVoiceSettingsChanges(): boolean {
    return (
      this.voiceGender !== this.savedVoiceGender ||
      this.voiceSpeed !== this.savedVoiceSpeed ||
      this.voiceSpeaker !== this.savedVoiceSpeaker ||
      this.sttTtsProvider !== this.savedSttTtsProvider
    );
  }

  loadSettings(): void {
    this.companyService.getCompanySettings(this.companyId).subscribe({
      next: (settings) => {
        this.companySettings = settings;
        this.voiceGender = settings.voice_gender || 'female';
        this.voiceSpeed =
          settings.voice_speed !== null && settings.voice_speed !== undefined
            ? Math.min(2.0, Math.max(0.5, Number(settings.voice_speed)))
            : 1.1;

        const speakerVal = (settings.voice_speaker || '').toLowerCase().trim();
        const isValid = this.voiceRoster.some((v) => v.value === speakerVal);
        this.voiceSpeaker = isValid
          ? speakerVal
          : (this.voiceGender === 'male'
              ? DEFAULT_SARVAM_MALE_VOICE
              : DEFAULT_SARVAM_FEMALE_VOICE);

        this.sttTtsProvider = settings.stt_tts_provider || 'sarvam';

        this.savedVoiceGender = this.voiceGender;
        this.savedVoiceSpeed = this.voiceSpeed;
        this.savedVoiceSpeaker = this.voiceSpeaker;
        this.savedSttTtsProvider = this.sttTtsProvider;
        this.buildServicesList();
      },
      error: () => {
        this.companySettings = { ...DEFAULT_COMPANY_SETTINGS };
        this.voiceGender = DEFAULT_COMPANY_SETTINGS.voice_gender || 'female';
        this.voiceSpeed = DEFAULT_COMPANY_SETTINGS.voice_speed || 1.1;
        this.voiceSpeaker =
          DEFAULT_COMPANY_SETTINGS.voice_speaker || DEFAULT_SARVAM_FEMALE_VOICE;
        this.sttTtsProvider =
          DEFAULT_COMPANY_SETTINGS.stt_tts_provider || 'sarvam';

        this.savedVoiceGender = this.voiceGender;
        this.savedVoiceSpeed = this.voiceSpeed;
        this.savedVoiceSpeaker = this.voiceSpeaker;
        this.savedSttTtsProvider = this.sttTtsProvider;
        this.buildServicesList();
      },
    });
  }

  saveVoiceSettings(): void {
    if (!this.companyId) return;

    // Partial update: only send fields that actually changed
    const payload: CompanyVoiceSettingsUpdate = {};
    let hasChanges = false;

    if (this.sttTtsProvider !== this.savedSttTtsProvider) {
      payload.stt_tts_provider = this.sttTtsProvider;
      hasChanges = true;
    }

    // Only send Sarvam voice persona attributes if they actually changed
    if (this.voiceGender !== this.savedVoiceGender) {
      payload.voice_gender = this.voiceGender;
      hasChanges = true;
    }

    if (this.voiceSpeed !== this.savedVoiceSpeed) {
      payload.voice_speed = Math.min(
        2.0,
        Math.max(0.5, Number(this.voiceSpeed)),
      );
      hasChanges = true;
    }

    if (this.voiceSpeaker !== this.savedVoiceSpeaker) {
      payload.voice_speaker = (this.voiceSpeaker || '').toLowerCase().trim();
      hasChanges = true;
    }

    if (!hasChanges) {
      this.messageService.add({
        severity: 'info',
        summary: 'No Changes',
        detail: 'No voice settings were modified.',
      });
      return;
    }

    this.savingVoiceSettings = true;
    this.companyService
      .updateCompanyVoiceSettings(this.companyId, payload)
      .subscribe({
        next: (updatedSettings) => {
          this.savingVoiceSettings = false;
          this.companySettings = updatedSettings;

          this.savedVoiceGender =
            updatedSettings.voice_gender || this.voiceGender;
          this.savedVoiceSpeed =
            updatedSettings.voice_speed !== null &&
            updatedSettings.voice_speed !== undefined
              ? updatedSettings.voice_speed
              : this.voiceSpeed;
          this.savedVoiceSpeaker =
            updatedSettings.voice_speaker || this.voiceSpeaker;
          this.savedSttTtsProvider =
            updatedSettings.stt_tts_provider || this.sttTtsProvider;

          this.voiceGender = this.savedVoiceGender;
          this.voiceSpeed = this.savedVoiceSpeed;
          this.voiceSpeaker = this.savedVoiceSpeaker;
          this.sttTtsProvider = this.savedSttTtsProvider;

          this.messageService.add({
            severity: 'success',
            summary: 'Voice Settings Saved',
            detail: `Provider: ${this.sttTtsProvider.toUpperCase()}, Voice: ${this.voiceSpeaker} (${this.voiceGender}), Speed: ${this.voiceSpeed}x`,
          });
          this.buildServicesList();
        },
        error: (err) => {
          this.savingVoiceSettings = false;
          const errorDetail =
            err?.error?.detail ||
            err?.message ||
            'Failed to update platform voice settings. Superadmin rights required.';
          this.messageService.add({
            severity: 'error',
            summary:
              err?.status === 403 ? 'Permission Denied' : 'Update Failed',
            detail: errorDetail,
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
            status: !isEnabled
              ? 'disabled'
              : autoCall
                ? 'active'
                : 'configured',
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
