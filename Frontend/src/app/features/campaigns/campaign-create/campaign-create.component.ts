import { Component, EventEmitter, Input, Output, OnChanges, SimpleChanges } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { CampaignService } from '../../../services/campaign.service';
import { ContactListService } from '../../../services/contact-list.service';
import { ContactList } from '../../../models/contact-list.models';
import { ChannelService } from '../../../services/channel.service';
import { Channel } from '../../../models/channel.models';
import {
  Campaign,
  CampaignKind,
  CampaignChannel,
  CampaignPurpose,
  AudienceType,
  CampaignCreateRequest,
  CampaignUpdateRequest,
} from '../../../models/campaign.models';
import { MessageService } from 'primeng/api';

// Channels where a campaign must send through one specific connected account,
// same channels the backend requires channel_account_id for (routers/campaigns.py).
const ACCOUNT_REQUIRED_CHANNELS: CampaignChannel[] = ['whatsapp', 'messenger', 'instagram'];

@Component({
  selector: 'app-campaign-create',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './campaign-create.component.html',
  styleUrl: './campaign-create.component.scss',
})
export class CampaignCreateComponent implements OnChanges {
  @Input() visible = false;
  @Input() campaign: Campaign | null = null;
  @Input() audienceId = '';
  @Output() complete = new EventEmitter<void>();
  @Output() close = new EventEmitter<void>();

  name = '';
  kind: CampaignKind = 'message';
  channel: CampaignChannel = 'whatsapp';
  channelAccountId = '';
  purpose: CampaignPurpose = 'promotional';
  audienceType: AudienceType = 'list';
  body = '';
  scheduledAt = '';
  concurrency: number = 5; // Integer, 1–50, default 5 (only meaningful for kind: 'call')
  rateLimit: number | null = null;

  saving = false;
  contactLists: ContactList[] = [];
  channels: Channel[] = [];

  kindOptions: { label: string; value: CampaignKind; icon: string }[] = [
    { label: 'Message', value: 'message', icon: 'pi pi-envelope' },
    { label: 'Call', value: 'call', icon: 'pi pi-phone' },
  ];

  channelOptions: { label: string; value: CampaignChannel }[] = [
    { label: 'WhatsApp', value: 'whatsapp' },
    { label: 'Messenger', value: 'messenger' },
    { label: 'Instagram', value: 'instagram' },
    { label: 'SMS', value: 'sms' },
    { label: 'Email', value: 'email' },
    { label: 'Voice', value: 'voice' },
  ];

  purposeOptions: { label: string; value: CampaignPurpose; hint: string }[] = [
    {
      label: 'Promotional',
      value: 'promotional',
      hint: 'Standard promotional outreach',
    },
    {
      label: 'Festive',
      value: 'festive',
      hint: 'Holiday / occasion greetings',
    },
    {
      label: 'Cold Outreach',
      value: 'cold_outreach',
      hint: 'First-time contact',
    },
    { label: 'Follow Up', value: 'follow_up', hint: 'Re-engage warm leads' },
    {
      label: 'Reactivation',
      value: 'reactivation',
      hint: 'Win back churned contacts',
    },
    {
      label: 'Transactional',
      value: 'transactional',
      hint: 'Bypasses quiet hours',
    },
  ];

  audienceTypeOptions: { label: string; value: AudienceType }[] = [
    { label: 'Contact List', value: 'list' },
    { label: 'Leads Filter', value: 'leads' },
    { label: 'Customers', value: 'customers' },
  ];

  constructor(
    private campaignService: CampaignService,
    private contactListService: ContactListService,
    private channelService: ChannelService,
    private messageService: MessageService,
  ) {
    this.loadContactLists();
    this.loadChannels();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['campaign'] && this.campaign) {
      this.populateFromCampaign(this.campaign);
    } else if (changes['visible'] && this.visible && !this.campaign) {
      this.resetForm();
    }
  }

  get isEditMode(): boolean {
    return !!this.campaign?.id;
  }

  populateFromCampaign(c: Campaign): void {
    this.name = c.name || '';
    this.kind = (c.kind as CampaignKind) || 'message';
    this.channel = (c.channel as CampaignChannel) || (this.kind === 'call' ? 'voice' : 'whatsapp');
    this.channelAccountId = c.channel_account_id || '';
    this.purpose = (c.purpose as CampaignPurpose) || 'promotional';
    this.audienceType = (c.audience_type as AudienceType) || 'list';
    this.audienceId = c.list_id || c.audience_id || '';
    this.body = c.message_body || c.body || '';
    this.concurrency = c.concurrency != null ? c.concurrency : 5;
    this.rateLimit = c.rate_per_minute ?? c.rate_limit ?? null;
    this.scheduledAt = c.scheduled_at ? this.formatDateTimeLocal(c.scheduled_at) : '';
  }

  resetForm(): void {
    this.name = '';
    this.kind = 'message';
    this.channel = 'whatsapp';
    this.channelAccountId = '';
    this.purpose = 'promotional';
    this.audienceType = 'list';
    this.body = '';
    this.scheduledAt = '';
    this.concurrency = 5;
    this.rateLimit = null;
  }

  private formatDateTimeLocal(dateStr: string): string {
    try {
      const d = new Date(dateStr);
      if (isNaN(d.getTime())) return '';
      const pad = (n: number) => n.toString().padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch {
      return '';
    }
  }

  loadContactLists(): void {
    this.contactListService.getLists().subscribe({
      next: (lists) => {
        this.contactLists = lists;
      },
    });
  }

  loadChannels(): void {
    this.channelService.getChannels().subscribe({
      next: (channels) => {
        this.channels = channels;
      },
    });
  }

  onKindChange(): void {
    if (this.kind === 'call') {
      if (this.concurrency == null || this.concurrency < 1 || this.concurrency > 50) {
        this.concurrency = 5;
      }
      if (this.channel !== 'voice') {
        this.channel = 'voice';
      }
    } else {
      if (this.channel === 'voice') {
        this.channel = 'whatsapp';
      }
    }
    this.onChannelChange();
  }

  /** Whether the selected channel needs one specific connected account picked. */
  get needsChannelAccount(): boolean {
    return this.kind === 'message' && ACCOUNT_REQUIRED_CHANNELS.includes(this.channel);
  }

  get accountsForChannel(): Channel[] {
    return this.channels.filter(
      (c) => c.channel === this.channel && c.is_active !== false,
    );
  }

  onChannelChange(): void {
    // The previous selection almost certainly belongs to a different channel.
    this.channelAccountId = '';
  }

  get availableVariables(): string[] {
    if (this.audienceType === 'list' && this.audienceId) {
      const list = this.contactLists.find((l) => l.id === this.audienceId);
      return list?.columns || [];
    }
    return ['name', 'phone', 'email'];
  }

  insertVariable(variable: string): void {
    this.body += `{{${variable}}}`;
  }

  get isConcurrencyValid(): boolean {
    if (this.kind !== 'call') return true;
    if (this.concurrency == null) return false;
    const n = Number(this.concurrency);
    return Number.isInteger(n) && n >= 1 && n <= 50;
  }

  get canSave(): boolean {
    if (!this.name.trim() || !this.body.trim()) return false;
    if (this.needsChannelAccount && !this.channelAccountId) return false;
    if (this.kind === 'call' && !this.isConcurrencyValid) return false;
    return true;
  }

  save(): void {
    if (!this.canSave) return;
    this.saving = true;

    if (this.isEditMode && this.campaign?.id) {
      const updatePayload: CampaignUpdateRequest = {
        name: this.name.trim(),
        message_body: this.body.trim(),
        scheduled_at: this.scheduledAt ? new Date(this.scheduledAt).toISOString() : null,
      };

      if (this.kind === 'call') {
        updatePayload.concurrency = this.concurrency
          ? Math.min(50, Math.max(1, Math.round(Number(this.concurrency))))
          : 5;
      } else if (this.rateLimit != null) {
        updatePayload.rate_per_minute = this.rateLimit;
      }

      this.campaignService.updateCampaign(this.campaign.id, updatePayload).subscribe({
        next: () => {
          this.saving = false;
          this.complete.emit();
        },
        error: (err) => {
          this.saving = false;
          this.messageService.add({
            severity: 'error',
            summary: 'Update Failed',
            detail:
              err.error?.detail ||
              'Failed to update campaign. Check your inputs.',
            life: 6000,
          });
        },
      });
      return;
    }

    const createPayload: CampaignCreateRequest = {
      name: this.name.trim(),
      kind: this.kind,
      channel: this.channel,
      channel_account_id: this.needsChannelAccount ? this.channelAccountId : undefined,
      purpose: this.purpose,
      audience_type: this.audienceType,
      list_id: this.audienceType === 'list' ? this.audienceId : undefined,
      message_body: this.body.trim(),
      scheduled_at: this.scheduledAt || undefined,
    };

    if (this.kind === 'call') {
      createPayload.concurrency = this.concurrency
        ? Math.min(50, Math.max(1, Math.round(Number(this.concurrency))))
        : 5;
    } else if (this.rateLimit != null) {
      createPayload.rate_limit = this.rateLimit;
    }

    this.campaignService.createCampaign(createPayload).subscribe({
      next: () => {
        this.saving = false;
        this.complete.emit();
      },
      error: (err) => {
        this.saving = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Validation Error',
          detail:
            err.error?.detail ||
            'Failed to create campaign. Check your inputs.',
          life: 6000,
        });
      },
    });
  }

  onClose(): void {
    this.close.emit();
  }
}
