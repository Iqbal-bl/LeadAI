import { Component, OnInit } from '@angular/core';
import { Router, ActivatedRoute } from '@angular/router';
import { SharedModule } from '../../../shared/shared.module';
import { CampaignService } from '../../../services/campaign.service';
import { AuthService } from '../../../services/auth.service';
import {
  Campaign,
  CampaignStatus,
  CampaignPreview,
  CampaignRecipient,
  RestartMode,
} from '../../../models/campaign.models';
import { MessageService, MenuItem } from 'primeng/api';
import { CLIENT_PERMISSIONS } from '../../../modules/client/constants/permission.constants';
import { CampaignCreateComponent } from '../campaign-create/campaign-create.component';

@Component({
  selector: 'app-batch-list',
  standalone: true,
  imports: [SharedModule, CampaignCreateComponent],
  templateUrl: './batch-list.component.html',
  styleUrl: './batch-list.component.scss',
})
export class BatchListComponent implements OnInit {
  PERMISSIONS = CLIENT_PERMISSIONS;

  campaigns: Campaign[] = [];
  loading = true;
  canSend = false;
  startingCampaignId: string | null = null;

  showCreate = false;
  selectedCampaignForEdit: Campaign | null = null;
  selectedAudienceId = '';

  // Preview Dialog state
  showPreviewDialog = false;
  activePreviewCampaign: Campaign | null = null;
  activePreview: CampaignPreview | null = null;
  previewLoading = false;

  // Recipients Dialog state
  showRecipientsDialog = false;
  activeRecipientsCampaign: Campaign | null = null;
  recipients: CampaignRecipient[] = [];
  recipientsLoading = false;

  // Context Menu state
  campaignRowMenuItems: MenuItem[] = [];
  selectedCampaign: Campaign | null = null;

  constructor(
    private campaignService: CampaignService,
    private messageService: MessageService,
    private authService: AuthService,
    private router: Router,
    private route: ActivatedRoute,
  ) {}

  ngOnInit(): void {
    const user = this.authService.getCurrentUser();
    this.canSend = user?.permissions?.includes('campaign.send') ?? false;

    this.route.queryParams.subscribe((params) => {
      if (params['audienceId']) {
        this.selectedAudienceId = params['audienceId'];
      }
      if (params['create'] === 'true' || params['audienceId']) {
        this.openCreate();
      }
    });

    this.loadCampaigns();
  }

  isLeadCampaign(c: Campaign): boolean {
    const type = (c.campaign_type || '').toLowerCase().trim();
    if (
      type === 'lead' ||
      type === 'lead_campaign' ||
      type === 'leads' ||
      type.includes('lead')
    ) {
      return true;
    }
    if (c.created_via === 'import') {
      return true;
    }
    return false;
  }

  loadCampaigns(): void {
    this.loading = true;
    this.campaignService.getCampaigns({ campaign_type: 'lead_campaign' }).subscribe({
      next: (res: any) => {
        const raw = res.items ?? (Array.isArray(res) ? res : []);
        const mapped = raw.map((c: any) => ({
          ...c,
          counters: {
            total: c.total_count || 0,
            sent: c.sent_count || 0,
            delivered: c.delivered_count || 0,
            failed: c.failed_count || 0,
            replied: c.replied_count || 0,
          },
        }));
        this.campaigns = mapped.filter((c: any) => this.isLeadCampaign(c));
        this.loading = false;
      },
      error: () => {
        this.campaigns = [];
        this.loading = false;
      },
    });
  }

  goToImport(): void {
    this.router.navigate(['/client/leads/import'], {
      queryParams: { from: 'batches' },
    });
  }

  openCreate(): void {
    this.selectedCampaignForEdit = null;
    this.showCreate = true;
  }

  openEdit(campaign: Campaign): void {
    this.selectedCampaignForEdit = campaign;
    this.showCreate = true;
  }

  onCreateComplete(): void {
    const isEdit = !!this.selectedCampaignForEdit;
    this.showCreate = false;
    this.selectedCampaignForEdit = null;
    this.loadCampaigns();
    this.messageService.add({
      severity: 'success',
      summary: isEdit ? 'Batch Updated' : 'Batch Created',
      detail: isEdit
        ? 'Your campaign batch has been updated.'
        : 'Your campaign batch has been created.',
    });
  }

  onCreateClose(): void {
    this.showCreate = false;
    this.selectedCampaignForEdit = null;
  }

  viewCampaign(campaign: Campaign): void {
    this.router.navigate(['/client/campaigns', campaign.id]);
  }

  buildCampaign(campaign: Campaign): void {
    this.previewLoading = true;
    this.showPreviewDialog = true;
    this.activePreviewCampaign = campaign;
    this.activePreview = null;

    this.campaignService.buildCampaign(campaign.id).subscribe({
      next: () => {
        this.campaignService.previewCampaign(campaign.id).subscribe({
          next: (res: any) => {
            this.activePreview = {
              recipient_count: res.audience_size || 0,
              eta_minutes: Math.ceil(res.estimated_minutes || 0),
              warnings: (res.warnings || []).map((w: any) => {
                if (typeof w === 'string') {
                  return {
                    severity:
                      w.toLowerCase().includes('reject') ||
                      w.toLowerCase().includes('error')
                        ? 'error'
                        : 'warning',
                    message: w,
                  };
                }
                return w;
              }),
              sample_messages: (res.sample_messages || []).map((msg: any) => {
                if (typeof msg === 'string') {
                  return {
                    recipient: 'Sample Recipient',
                    rendered_body: msg,
                  };
                }
                return msg;
              }),
            };
            this.previewLoading = false;
            this.loadCampaigns();
          },
          error: () => {
            this.previewLoading = false;
            this.showPreviewDialog = false;
            this.messageService.add({
              severity: 'error',
              summary: 'Error',
              detail: 'Failed to load campaign preview.',
            });
          },
        });
      },
      error: () => {
        this.previewLoading = false;
        this.showPreviewDialog = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Failed to build audience.',
        });
      },
    });
  }

  startFromPreview(): void {
    if (!this.activePreviewCampaign) return;
    const campaign = this.activePreviewCampaign;
    this.showPreviewDialog = false;
    this.startCampaignWithMode(campaign, 'all');
  }

  startCampaignWithMode(campaign: Campaign, mode: RestartMode = 'all'): void {
    if (this.startingCampaignId) return;
    this.startingCampaignId = campaign.id;
    this.campaignService.startCampaign(campaign.id, mode).subscribe({
      next: () => {
        this.startingCampaignId = null;
        this.messageService.add({
          severity: 'success',
          summary: 'Campaign Started',
          detail: `"${campaign.name}" started with mode: ${mode}.`,
        });
        this.loadCampaigns();
      },
      error: (err) => {
        this.startingCampaignId = null;
        this.messageService.add({
          severity: 'error',
          summary: 'Start Failed',
          detail:
            err.error?.detail ||
            err.message ||
            'Failed to start campaign with selected mode.',
        });
      },
    });
  }

  pauseCampaign(campaign: Campaign): void {
    this.campaignService.pauseCampaign(campaign.id).subscribe({
      next: () => {
        this.messageService.add({
          severity: 'info',
          summary: 'Campaign Paused',
          detail: `Campaign "${campaign.name}" has been paused.`,
        });
        this.loadCampaigns();
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Failed to pause campaign.',
        });
      },
    });
  }

  resumeCampaign(campaign: Campaign): void {
    this.campaignService.resumeCampaign(campaign.id).subscribe({
      next: () => {
        this.messageService.add({
          severity: 'success',
          summary: 'Campaign Resumed',
          detail: `Campaign "${campaign.name}" has resumed.`,
        });
        this.loadCampaigns();
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Failed to resume campaign.',
        });
      },
    });
  }

  viewRecipients(campaign: Campaign): void {
    this.showRecipientsDialog = true;
    this.activeRecipientsCampaign = campaign;
    this.recipientsLoading = true;
    this.recipients = [];

    this.campaignService.getRecipients(campaign.id).subscribe({
      next: (res: any) => {
        this.recipients = Array.isArray(res) ? res : res?.items || [];
        this.recipientsLoading = false;
      },
      error: () => {
        this.recipients = [];
        this.recipientsLoading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Failed to load recipients list.',
        });
      },
    });
  }

  openActionMenu(event: Event, campaign: Campaign, menu: any): void {
    event.stopPropagation();
    this.selectedCampaign = campaign;
    const hasFailures =
      (campaign.counters?.failed ?? 0) > 0 ||
      ((campaign as any).failed_count ?? 0) > 0;
    const hasQueued =
      (campaign.counters?.queued ?? 0) > 0 ||
      ((campaign as any).queued_count ?? 0) > 0;
    const isRunning = campaign.status === 'running';
    const isPaused = campaign.status === 'paused';
    const isDraft = campaign.status === 'draft';

    const items: MenuItem[] = [
      {
        label: 'View Details',
        icon: 'pi pi-eye',
        command: () => this.viewCampaign(campaign),
      },
      {
        label: 'Edit Campaign',
        icon: 'pi pi-pencil',
        disabled: isRunning,
        title: isRunning ? 'Pause the campaign before editing' : '',
        command: () => this.openEdit(campaign),
      },
    ];

    if (isDraft) {
      items.push({
        label: 'Build Audience',
        icon: 'pi pi-cog',
        command: () => this.buildCampaign(campaign),
      });
    }

    if (isRunning) {
      items.push({
        label: 'Pause Campaign',
        icon: 'pi pi-pause',
        command: () => this.pauseCampaign(campaign),
      });
    } else if (isPaused) {
      items.push({
        label: 'Resume Campaign',
        icon: 'pi pi-play',
        command: () => this.resumeCampaign(campaign),
      });
    }

    items.push({
      separator: true,
    });

    items.push({
      label: 'Restart / Start (All)',
      icon: 'pi pi-refresh',
      disabled: !this.canSend || this.startingCampaignId !== null,
      command: () => this.startCampaignWithMode(campaign, 'all'),
    });

    items.push({
      label: 'Retry Failed Only',
      icon: 'pi pi-replay',
      disabled:
        !this.canSend || !hasFailures || this.startingCampaignId !== null,
      command: () => this.startCampaignWithMode(campaign, 'failed_only'),
    });

    items.push({
      label: 'Resume Pending Only',
      icon: 'pi pi-play',
      disabled: !this.canSend || !hasQueued || this.startingCampaignId !== null,
      command: () => this.startCampaignWithMode(campaign, 'pending_only'),
    });

    if (campaign.status !== 'draft' && campaign.status !== 'building') {
      items.push({
        separator: true,
      });
      items.push({
        label: 'View Recipients',
        icon: 'pi pi-users',
        command: () => this.viewRecipients(campaign),
      });
    }

    this.campaignRowMenuItems = items;
    menu.toggle(event);
  }

  getStatusSeverity(
    status: CampaignStatus,
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
      draft: 'secondary',
      building: 'info',
      ready: 'info',
      running: 'success',
      paused: 'warn',
      cancelled: 'danger',
      completed: 'success',
      failed: 'danger',
    };
    return map[status] || 'secondary';
  }

  getChannelIcon(channel: string): string {
    const icons: Record<string, string> = {
      whatsapp: 'pi pi-whatsapp',
      messenger: 'pi pi-facebook',
      instagram: 'pi pi-instagram',
      sms: 'pi pi-mobile',
      email: 'pi pi-envelope',
      voice: 'pi pi-phone',
    };
    return icons[channel] || 'pi pi-comment';
  }

  getProgressPercent(campaign: Campaign): number {
    if (!campaign.counters || campaign.counters.total === 0) return 0;
    return Math.round(
      ((campaign.counters.sent +
        campaign.counters.delivered +
        campaign.counters.failed) /
        campaign.counters.total) *
        100,
    );
  }

  getWarningSeverity(severity: string): string {
    if (severity === 'error') return 'rgba(239, 68, 68, 0.1)';
    if (severity === 'warning') return 'rgba(245, 158, 11, 0.1)';
    return 'rgba(59, 130, 246, 0.1)';
  }

  getWarningColor(severity: string): string {
    if (severity === 'error') return '#f87171';
    if (severity === 'warning') return '#fbbf24';
    return '#60a5fa';
  }

  getRecipientSeverity(
    status: string,
  ): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
    const map: Record<
      string,
      'success' | 'info' | 'warn' | 'danger' | 'secondary'
    > = {
      pending: 'secondary',
      queued: 'info',
      sent: 'success',
      delivered: 'success',
      read: 'success',
      replied: 'success',
      failed: 'danger',
      skipped: 'warn',
    };
    return map[status] || 'secondary';
  }
}
