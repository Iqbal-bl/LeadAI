import { Component, OnInit, OnDestroy, ViewChild } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { ChannelService } from '../../../services/channel.service';
import {
  Channel,
  ChannelStatus,
  LinkedInStatus,
} from '../../../models/channel.models';
import { MessageService, MenuItem } from 'primeng/api';
import { Menu } from 'primeng/menu';
import { ConfirmationService } from '../../../shared/services/confirmation.service';
import { ChannelWizardComponent } from '../channel-wizard/channel-wizard.component';
import { ChannelContactsComponent } from '../channel-contacts/channel-contacts.component';

@Component({
  selector: 'app-channel-list',
  standalone: true,
  imports: [SharedModule, ChannelWizardComponent, ChannelContactsComponent],
  templateUrl: './channel-list.component.html',
  styleUrl: './channel-list.component.scss',
})
export class ChannelListComponent implements OnInit, OnDestroy {
  @ViewChild('channelActionMenu') channelActionMenu!: Menu;

  channels: Channel[] = [];
  channelStatus: ChannelStatus | null = null;
  linkedinStatus: LinkedInStatus | null = null;
  linkedinLoading = false;
  private linkedinPollingInterval: any;
  private messageEventListener!: (event: MessageEvent) => void;
  loading = true;

  // Dialog Visibility Flags
  showWizard = false;
  showContacts = false;
  showWebhookDialog = false;

  // Selected Channels for Dialogs
  selectedChannel: Channel | null = null;
  webhookChannel: Channel | null = null;

  constructor(
    private channelService: ChannelService,
    private messageService: MessageService,
    private confirmationService: ConfirmationService,
  ) {}

  ngOnInit(): void {
    this.loadChannels();
    this.loadStatus();
    // this.loadLinkedInStatus();
    this.messageEventListener = (event: MessageEvent) => this.handleOAuthMessage(event);
    window.addEventListener('message', this.messageEventListener);
  }

  ngOnDestroy(): void {
    if (this.messageEventListener) {
      window.removeEventListener('message', this.messageEventListener);
    }
    if (this.linkedinPollingInterval) {
      clearInterval(this.linkedinPollingInterval);
    }
  }

  private handleOAuthMessage(event: MessageEvent): void {
    if (!event.data || typeof event.data !== 'object') return;
    const { type, channel, error } = event.data;

    if (type === 'FACEBOOK_AUTH_SUCCESS' || type === 'INSTAGRAM_AUTH_SUCCESS') {
      this.messageService.add({
        severity: 'success',
        summary: 'OAuth Success',
        detail: `${type === 'FACEBOOK_AUTH_SUCCESS' ? 'Facebook Page' : 'Instagram'} connected successfully!`,
      });
      this.loadChannels();
      this.loadStatus();
    } else if (type === 'FACEBOOK_AUTH_ERROR' || type === 'INSTAGRAM_AUTH_ERROR') {
      this.messageService.add({
        severity: 'error',
        summary: 'OAuth Error',
        detail: error || 'Authentication failed.',
      });
    }
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

  activeChannelMenuItems: MenuItem[] = [];

  openChannelMenu(event: Event, channel: Channel): void {
    event.stopPropagation();
    this.activeChannelMenuItems = this.getChannelMenuItems(channel);
    this.channelActionMenu.toggle(event);
  }

  getChannelMenuItems(channel: Channel): MenuItem[] {
    return [
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


