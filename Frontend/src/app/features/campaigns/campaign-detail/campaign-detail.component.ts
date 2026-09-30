import { Component, OnInit, OnDestroy } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { SharedModule } from '../../../shared/shared.module';
import { CampaignService } from '../../../services/campaign.service';
import { AuthService } from '../../../services/auth.service';
import {
  Campaign,
  CampaignBatchMeta,
  CampaignHistoryItem,
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
  progressPercent = 0;

  canSend = false;
  private pollTimer: any = null;

  // Run History & Recipients State
  activeTab: string = 'history';
  historyItems: CampaignHistoryItem[] = [];
  historyLoading = false;
  historyTotal = 0;

  allRecipients: CampaignRecipient[] = [];
  recipientsLoading = false;
  recipientStatusFilter = '';
  recipientStatusOptions = [
    { label: 'All Statuses', value: '' },
    { label: 'Queued', value: 'queued' },
    { label: 'Sent', value: 'sent' },
    { label: 'Delivered', value: 'delivered' },
    { label: 'Failed', value: 'failed' },
    { label: 'Skipped', value: 'skipped' },
  ];

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
            total: c.total_count ?? 0,
            queued: c.queued_count ?? 0,
            sent: c.sent_count ?? 0,
            delivered: c.delivered_count ?? 0,
            read: c.read_count ?? 0,
            replied: c.replied_count ?? 0,
            failed: c.failed_count ?? 0,
            skipped: c.skipped_count ?? 0,
            leads_created: c.leads_created ?? 0,
          },
        };
        const cnt = this.campaign?.counters;
        this.progressPercent =
          cnt && cnt.total > 0
            ? Math.round(
                ((cnt.sent + cnt.delivered + cnt.failed) / cnt.total) * 100,
              )
            : 0;
        this.loading = false;

        this.loadHistory(id);
        this.loadRecipients(id);

        if (
          this.campaign?.status === 'running' ||
          this.campaign?.status === 'building'
        ) {
          this.startPolling(id);
        }
        if (
          this.campaign?.status === 'ready' ||
          this.campaign?.status === 'running'
        ) {
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

  loadHistory(id: string): void {
    this.historyLoading = true;
    this.campaignService.getHistory(id).subscribe({
      next: (res) => {
        this.historyItems = res.items || [];
        this.historyTotal = res.total_items || 0;
        this.historyLoading = false;
      },
      error: () => {
        this.historyItems = [];
        this.historyLoading = false;
      },
    });
  }

  loadRecipients(id: string): void {
    this.recipientsLoading = true;
    this.campaignService.getRecipients(id, this.recipientStatusFilter || undefined).subscribe({
      next: (res: any) => {
        this.allRecipients = Array.isArray(res) ? res : res?.items || [];
        this.recipientsLoading = false;
      },
      error: () => {
        this.allRecipients = [];
        this.recipientsLoading = false;
      },
    });
  }

  onRecipientStatusChange(): void {
    if (this.campaign) {
      this.loadRecipients(this.campaign.id);
    }
  }

  get isDeferred(): boolean {
    if (!this.campaign) return false;
    const msg = (this.campaign.status_message || '').toLowerCase();
    if (msg.includes('quiet hours') || msg.includes('resumes')) return true;
    const latest = this.historyItems[0];
    if (
      latest &&
      latest.action === 'campaign.deferred' &&
      this.campaign.status !== 'completed' &&
      this.campaign.status !== 'cancelled'
    ) {
      return true;
    }
    return false;
  }

  get deferredResumeTime(): string | null {
    const match = (this.campaign?.status_message || '').match(/resumes\s+([0-9:]+)/i);
    if (match) return match[1];
    const deferredEvent = this.historyItems.find((h) => h.action === 'campaign.deferred');
    if (deferredEvent?.meta?.resume_at) {
      return deferredEvent.meta.resume_at;
    }
    return null;
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
      },
      error: () => {
        this.previewLoading = false;
      },
    });
  }

  loadFailedRecipients(id: string): void {
    this.campaignService.getRecipients(id, 'failed').subscribe({
      next: (res: any) => {
        this.failedRecipients = Array.isArray(res) ? res : res?.items || [];
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
          this.campaign = {
            ...c,
            counters: {
              total: c.total_count ?? 0,
              queued: c.queued_count ?? 0,
              sent: c.sent_count ?? 0,
              delivered: c.delivered_count ?? 0,
              read: c.read_count ?? 0,
              replied: c.replied_count ?? 0,
              failed: c.failed_count ?? 0,
              skipped: c.skipped_count ?? 0,
              leads_created: c.leads_created ?? 0,
            },
          };
          this.loadHistory(id);
          if (
            this.campaign?.status !== 'running' &&
            this.campaign?.status !== 'building'
          ) {
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
        this.messageService.add({
          severity: 'info',
          summary: 'Building',
          detail: 'Materialising recipient rows...',
        });
        this.loadCampaign(this.campaign!.id);
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Build failed.',
        });
      },
    });
  }

  startingCampaign = false;

  canStartCampaign(): boolean {
    const s = this.campaign?.status;
    return s === 'ready' || s === 'scheduled';
  }

  startCampaign(): void {
    if (!this.campaign || this.startingCampaign) return;
    this.confirmationService.confirm({
      message: `Start sending to ${this.campaign.counters?.total || 0} recipients? This action spends money and reaches real people.`,
      header: 'Start Campaign',
      icon: 'pi pi-send',
      accept: () => {
        if (!this.campaign || this.startingCampaign) return;
        this.startingCampaign = true;
        this.campaignService.startCampaign(this.campaign.id).subscribe({
          next: () => {
            this.startingCampaign = false;
            this.messageService.add({
              severity: 'success',
              summary: 'Campaign Started',
              detail: 'Campaign is now queued and sending.',
            });
            this.loadCampaign(this.campaign!.id);
          },
          error: (err) => {
            this.startingCampaign = false;
            this.messageService.add({
              severity: 'error',
              summary: 'Error',
              detail: err.error?.detail || 'Failed to start campaign.',
            });
          },
        });
      },
    });
  }

  pauseCampaign(): void {
    if (!this.campaign) return;
    this.campaignService.pauseCampaign(this.campaign.id).subscribe({
      next: () => {
        this.messageService.add({
          severity: 'warn',
          summary: 'Paused',
          detail: 'Campaign has been paused.',
        });
        this.loadCampaign(this.campaign!.id);
      },
    });
  }

  resumeCampaign(): void {
    if (!this.campaign) return;
    this.campaignService.resumeCampaign(this.campaign.id).subscribe({
      next: () => {
        this.messageService.add({
          severity: 'success',
          summary: 'Resumed',
          detail: 'Campaign is sending again.',
        });
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
            this.messageService.add({
              severity: 'info',
              summary: 'Cancelled',
              detail: 'Campaign has been cancelled.',
            });
            this.loadCampaign(this.campaign!.id);
          },
        });
      },
    });
  }

  retryFailed(): void {
    if (!this.campaign) return;
    this.confirmationService.confirm({
      message:
        'Retry all permanently failed recipients? This will NOT re-send to people who already received the message.',
      header: 'Retry Failed',
      icon: 'pi pi-replay',
      accept: () => {
        this.campaignService.retryFailed(this.campaign!.id).subscribe({
          next: () => {
            this.messageService.add({
              severity: 'info',
              summary: 'Retrying',
              detail: 'Failed recipients are being retried.',
            });
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
    if (!this.campaign?.counters || this.campaign.counters.total === 0)
      return 0;
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

  getStatusSeverity(status: string): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
    switch ((status || '').toLowerCase()) {
      case 'running':
      case 'sent':
      case 'completed':
        return 'success';
      case 'scheduled':
      case 'queued':
        return 'info';
      case 'paused':
      case 'building':
      case 'draft':
        return 'warn';
      case 'cancelled':
      case 'failed':
        return 'danger';
      default:
        return 'secondary';
    }
  }

  getActionLabel(action: string): string {
    const map: Record<string, string> = {
      'campaign.created': 'Campaign Created',
      'campaign.audience_built': 'Audience Materialised',
      'campaign.started': 'Campaign Started',
      'campaign.batch_processed': 'Batch Processed',
      'campaign.deferred': 'Outreach Deferred',
      'campaign.paused': 'Campaign Paused',
      'campaign.resumed': 'Campaign Resumed',
      'campaign.cancelled': 'Campaign Cancelled',
      'campaign.completed': 'Campaign Completed',
      'campaign.failed': 'Campaign Failed',
    };
    return map[action] || action.replace(/^campaign\./, '').replace(/_/g, ' ');
  }

  getActionIcon(action: string): string {
    const map: Record<string, string> = {
      'campaign.created': 'pi pi-plus-circle',
      'campaign.audience_built': 'pi pi-users',
      'campaign.started': 'pi pi-send',
      'campaign.batch_processed': 'pi pi-bolt',
      'campaign.deferred': 'pi pi-clock',
      'campaign.paused': 'pi pi-pause',
      'campaign.resumed': 'pi pi-play',
      'campaign.cancelled': 'pi pi-times-circle',
      'campaign.completed': 'pi pi-check-circle',
      'campaign.failed': 'pi pi-exclamation-triangle',
    };
    return map[action] || 'pi pi-info-circle';
  }

  getActionSeverity(action: string): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
    switch (action) {
      case 'campaign.started':
      case 'campaign.resumed':
      case 'campaign.completed':
        return 'success';
      case 'campaign.deferred':
      case 'campaign.paused':
        return 'warn';
      case 'campaign.cancelled':
      case 'campaign.failed':
        return 'danger';
      case 'campaign.batch_processed':
      case 'campaign.audience_built':
      case 'campaign.created':
        return 'info';
      default:
        return 'secondary';
    }
  }

  getActionColor(action: string): string {
    switch (action) {
      case 'campaign.started':
      case 'campaign.resumed':
      case 'campaign.completed':
        return '#10b981';
      case 'campaign.deferred':
      case 'campaign.paused':
        return '#f59e0b';
      case 'campaign.cancelled':
      case 'campaign.failed':
        return '#ef4444';
      case 'campaign.batch_processed':
        return '#06b6d4';
      case 'campaign.audience_built':
        return '#8b5cf6';
      case 'campaign.created':
        return '#3b82f6';
      default:
        return '#64748b';
    }
  }

  getActionBg(action: string): string {
    switch (action) {
      case 'campaign.started':
      case 'campaign.resumed':
      case 'campaign.completed':
        return 'rgba(16, 185, 129, 0.15)';
      case 'campaign.deferred':
      case 'campaign.paused':
        return 'rgba(245, 158, 11, 0.15)';
      case 'campaign.cancelled':
      case 'campaign.failed':
        return 'rgba(239, 68, 68, 0.15)';
      case 'campaign.batch_processed':
        return 'rgba(6, 182, 212, 0.15)';
      case 'campaign.audience_built':
        return 'rgba(139, 92, 246, 0.15)';
      case 'campaign.created':
        return 'rgba(59, 130, 246, 0.15)';
      default:
        return 'rgba(100, 116, 139, 0.15)';
    }
  }

  getRelativeTime(dateStr: string): string {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHr = Math.floor(diffMin / 60);
    const diffDays = Math.floor(diffHr / 24);

    if (diffSec < 45) return 'Just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHr < 24) return `${diffHr}h ago`;
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  formatFullDate(dateStr: string | null | undefined): string {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    return isNaN(d.getTime())
      ? ''
      : d.toLocaleDateString(undefined, {
          day: 'numeric',
          month: 'short',
          year: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        });
  }

  getRecipientSeverity(
    status: string,
  ): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
    switch ((status || '').toLowerCase()) {
      case 'sent':
      case 'delivered':
      case 'read':
      case 'replied':
        return 'success';
      case 'queued':
      case 'sending':
        return 'info';
      case 'skipped':
      case 'opted_out':
        return 'warn';
      case 'failed':
        return 'danger';
      default:
        return 'secondary';
    }
  }
}
