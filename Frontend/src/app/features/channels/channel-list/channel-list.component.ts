import { Component, OnInit } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { ChannelService } from '../../../services/channel.service';
import {
  Channel,
  ChannelStatus,
  ChannelUpdateRequest,
  LinkedInStatus,
} from '../../../models/channel.models';
import { MessageService, ConfirmationService, MenuItem } from 'primeng/api';
import { ChannelWizardComponent } from '../channel-wizard/channel-wizard.component';
import { ChannelContactsComponent } from '../channel-contacts/channel-contacts.component';
import { ScriptService } from '../../../services/script.service';
import { Script } from '../../../models/script.models';

@Component({
  selector: 'app-channel-list',
  standalone: true,
  imports: [SharedModule, ChannelWizardComponent, ChannelContactsComponent],
  templateUrl: './channel-list.component.html',
  styleUrl: './channel-list.component.scss',
})
export class ChannelListComponent implements OnInit {
  channels: Channel[] = [];
  channelStatus: ChannelStatus | null = null;
  linkedinStatus: LinkedInStatus | null = null;
  linkedinLoading = false;
  private linkedinPollingInterval: any;
  loading = true;

  // Scripts for dropdown selection
  scripts: Script[] = [];

  // Dialog Visibility Flags
  showWizard = false;
  showContacts = false;
  showEditDialog = false;
  showTestDialog = false;
  showWebhookDialog = false;

  // Selected Channels for Dialogs
  selectedChannel: Channel | null = null;
  editingChannel: Channel | null = null;
  testingChannel: Channel | null = null;
  webhookChannel: Channel | null = null;

  // Edit Form Model
  editForm: ChannelUpdateRequest = {
    name: '',
    access_token: '',
    app_secret: '',
    verify_token: '',
    display_number: '',
    api_version: 'v21.0',
    is_active: true,
    auto_reply: true,
    script_id: '',
    default_language: 'en',
  };
  editLoading = false;
  editError = '';

  // Test Message Form Model
  testForm = {
    to: '',
    message: 'Hello! This is a test message from LeadAI.',
    template_name: '',
    template_language: 'en',
  };
  testLoading = false;
  testSuccess = false;
  testError = '';

  constructor(
    private channelService: ChannelService,
    private messageService: MessageService,
    private confirmationService: ConfirmationService,
    private scriptService: ScriptService,
  ) {}

  ngOnInit(): void {
    this.loadChannels();
    this.loadStatus();
    this.loadLinkedInStatus();
    this.loadScripts();
  }

  ngOnDestroy(): void {
    if (this.linkedinPollingInterval) {
      clearInterval(this.linkedinPollingInterval);
    }
  }

  loadScripts(): void {
    this.scriptService.getScripts(undefined, true).subscribe({
      next: (res) => {
        this.scripts = res || [];
      },
      error: () => {
        this.scripts = [];
      },
    });
  }

  loadLinkedInStatus(): void {
    this.channelService.getLinkedInStatus().subscribe({
      next: (status) => {
        this.linkedinStatus = status;
      },
      error: () => {
        this.linkedinStatus = null;
      },
    });
  }

  connectLinkedIn(): void {
    this.linkedinLoading = true;
    this.channelService.getLinkedInConnectUrl().subscribe({
      next: (res) => {
        if (res && res.authorize_url) {
          const width = 600;
          const height = 650;
          const left = window.screen.width / 2 - width / 2;
          const top = window.screen.height / 2 - height / 2;
          const popup = window.open(
            res.authorize_url,
            'LinkedIn Connect',
            `width=${width},height=${height},top=${top},left=${left},scrollbars=yes,status=yes`,
          );

          if (this.linkedinPollingInterval) {
            clearInterval(this.linkedinPollingInterval);
          }

          this.linkedinPollingInterval = setInterval(() => {
            this.channelService.getLinkedInStatus().subscribe({
              next: (status) => {
                if (status && status.connected) {
                  clearInterval(this.linkedinPollingInterval);
                  this.linkedinLoading = false;
                  this.linkedinStatus = status;
                  if (popup && !popup.closed) {
                    popup.close();
                  }
                  this.messageService.add({
                    severity: 'success',
                    summary: 'LinkedIn Connected',
                    detail: `LinkedIn profile connected successfully${status.person_urn ? ' (' + status.person_urn + ')' : ''}.`,
                  });
                  this.loadChannels();
                  this.loadStatus();
                }
              },
            });
          }, 3000);
        } else {
          this.linkedinLoading = false;
        }
      },
      error: (err) => {
        this.linkedinLoading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'OAuth Error',
          detail: err?.error?.detail || err?.message || 'Failed to initiate LinkedIn OAuth authorization.',
        });
      },
    });
  }

  disconnectLinkedIn(): void {
    this.confirmationService.confirm({
      message: 'Are you sure you want to disconnect this company\'s LinkedIn profile?',
      header: 'Disconnect LinkedIn Profile',
      icon: 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: 'p-button-danger',
      accept: () => {
        this.channelService.disconnectLinkedIn().subscribe({
          next: () => {
            this.messageService.add({
              severity: 'success',
              summary: 'Disconnected',
              detail: 'LinkedIn profile disconnected successfully.',
            });
            this.loadLinkedInStatus();
            this.loadChannels();
            this.loadStatus();
          },
          error: (err) => {
            this.messageService.add({
              severity: 'error',
              summary: 'Error',
              detail: err?.error?.detail || 'Failed to disconnect LinkedIn profile.',
            });
          },
        });
      },
    });
  }

  loadChannels(): void {
    this.loading = true;
    this.channelService.getChannels().subscribe({
      next: (res: any) => {
        this.channels = Array.isArray(res) ? res : (res?.items || []);
        this.loading = false;
      },
      error: () => {
        this.channels = [];
        this.loading = false;
      },
    });
  }

  loadStatus(): void {
    this.channelService.getChannelStatus().subscribe({
      next: (status) => {
        this.channelStatus = status;
      },
    });
  }

  // --- Wizard Actions ---
  openWizard(): void {
    this.showWizard = true;
  }

  onWizardComplete(): void {
    this.showWizard = false;
    this.loadChannels();
    this.loadStatus();
    this.loadLinkedInStatus();
    this.messageService.add({
      severity: 'success',
      summary: 'Channel Connected',
      detail: 'Your channel has been successfully connected and verified.',
    });
  }

  onWizardClose(): void {
    this.showWizard = false;
  }

  // --- Contacts Modal ---
  openContacts(channel: Channel): void {
    this.selectedChannel = channel;
    this.showContacts = true;
  }

  closeContacts(): void {
    this.showContacts = false;
    this.selectedChannel = null;
  }

  // --- Webhook Details Modal ---
  openWebhookInfo(channel: Channel): void {
    this.webhookChannel = channel;
    this.showWebhookDialog = true;
  }

  closeWebhookInfo(): void {
    this.showWebhookDialog = false;
    this.webhookChannel = null;
  }

  copyToClipboard(value: string | undefined, label: string): void {
    if (!value) return;
    navigator.clipboard.writeText(value).then(() => {
      this.messageService.add({
        severity: 'success',
        summary: 'Copied',
        detail: `${label} copied to clipboard`,
        life: 2000,
      });
    });
  }

  // --- Edit Channel Modal ---
  openEditDialog(channel: Channel): void {
    this.editingChannel = channel;
    this.editError = '';
    this.editForm = {
      name: channel.name || channel.display_name || '',
      access_token: '',
      app_secret: '',
      verify_token: channel.verify_token || '',
      display_number: channel.display_number || '',
      api_version: channel.api_version || 'v21.0',
      is_active: channel.is_active !== undefined ? channel.is_active : true,
      auto_reply: channel.auto_reply !== undefined ? channel.auto_reply : true,
      script_id: channel.script_id || '',
      default_language: channel.default_language || 'en',
    };
    this.showEditDialog = true;
  }

  closeEditDialog(): void {
    this.showEditDialog = false;
    this.editingChannel = null;
  }

  saveEdit(): void {
    if (!this.editingChannel) return;

    this.editLoading = true;
    this.editError = '';

    const payload: ChannelUpdateRequest = {
      name: this.editForm.name?.trim() || undefined,
      display_number: this.editForm.display_number?.trim() || undefined,
      api_version: this.editForm.api_version?.trim() || undefined,
      is_active: this.editForm.is_active,
      auto_reply: this.editForm.auto_reply,
      script_id: this.editForm.script_id || undefined,
      default_language: this.editForm.default_language?.trim() || undefined,
    };

    if (this.editForm.access_token?.trim()) {
      payload.access_token = this.editForm.access_token.trim();
    }
    if (this.editForm.app_secret?.trim()) {
      payload.app_secret = this.editForm.app_secret.trim();
    }
    if (this.editForm.verify_token?.trim()) {
      payload.verify_token = this.editForm.verify_token.trim();
    }

    this.channelService.updateChannel(this.editingChannel.id, payload).subscribe({
      next: (updated) => {
        this.editLoading = false;
        this.showEditDialog = false;
        this.editingChannel = null;
        this.loadChannels();
        this.loadStatus();
        this.messageService.add({
          severity: 'success',
          summary: 'Channel Updated',
          detail: 'Channel configurations updated successfully.',
        });
      },
      error: (err) => {
        this.editLoading = false;
        this.editError = err.error?.detail || err.message || 'Failed to update channel.';
      },
    });
  }

  // --- Send Test Message Modal ---
  openTestDialog(channel: Channel): void {
    this.testingChannel = channel;
    this.testSuccess = false;
    this.testError = '';
    this.testForm = {
      to: '',
      message: 'Hello! This is a test message from LeadAI.',
      template_name: '',
      template_language: 'en',
    };
    this.showTestDialog = true;
  }

  closeTestDialog(): void {
    this.showTestDialog = false;
    this.testingChannel = null;
  }

  sendTest(): void {
    if (!this.testingChannel || !this.testForm.to.trim()) return;

    this.testLoading = true;
    this.testError = '';
    this.testSuccess = false;

    this.channelService.testChannel(this.testingChannel.id, {
      to: this.testForm.to.trim(),
      message: this.testForm.message?.trim() || undefined,
      template_name: this.testForm.template_name?.trim() || undefined,
      template_language: this.testForm.template_language?.trim() || 'en',
    }).subscribe({
      next: () => {
        this.testLoading = false;
        this.testSuccess = true;
        this.messageService.add({
          severity: 'success',
          summary: 'Test Sent',
          detail: 'Test message sent successfully!',
        });
      },
      error: (err) => {
        this.testLoading = false;
        this.testError = err.error?.detail || err.message || 'Failed to send test message. Check token permissions and recipient 24h window.';
      },
    });
  }

  // --- Disconnect / Delete Channel ---
  deleteChannel(channel: Channel): void {
    this.confirmationService.confirm({
      message: `Are you sure you want to disconnect "${channel.name || channel.display_name || channel.display_number || channel.channel}"? Active conversations on this channel may be interrupted.`,
      header: 'Disconnect Channel',
      icon: 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: 'p-button-danger',
      accept: () => {
        this.channelService.deleteChannel(channel.id).subscribe({
          next: () => {
            this.loadChannels();
            this.loadStatus();
            this.messageService.add({
              severity: 'success',
              summary: 'Disconnected',
              detail: 'Channel has been disconnected.',
            });
          },
          error: (err) => {
            this.messageService.add({
              severity: 'error',
              summary: 'Error',
              detail: err.error?.detail || 'Failed to disconnect channel.',
            });
          },
        });
      },
    });
  }

  // --- In-Table Quick Toggles ---
  toggleAutoReply(channel: Channel): void {
    const newValue = !channel.auto_reply;
    this.channelService.toggleAutoReply(channel.id, newValue).subscribe({
      next: () => {
        channel.auto_reply = newValue;
        this.messageService.add({
          severity: 'info',
          summary: 'Auto-Reply Updated',
          detail: `Auto-reply ${newValue ? 'enabled' : 'disabled'} for ${channel.name || channel.display_name || channel.channel}.`,
        });
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Failed to update auto-reply setting.',
        });
      },
    });
  }

  toggleActiveStatus(channel: Channel): void {
    const newActive = !channel.is_active;
    this.channelService.toggleActiveStatus(channel.id, newActive).subscribe({
      next: () => {
        channel.is_active = newActive;
        this.loadStatus();
        this.messageService.add({
          severity: 'info',
          summary: 'Status Updated',
          detail: `Channel is now ${newActive ? 'active' : 'inactive'}.`,
        });
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Failed to update channel status.',
        });
      },
    });
  }

  // --- Presentation Helpers ---
  getChannelIcon(type: string | undefined): string {
    const icons: Record<string, string> = {
      whatsapp: 'pi pi-whatsapp',
      messenger: 'pi pi-facebook',
      instagram: 'pi pi-instagram',
      linkedin: 'pi pi-linkedin',
    };
    return (type && icons[type.toLowerCase()]) || 'pi pi-comment';
  }

  getChannelColor(type: string | undefined): string {
    const colors: Record<string, string> = {
      whatsapp: '#25D366',
      messenger: '#0084FF',
      instagram: '#E4405F',
      linkedin: '#0A66C2',
    };
    return (type && colors[type.toLowerCase()]) || '#6366f1';
  }

  getChannelMenuItems(channel: Channel): MenuItem[] {
    return [
      {
        label: 'Send Test Message',
        icon: 'pi pi-send',
        command: () => this.openTestDialog(channel),
      },
      {
        label: 'View Contacts',
        icon: 'pi pi-users',
        command: () => this.openContacts(channel),
      },
      {
        label: 'Webhook Configuration',
        icon: 'pi pi-link',
        command: () => this.openWebhookInfo(channel),
      },
      {
        label: 'Edit Settings',
        icon: 'pi pi-pencil',
        command: () => this.openEditDialog(channel),
      },
      {
        separator: true,
      },
      {
        label: 'Disconnect',
        icon: 'pi pi-trash',
        styleClass: 'text-red-500',
        command: () => this.deleteChannel(channel),
      },
    ];
  }
}


