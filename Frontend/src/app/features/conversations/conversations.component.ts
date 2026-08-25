import { Component, OnInit } from '@angular/core';
import { Conversation } from '../../models/conversation.models';
import { InboxService, InboxQueryParams } from '../../services/inbox.service';
import { CampaignService } from '../../services/campaign.service';
import { Campaign } from '../../models/campaign.models';
import { SharedModule } from '../../shared/shared.module';

@Component({
  selector: 'app-conversations',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './conversations.component.html',
  styleUrl: './conversations.component.scss',
})
export class ConversationsComponent implements OnInit {
  conversations: Conversation[] = [];
  selectedConversation: Conversation | null = null;

  // Filters
  selectedChannel = '';
  showAllLeads = false;
  selectedCampaignId = '';

  campaigns: Campaign[] = [];

  channelOptions = [
    { label: 'All Channels', value: '' },
    { label: 'Web Chat', value: 'web' },
    { label: 'WhatsApp', value: 'whatsapp' },
    { label: 'Facebook Messenger', value: 'messenger' },
    { label: 'Instagram', value: 'instagram' },
    { label: 'SMS', value: 'sms' },
    { label: 'Email', value: 'email' },
    { label: 'Voice Dialler', value: 'voice' },
  ];

  constructor(
    private inboxService: InboxService,
    private campaignService: CampaignService,
  ) {}

  ngOnInit(): void {
    this.loadConversations();
    this.loadCampaigns();
  }

  loadCampaigns(): void {
    this.campaignService.getCampaigns().subscribe({
      next: (campaigns) => {
        this.campaigns = campaigns;
      },
    });
  }

  loadConversations(): void {
    const params: InboxQueryParams = {};
    if (this.selectedChannel) {
      params.channel = this.selectedChannel;
    }
    if (!this.showAllLeads) {
      params.above_threshold = true;
    }
    if (this.selectedCampaignId) {
      params.campaign_id = this.selectedCampaignId;
    }

    this.inboxService.getInbox(params).subscribe({
      next: (response) => {
        this.conversations = response.items.map((item: any, idx: number) => ({
          id: idx + 1,
          leadId: item.id as any,
          leadName: item.customer_name || item.customer_ref,
          type: item.channel === 'voice' ? 'Scheduled' : 'AI',
          status: item.status === 'open' ? 'In Progress' : 'Completed',
          startTime: item.last_message_at,
          duration: '5:32',
          summary: item.summary || 'Customer thread conversation.',
          confidence: (item.lead?.score || 80) / 100,
          agent: item.assigned_user_email || 'AI Assistant',
          channel: item.channel,
          aboveThreshold: item.above_threshold,
        }));
      },
      error: () => {},
    });
  }

  getStatusSeverity(
    status: string,
  ):
    | 'success'
    | 'secondary'
    | 'info'
    | 'warn'
    | 'danger'
    | 'contrast'
    | undefined {
    const map: Record<
      string,
      'success' | 'secondary' | 'info' | 'warn' | 'danger' | 'contrast'
    > = {
      Completed: 'success',
      'In Progress': 'info',
      Missed: 'danger',
      Scheduled: 'warn',
    };
    return map[status] || 'info';
  }

  getTypeSeverity(
    type: string,
  ):
    | 'success'
    | 'secondary'
    | 'info'
    | 'warn'
    | 'danger'
    | 'contrast'
    | undefined {
    const map: Record<
      string,
      'success' | 'secondary' | 'info' | 'warn' | 'danger' | 'contrast'
    > = {
      AI: 'info',
      Human: 'success',
      Scheduled: 'warn',
    };
    return map[type] || 'info';
  }

  getChannelIcon(channel: string): string {
    const icons: Record<string, string> = {
      whatsapp: 'pi pi-whatsapp',
      messenger: 'pi pi-facebook',
      instagram: 'pi pi-instagram',
      sms: 'pi pi-mobile',
      email: 'pi pi-envelope',
      voice: 'pi pi-phone',
      web: 'pi pi-desktop',
    };
    return icons[channel] || 'pi pi-comment';
  }

  getChannelColor(channel: string): string {
    const colors: Record<string, string> = {
      whatsapp: '#25D366',
      messenger: '#0084FF',
      instagram: '#E4405F',
      sms: '#8b5cf6',
      email: '#ef4444',
      voice: '#f59e0b',
      web: '#3b82f6',
    };
    return colors[channel] || '#6b7280';
  }
}
