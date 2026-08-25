import { Component, EventEmitter, Input, Output } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { CampaignService } from '../../../services/campaign.service';
import { ContactListService } from '../../../services/contact-list.service';
import { ContactList } from '../../../models/contact-list.models';
import {
  CampaignKind,
  CampaignChannel,
  CampaignPurpose,
  AudienceType,
} from '../../../models/campaign.models';
import { MessageService } from 'primeng/api';

@Component({
  selector: 'app-campaign-create',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './campaign-create.component.html',
  styleUrl: './campaign-create.component.scss',
})
export class CampaignCreateComponent {
  @Input() visible = false;
  @Output() complete = new EventEmitter<void>();
  @Output() close = new EventEmitter<void>();

  name = '';
  kind: CampaignKind = 'message';
  channel: CampaignChannel = 'whatsapp';
  purpose: CampaignPurpose = 'promotional';
  audienceType: AudienceType = 'list';
  audienceId = '';
  body = '';
  scheduledAt = '';
  concurrency: number | null = null;
  rateLimit: number | null = null;

  saving = false;
  contactLists: ContactList[] = [];

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
    private messageService: MessageService,
  ) {
    this.loadContactLists();
  }

  loadContactLists(): void {
    this.contactListService.getLists().subscribe({
      next: (lists) => {
        this.contactLists = lists;
      },
    });
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

  get canSave(): boolean {
    return !!this.name.trim() && !!this.body.trim();
  }

  save(): void {
    this.saving = true;
    this.campaignService
      .createCampaign({
        name: this.name,
        kind: this.kind,
        channel: this.channel,
        purpose: this.purpose,
        audience_type: this.audienceType,
        list_id: this.audienceType === 'list' ? this.audienceId : undefined,
        message_body: this.body,
        scheduled_at: this.scheduledAt || undefined,
        concurrency: this.concurrency ?? undefined,
        rate_limit: this.rateLimit ?? undefined,
      })
      .subscribe({
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
