import { Component, OnInit, OnDestroy } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { SharedModule } from '../../../shared/shared.module';
import { CampaignService } from '../../../services/campaign.service';
import { AuthService } from '../../../services/auth.service';
import {
  Campaign,
  CampaignPreview,
  CampaignRecipient,
} from '../../../models/campaign.models';
import { MessageService } from 'primeng/api';
import { ConfirmationService } from '../../../shared/services/confirmation.service';

@Component({
  selector: 'app-campaign-detail',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './campaign-detail.component.html',
  styleUrl: './campaign-detail.component.scss',
})
export class CampaignDetailComponent implements OnInit, OnDestroy {
  campaign: Campaign | null = null;
  preview: CampaignPreview | null = null;
  failedRecipients: CampaignRecipient[] = [];
  loading = true;
  previewLoading = false;

  canSend = false;
  private pollTimer: any = null;

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private campaignService: CampaignService,
    private authService: AuthService,
    private messageService: MessageService,
    private confirmationService: ConfirmationService,
  ) {}

  ngOnInit(): void {
    const user = this.authService.getCurrentUser();
    this.canSend = user?.permissions?.includes('campaign.send') ?? false;

    const id = this.route.snapshot.paramMap.get('id');
    if (id) {
      this.loadCampaign(id);
    }
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  loadCampaign(id: string): void {
    this.loading = true;
    this.campaignService.getCampaign(id).subscribe({
      next: (c: any) => {
        this.campaign = {
          ...c,
          counters: {
            total: c.total_count || 0,
            sent: c.sent_count || 0,
            delivered: c.delivered_count || 0,
            failed: c.failed_count || 0,
            replied: c.replied_count || 0
          }
        };
        this.loading = false;

        if (this.campaign?.status === 'running' || this.campaign?.status === 'building') {
          this.startPolling(id);
        }
        if (this.campaign?.status === 'ready' || this.campaign?.status === 'running') {
          this.loadPreview(id);
        }
        if (this.campaign?.counters && this.campaign.counters.failed > 0) {
          this.loadFailedRecipients(id);
        }
      },
      error: () => {
        this.loading = false;
      },
    });
  }

  loadPreview(id: string): void {
    this.previewLoading = true;
    this.campaignService.previewCampaign(id).subscribe({
      next: (res: any) => {
        this.preview = {
          recipient_count: res.audience_size || 0,
          eta_minutes: Math.ceil(res.estimated_minutes || 0),
          warnings: (res.warnings || []).map((w: any) => {
            if (typeof w === 'string') {
              return {
                severity: w.toLowerCase().includes('reject') || w.toLowerCase().includes('error') ? 'error' : 'warning',
                message: w
              };
            }
            return w;
          }),
          sample_messages: (res.sample_messages || []).map((msg: any) => {
            if (typeof msg === 'string') {
              return {
                recipient: 'Sample Recipient',
                rendered_body: msg
              };
            }
            return msg;
          })
        };
        this.previewLoading = false;
      },
      error: () => {
        this.previewLoading = false;
      },
    });
  }

  loadFailedRecipients(id: string): void {
    this.campaignService.getRecipients(id, 'failed').subscribe({
      next: (res: any) => {
        this.failedRecipients = Array.isArray(res) ? res : (res?.items || []);
      },
      error: () => {
        this.failedRecipients = [];
      },
    });
  }

  startPolling(id: string): void {
    this.stopPolling();
    this.pollTimer = setInterval(() => {
      this.campaignService.getCampaign(id).subscribe({
        next: (c: any) => {
          const oldStatus = this.campaign?.status;
          this.campaign = {
            ...c,
            counters: {
              total: c.total_count || 0,
              sent: c.sent_count || 0,
              delivered: c.delivered_count || 0,
              failed: c.failed_count || 0,
              replied: c.replied_count || 0
            }
          };
          if (this.campaign?.status !== 'running' && this.campaign?.status !== 'building') {
            this.stopPolling();
            if (this.campaign?.status === 'ready') {
              this.loadPreview(id);
            }
          }
          if (this.campaign?.counters && this.campaign.counters.failed > 0) {
            this.loadFailedRecipients(id);
          }
        },
      });
    }, 5000);
  }

  stopPolling(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  buildCampaign(): void {
    if (!this.campaign) return;
    this.campaignService.buildCampaign(this.campaign.id).subscribe({
      next: () => {
        this.messageService.add({ severity: 'info', summary: 'Building', detail: 'Materialising recipient rows...' });
        this.loadCampaign(this.campaign!.id);
      },
      error: () => {
        this.messageService.add({ severity: 'error', summary: 'Error', detail: 'Build failed.' });
      },
    });
  }

  startCampaign(): void {
    if (!this.campaign) return;
    this.confirmationService.confirm({
      message: `Start sending to ${this.campaign.counters?.total || 0} recipients? This action spends money and reaches real people.`,
      header: 'Start Campaign',
      icon: 'pi pi-send',
      accept: () => {
        this.campaignService.startCampaign(this.campaign!.id).subscribe({
          next: () => {
            this.messageService.add({ severity: 'success', summary: 'Started', detail: 'Campaign is now queued and sending.' });
            this.loadCampaign(this.campaign!.id);
          },
          error: () => {
            this.messageService.add({ severity: 'error', summary: 'Error', detail: 'Failed to start campaign.' });
          },
        });
      },
    });
  }

  pauseCampaign(): void {
    if (!this.campaign) return;
    this.campaignService.pauseCampaign(this.campaign.id).subscribe({
      next: () => {
        this.messageService.add({ severity: 'warn', summary: 'Paused', detail: 'Campaign has been paused.' });
        this.loadCampaign(this.campaign!.id);
      },
    });
  }

  resumeCampaign(): void {
    if (!this.campaign) return;
    this.campaignService.resumeCampaign(this.campaign.id).subscribe({
      next: () => {
        this.messageService.add({ severity: 'success', summary: 'Resumed', detail: 'Campaign is sending again.' });
        this.loadCampaign(this.campaign!.id);
      },
    });
  }

  cancelCampaign(): void {
    if (!this.campaign) return;
    this.confirmationService.confirm({
      message: 'Cancel this campaign? Unsent messages will not be delivered.',
      header: 'Cancel Campaign',
      icon: 'pi pi-times',
      acceptButtonStyleClass: 'p-button-danger',
      accept: () => {
        this.campaignService.cancelCampaign(this.campaign!.id).subscribe({
          next: () => {
            this.messageService.add({ severity: 'info', summary: 'Cancelled', detail: 'Campaign has been cancelled.' });
            this.loadCampaign(this.campaign!.id);
          },
        });
      },
    });
  }

  retryFailed(): void {
    if (!this.campaign) return;
    this.confirmationService.confirm({
      message: 'Retry all permanently failed recipients? This will NOT re-send to people who already received the message.',
      header: 'Retry Failed',
      icon: 'pi pi-replay',
      accept: () => {
        this.campaignService.retryFailed(this.campaign!.id).subscribe({
          next: () => {
            this.messageService.add({ severity: 'info', summary: 'Retrying', detail: 'Failed recipients are being retried.' });
            this.loadCampaign(this.campaign!.id);
          },
        });
      },
    });
  }

  goBack(): void {
    this.router.navigate(['/client/campaigns']);
  }

  getProgressPercent(): number {
    if (!this.campaign?.counters || this.campaign.counters.total === 0) return 0;
    const c = this.campaign.counters;
    return Math.round(((c.sent + c.delivered + c.failed) / c.total) * 100);
  }

  getWarningSeverity(severity: string): string {
    const map: Record<string, string> = {
      error: 'rgba(239,68,68,0.1)',
      warn: 'rgba(245,158,11,0.1)',
      info: 'rgba(99,102,241,0.1)',
    };
    return map[severity] || map['info'];
  }

  getWarningColor(severity: string): string {
    const map: Record<string, string> = {
      error: '#ef4444',
      warn: '#f59e0b',
      info: '#6366f1',
    };
    return map[severity] || map['info'];
  }
}
