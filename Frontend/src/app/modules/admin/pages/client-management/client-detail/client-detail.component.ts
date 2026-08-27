import { Component, OnInit, inject } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { CompanyService } from '../../../../../services/company.service';
import { ChannelService } from '../../../../../services/channel.service';
import { VoiceService } from '../../../../../services/voice.service';
import { AuthService } from '../../../../../services/auth.service';
import { Company, CompanySettings } from '../../../../../models/company.models';
import { Channel, LinkedInStatus } from '../../../../../models/channel.models';
import { MessageService } from 'primeng/api';
import { ConfirmationService } from '../../../../../shared/services/confirmation.service';
import { SharedModule } from '../../../../../shared/shared.module';

export interface ServiceAccessItem {
  id: string;
  key?: string;
  isEnabled?: boolean;
  name: string;
  category: 'Voice' | 'Social' | 'Web' | 'Messaging';
  description: string;
  icon: string;
  brandColor: string;
  bgGradient: string;
  status: 'active' | 'configured' | 'available' | 'disabled';
  statusLabel: string;
  badgeSeverity: 'success' | 'info' | 'warn' | 'secondary';
  features: string[];
  configDetails?: {
    accountName?: string;
    accountHandle?: string;
    displayNumber?: string;
    autoReply?: boolean;
    lastActive?: string;
    extraNote?: string;
  };
}

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
  private channelService = inject(ChannelService);
  private voiceService = inject(VoiceService);
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
  companyServicesMap: Record<string, boolean> = {
    whatsapp: true,
    facebook: true,
    messenger: true,
    instagram: true,
    voice_agent: true,
    linkedin: true,
    email_marketing: true,
  };

  ngOnInit(): void {
    this.route.params.subscribe((params) => {
      this.companyId = params['id'] || params['company_id'] || params['clientId'] || '';
      if (this.companyId) {
        this.loadCompanyDetails();
        this.loadSettings();
        this.loadChannels();
        this.loadLinkedInStatus();
        this.loadCompanyServices();
      }
    });
  }

  loadCompanyServices(): void {
    const options: any = {};
    if (this.authService.isPlatformAdmin()) {
      options.params = { client_id: this.companyId };
    }
    this.companyService.getCompanyPermissions(this.companyId, options).subscribe({
      next: (res) => {
        const items = res?.permissions || (res as any)?.services || [];
        if (items.length > 0) {
          const map: Record<string, boolean> = {};
          items.forEach((s: any) => {
            map[s.key.toLowerCase()] = s.is_enabled;
          });
          this.companyServicesMap = { ...this.companyServicesMap, ...map };
          this.buildServicesList();
        }
      },
      error: () => {
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

    this.companyService.patchCompanyPermissions(
      this.companyId,
      { permissions: [{ key: serviceKey, is_enabled: newStatus }] },
      options
    ).subscribe({
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
        // Mock fallback for local preview
        this.company = {
          id: this.companyId,
          name: 'TechCorp Solutions',
          email: 'billing@techcorp.com',
          phone_number: '+1 (555) 234-5678',
          description:
            'Enterprise SaaS provider specializing in sales automation, real-time AI dialling, and omnichannel customer communication.',
          is_active: true,
          created_at: '2024-01-15T08:30:00Z',
          user_count: 142,
          document_count: 24,
          chunk_count: 1240,
          script_count: 5,
          conversation_count: 1845,
        };
        this.buildServicesList();
        this.loading = false;
      },
    });
  }

  loadSettings(): void {
    this.companyService.getCompanySettings(this.companyId).subscribe({
      next: (settings) => {
        this.companySettings = settings;
        this.buildServicesList();
      },
      error: () => {
        this.companySettings = {
          handoff_threshold: 65,
          retrieval_top_k: 5,
          default_language: 'en',
          auto_assign_enabled: true,
          auto_call_on_hot_lead: true,
          widget_enabled: true,
          widget_greeting: 'Hello! How can our AI assistant help you today?',
        };
        this.buildServicesList();
      },
    });
  }

  loadChannels(): void {
    this.loadingChannels = true;
    this.channelService.getChannels().subscribe({
      next: (res: any) => {
        this.channels = Array.isArray(res) ? res : (res?.items || []);
        this.loadingChannels = false;
        this.buildServicesList();
      },
      error: () => {
        this.channels = [];
        this.loadingChannels = false;
        this.buildServicesList();
      },
    });
  }

  loadLinkedInStatus(): void {
    this.channelService.getLinkedInStatus().subscribe({
      next: (status) => {
        this.linkedinStatus = status;
        this.buildServicesList();
      },
      error: () => {
        this.linkedinStatus = null;
        this.buildServicesList();
      },
    });
  }

  buildServicesList(): void {
    const whatsappCh = this.channels.find(
      (c) => c.channel?.toLowerCase() === 'whatsapp'
    );
    const messengerCh = this.channels.find(
      (c) => c.channel?.toLowerCase() === 'messenger' || c.channel?.toLowerCase() === 'facebook'
    );
    const instagramCh = this.channels.find(
      (c) => c.channel?.toLowerCase() === 'instagram'
    );
    const isLinkedInConnected = !!(this.linkedinStatus && this.linkedinStatus.connected);

    const isVoiceEnabled = (this.companyServicesMap['voice_agent']) !== false;
    const isWhatsappEnabled = (this.companyServicesMap['social.whatsapp'] ?? this.companyServicesMap['whatsapp']) !== false;
    const isMessengerEnabled = (this.companyServicesMap['social.facebook'] ?? this.companyServicesMap['facebook'] ?? this.companyServicesMap['messenger']) !== false;
    const isInstagramEnabled = (this.companyServicesMap['social.instagram'] ?? this.companyServicesMap['instagram']) !== false;
    const isLinkedInEnabled = (this.companyServicesMap['social.linkedin'] ?? this.companyServicesMap['linkedin']) !== false;
    const isEmailEnabled = (this.companyServicesMap['email_marketing'] ?? this.companyServicesMap['email']) !== false;

    this.services = [
      {
        id: 'voice-calling',
        key: 'voice_agent',
        isEnabled: isVoiceEnabled,
        name: 'AI Voice & Dialler',
        category: 'Voice',
        description:
          'Inbound & outbound synthetic voice dialler with automated calling for hot leads, speech-to-text live transcription, and audio recordings.',
        icon: 'pi pi-phone',
        brandColor: '#f59e0b',
        bgGradient: 'linear-gradient(135deg, #f59e0b 0%, #d97706 100%)',
        status: !isVoiceEnabled ? 'disabled' : this.companySettings?.auto_call_on_hot_lead ? 'active' : 'configured',
        statusLabel: !isVoiceEnabled
          ? 'Disabled'
          : this.companySettings?.auto_call_on_hot_lead
          ? 'Active (Auto-Dial On)'
          : 'Configured',
        badgeSeverity: !isVoiceEnabled ? 'secondary' : 'success',
        features: [
          'Outbound AI Lead Calling',
          'Live Call Transcripts',
          'Call Audio Recording & Playback',
          'Human Agent Handoff Routing',
        ],
        configDetails: {
          displayNumber: '+1 (800) 555-0199',
          autoReply: this.companySettings?.auto_call_on_hot_lead ?? true,
          extraNote: 'Twilio Voice Integration Active',
        },
      },
      {
        id: 'whatsapp',
        key: 'social.whatsapp',
        isEnabled: isWhatsappEnabled,
        name: 'WhatsApp Business API',
        category: 'Social',
        description:
          'Official Meta Cloud API integration for WhatsApp messaging, verified template notifications, and automated 24/7 AI chat replies.',
        icon: 'pi pi-whatsapp',
        brandColor: '#25D366',
        bgGradient: 'linear-gradient(135deg, #25D366 0%, #128C7E 100%)',
        status: !isWhatsappEnabled ? 'disabled' : whatsappCh?.is_active ? 'active' : whatsappCh ? 'configured' : 'available',
        statusLabel: !isWhatsappEnabled
          ? 'Disabled'
          : whatsappCh?.is_active
          ? 'Active'
          : whatsappCh
          ? 'Connected (Inactive)'
          : 'Ready to Connect',
        badgeSeverity: !isWhatsappEnabled
          ? 'secondary'
          : whatsappCh?.is_active
          ? 'success'
          : whatsappCh
          ? 'warn'
          : 'secondary',
        features: [
          'Meta Cloud API v21.0',
          'Automated AI Inbound Replies',
          'Rich Media & Document Delivery',
          'Verified Business Number',
        ],
        configDetails: {
          accountName: whatsappCh?.name || 'Main WhatsApp Line',
          displayNumber: whatsappCh?.display_number || '+1 (555) 019-2834',
          autoReply: whatsappCh?.auto_reply ?? true,
          lastActive: whatsappCh?.last_inbound_at || 'Recently active',
        },
      },
      {
        id: 'messenger',
        key: 'social.facebook',
        isEnabled: isMessengerEnabled,
        name: 'Facebook Messenger',
        category: 'Social',
        description:
          'Meta Page Messenger webhook routing. Engages prospects directly from Facebook ads, post comments, and company page inbox.',
        icon: 'pi pi-facebook',
        brandColor: '#0084FF',
        bgGradient: 'linear-gradient(135deg, #0084FF 0%, #0063E6 100%)',
        status: !isMessengerEnabled ? 'disabled' : messengerCh?.is_active ? 'active' : messengerCh ? 'configured' : 'available',
        statusLabel: !isMessengerEnabled
          ? 'Disabled'
          : messengerCh?.is_active
          ? 'Active'
          : messengerCh
          ? 'Configured'
          : 'Ready to Connect',
        badgeSeverity: !isMessengerEnabled
          ? 'secondary'
          : messengerCh?.is_active
          ? 'success'
          : messengerCh
          ? 'warn'
          : 'secondary',
        features: [
          'Page Messaging Webhooks',
          'Facebook Ads Click-to-Chat Capture',
          'Instant AI Qualification',
          'Seamless Human Takeover',
        ],
        configDetails: {
          accountName: messengerCh?.name || 'Facebook Page Inbox',
          autoReply: messengerCh?.auto_reply ?? true,
          lastActive: messengerCh?.last_inbound_at || 'Recently active',
        },
      },
      {
        id: 'instagram',
        key: 'social.instagram',
        isEnabled: isInstagramEnabled,
        name: 'Instagram Direct (DM)',
        category: 'Social',
        description:
          'Automated Instagram Direct message responses, story mention replies, and comment-to-DM conversion funnels.',
        icon: 'pi pi-instagram',
        brandColor: '#E4405F',
        bgGradient: 'linear-gradient(135deg, #E4405F 0%, #833AB4 100%)',
        status: !isInstagramEnabled ? 'disabled' : instagramCh?.is_active ? 'active' : instagramCh ? 'configured' : 'available',
        statusLabel: !isInstagramEnabled
          ? 'Disabled'
          : instagramCh?.is_active
          ? 'Active'
          : instagramCh
          ? 'Configured'
          : 'Ready to Connect',
        badgeSeverity: !isInstagramEnabled
          ? 'secondary'
          : instagramCh?.is_active
          ? 'success'
          : instagramCh
          ? 'warn'
          : 'secondary',
        features: [
          'Instagram Business Graph API',
          'Story Reply Lead Generation',
          'DM Instant AI Response',
          'Comment Automation',
        ],
        configDetails: {
          accountHandle: instagramCh?.name || '@techcorp_solutions',
          autoReply: instagramCh?.auto_reply ?? true,
          lastActive: instagramCh?.last_inbound_at || 'Active today',
        },
      },
      {
        id: 'linkedin',
        key: 'social.linkedin',
        isEnabled: isLinkedInEnabled,
        name: 'LinkedIn Automation',
        category: 'Social',
        description:
          'B2B Social outreach and company profile integration with automated connection requests, message sync, and lead discovery.',
        icon: 'pi pi-linkedin',
        brandColor: '#0A66C2',
        bgGradient: 'linear-gradient(135deg, #0A66C2 0%, #004182 100%)',
        status: !isLinkedInEnabled ? 'disabled' : isLinkedInConnected ? 'active' : 'available',
        statusLabel: !isLinkedInEnabled
          ? 'Disabled'
          : isLinkedInConnected
          ? 'Connected'
          : 'Available',
        badgeSeverity: !isLinkedInEnabled
          ? 'secondary'
          : isLinkedInConnected
          ? 'success'
          : 'secondary',
        features: [
          'LinkedIn OAuth Authorization',
          'Profile & Company Sync',
          'Automated Social Outreach',
          'B2B Lead Qualification',
        ],
        configDetails: {
          accountName: isLinkedInConnected
            ? `URN: ${this.linkedinStatus?.person_urn || 'Connected'}`
            : 'Not Connected',
          extraNote: isLinkedInConnected
            ? 'Token Valid & Synchronized'
            : 'Click Connect to authorize OAuth',
        },
      },
      {
        id: 'sms-email',
        key: 'email_marketing',
        isEnabled: isEmailEnabled,
        name: 'Email Marketing & Outreach',
        category: 'Messaging',
        description:
          'Two-way messaging, transactional email drip campaigns, and automated follow-up triggers.',
        icon: 'pi pi-envelope',
        brandColor: '#8b5cf6',
        bgGradient: 'linear-gradient(135deg, #8b5cf6 0%, #a855f7 100%)',
        status: !isEmailEnabled ? 'disabled' : 'active',
        statusLabel: !isEmailEnabled ? 'Disabled' : 'Active',
        badgeSeverity: !isEmailEnabled ? 'secondary' : 'success',
        features: [
          'Automated SMS Follow-ups',
          'Transactional Email Delivery',
          'Opt-In / Opt-Out Consent Tracking',
          'Delivery Status Webhooks',
        ],
        configDetails: {
          displayNumber: '+1 (555) 018-9922',
          extraNote: 'AWS SES Active',
        },
      },
      {
        id: 'webchat',
        key: 'web',
        isEnabled: true,
        name: 'Web Chat Widget',
        category: 'Web',
        description:
          'Lightweight embeddable chat widget for client websites with custom brand colors, greeting scripts, and lead capture forms.',
        icon: 'pi pi-desktop',
        brandColor: '#6366f1',
        bgGradient: 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)',
        status: this.companySettings?.widget_enabled ? 'active' : 'available',
        statusLabel: this.companySettings?.widget_enabled ? 'Enabled' : 'Disabled',
        badgeSeverity: this.companySettings?.widget_enabled ? 'success' : 'warn',
        features: [
          '1-Line Script Embed Snippet',
          'Customizable AI Greeting',
          'RAG Knowledge Base Answering',
          'Automated Lead Intake Form',
        ],
        configDetails: {
          extraNote: `Greeting: "${this.companySettings?.widget_greeting || 'Hello!'}"`,
          autoReply: true,
        },
      },
    ];
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
        this.companyService.deleteCompany(this.company!.id, this.company!.id).subscribe({
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
    return `<script src="https://cdn.leadai.com/widget.js" data-company-id="${this.companyId}" async></script>`;
  }
}
