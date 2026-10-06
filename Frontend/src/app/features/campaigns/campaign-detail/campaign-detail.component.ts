import { Component, OnInit, OnDestroy } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { SharedModule } from '../../../shared/shared.module';
import { CampaignService } from '../../../services/campaign.service';
import { AuthService } from '../../../services/auth.service';
import { VoiceService } from '../../../services/voice.service';
import { CallTranscript } from '../../../models/voice.models';
import {
  Campaign,
  CampaignBatchMeta,
  CampaignHistoryItem,
  CampaignPreview,
  CampaignRecipient,
  CampaignExecution,
  CampaignExecutionListResponse,
  CampaignExecutionAttempt,
  CampaignExecutionAttemptsResponse,
  RestartMode,
} from '../../../models/campaign.models';
import { MessageService, MenuItem } from 'primeng/api';
import { ConfirmationService } from '../../../shared/services/confirmation.service';
import { CampaignCreateComponent } from '../campaign-create/campaign-create.component';
import {
  LeadConversationsComponent,
  CallMetadataInfo,
} from '../../leads/components/lead-conversations/lead-conversations.component';
import { Location } from '@angular/common';

@Component({
  selector: 'app-campaign-detail',
  standalone: true,
  imports: [SharedModule, CampaignCreateComponent, LeadConversationsComponent],
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

  showEditDialog = false;

  canSend = false;
  private pollTimer: any = null;

  // Run History & Recipients State (Tab lazy loading)
  activeTab: string = 'executions';
  loadedTabs: Set<string> = new Set<string>();
  historyItems: CampaignHistoryItem[] = [];
  historyLoading = false;
  historyTotal = 0;

  // Tracked Executions State (GET /executions)
  executions: CampaignExecution[] = [];
  executionsLoading = false;
  executionsTotal = 0;
  startModeMenuItems: MenuItem[] = [];

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

  // Execution & Batch State
  retryMode: 'failed_only' | 'restart_all' = 'failed_only';
  isRestarting = false;

  // Imported Lead Batch Specific View State
  batchActiveTab: 'caller_info' | 'execution_history' = 'caller_info';
  historySortAsc = false;
  historyFilterText = '';
  activeRecipientMenuItems: MenuItem[] = [];
  selectedRecipientForTranscript: CampaignRecipient | null = null;
  showTranscriptDialog = false;

  // Execution History Accordion & Attempts State
  expandedExecutionIds: string[] = [];
  executionAttempts: Record<string, CampaignExecutionAttempt[]> = {};
  executionAttemptsLoading: Record<string, boolean> = {};
  executionAttemptsError: Record<string, string | null> = {};

  // Real Call Transcript Dialog State (matching Lead Detail)
  transcriptVisible = false;
  transcriptLoading = false;
  activeTranscript: CallTranscript | null = null;
  selectedAttemptForTranscript: CampaignExecutionAttempt | null = null;
  dialogConversations: any[] = [];

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private campaignService: CampaignService,
    private authService: AuthService,
    private voiceService: VoiceService,
    private messageService: MessageService,
    private confirmationService: ConfirmationService,
    public location: Location,
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

  openEditDialog(): void {
    this.showEditDialog = true;
  }

  onEditComplete(): void {
    this.showEditDialog = false;
    this.loadedTabs.clear();
    const id = this.campaign?.id || this.route.snapshot.paramMap.get('id');
    if (id) {
      this.loadCampaign(id);
    }
  }

  onTabChange(tab: any): void {
    if (!tab) return;
    this.activeTab = tab;
    const id = this.campaign?.id || this.route.snapshot.paramMap.get('id');
    if (id) {
      this.loadTabData(tab, id);
    }
  }

  loadTabData(tab: string, id: string, force: boolean = false): void {
    if (!force && this.loadedTabs.has(tab)) return;
    this.loadedTabs.add(tab);
    switch (tab) {
      case 'executions':
        this.loadExecutions(id);
        break;
      case 'history':
        this.loadHistory(id);
        break;
      case 'recipients':
        this.loadRecipients(id);
        break;
    }
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

        // Lazy load ONLY the active tab data
        this.loadTabData(this.activeTab, id, true);

        if (
          this.campaign?.status === 'running' ||
          this.campaign?.status === 'queued' ||
          this.campaign?.status === 'building' ||
          this.campaign?.status === 'scheduled'
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
    this.campaignService
      .getRecipients(id, this.recipientStatusFilter || undefined)
      .subscribe({
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
    const match = (this.campaign?.status_message || '').match(
      /resumes\s+([0-9:]+)/i,
    );
    if (match) return match[1];
    const deferredEvent = this.historyItems.find(
      (h) => h.action === 'campaign.deferred',
    );
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
          if (this.loadedTabs.has('executions')) {
            this.loadExecutions(id);
          }
          if (this.loadedTabs.has('history')) {
            this.loadHistory(id);
          }
          if (this.loadedTabs.has('recipients')) {
            this.loadRecipients(id);
          }
          if (
            this.campaign?.status !== 'running' &&
            this.campaign?.status !== 'queued' &&
            this.campaign?.status !== 'building' &&
            this.campaign?.status !== 'scheduled'
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

  get hasPriorExecutions(): boolean {
    return (
      this.executionsTotal > 0 ||
      this.executions.length > 0 ||
      (this.campaign?.status !== 'draft' &&
        this.campaign?.status !== 'ready' &&
        this.campaign?.status !== 'scheduled')
    );
  }

  canStartCampaign(): boolean {
    const s = this.campaign?.status;
    const totalCount =
      this.campaign?.total_count ?? this.campaign?.counters?.total ?? 0;

    // Don't show when status is running, queued, or paused
    if (s === 'running' || s === 'queued' || s === 'paused') {
      return false;
    }

    // Show when status is "draft" (first run) or "completed"/"cancelled" (restart), and total_count > 0
    return (
      (s === 'draft' ||
        s === 'ready' ||
        s === 'scheduled' ||
        s === 'completed' ||
        s === 'cancelled') &&
      totalCount > 0
    );
  }

  loadExecutions(id: string): void {
    this.executionsLoading = true;
    this.campaignService.getExecutions(id).subscribe({
      next: (res) => {
        this.executions = res.items || [];
        this.executionsTotal = res.total_items || 0;
        this.executionsLoading = false;
        this.updateStartModeMenu();
      },
      error: () => {
        this.executions = [];
        this.executionsTotal = 0;
        this.executionsLoading = false;
        this.updateStartModeMenu();
      },
    });
  }

  updateStartModeMenu(): void {
    const failedCount =
      this.campaign?.counters?.failed ??
      (this.campaign as any)?.failed_count ??
      0;
    const queuedCount =
      this.campaign?.counters?.queued ??
      (this.campaign as any)?.queued_count ??
      0;

    this.startModeMenuItems = [
      {
        label: 'Restart All',
        icon: 'pi pi-refresh',
        command: () => this.startCampaign('all'),
      },
      {
        label: 'Retry Failed Only',
        icon: 'pi pi-replay',
        disabled: failedCount === 0 && this.hasPriorExecutions,
        command: () => this.startCampaign('failed_only'),
      },
      {
        label: 'Resume Pending Only',
        icon: 'pi pi-play',
        disabled: queuedCount === 0 && this.hasPriorExecutions,
        command: () => this.startCampaign('pending_only'),
      },
    ];
  }

  openStartMenu(event: Event, menu: any): void {
    event.stopPropagation();
    this.updateStartModeMenu();
    menu.toggle(event);
  }

  exportExecution(executionId: string): void {
    if (!this.campaign) return;
    this.campaignService.downloadExport(this.campaign.id, executionId);
    this.messageService.add({
      severity: 'info',
      summary: 'Export Started',
      detail: `Downloading report for execution ${executionId}...`,
    });
  }

  onAccordionValueChange(val: any): void {
    const ids: string[] = Array.isArray(val) ? val : val ? [val] : [];
    this.expandedExecutionIds = ids;
    for (const id of ids) {
      if (
        id &&
        this.executionAttempts[id] === undefined &&
        !this.executionAttemptsLoading[id]
      ) {
        this.loadExecutionAttempts(id);
      }
    }
  }

  loadExecutionAttempts(executionId: string, force: boolean = false): void {
    const campaignId =
      this.campaign?.id || this.route.snapshot.paramMap.get('id');
    if (!campaignId || !executionId) return;
    if (!force && this.executionAttempts[executionId] !== undefined) return;

    this.executionAttemptsLoading[executionId] = true;
    this.executionAttemptsError[executionId] = null;

    this.campaignService
      .getExecutionAttempts(campaignId, executionId)
      .subscribe({
        next: (res: CampaignExecutionAttemptsResponse) => {
          this.executionAttempts[executionId] = res.items || [];
          this.executionAttemptsLoading[executionId] = false;
        },
        error: (err: any) => {
          this.executionAttempts[executionId] = [];
          this.executionAttemptsLoading[executionId] = false;
          this.executionAttemptsError[executionId] =
            err?.error?.detail ||
            'Failed to load call attempts for this execution run.';
        },
      });
  }

  getTranscriptDialogHeader(): string {
    const name = this.selectedAttemptForTranscript?.name;
    const mode =
      this.activeTranscript?.mode === 'ai_voice'
        ? 'AI Voice Agent'
        : 'Human Specialist';
    let title = 'Call Transcript';
    if (name) {
      title += ` — ${name}`;
    }
    if (this.activeTranscript?.mode) {
      title += ` (${mode})`;
    }
    return title;
  }

  get callMetadataForDialog(): CallMetadataInfo | null {
    if (!this.activeTranscript && !this.selectedAttemptForTranscript)
      return null;
    return {
      status:
        this.activeTranscript?.status ||
        this.selectedAttemptForTranscript?.call_status ||
        undefined,
      duration: this.formatDuration(
        this.activeTranscript?.duration_sec ??
          this.selectedAttemptForTranscript?.call_duration_sec ??
          0,
      ),
      phone:
        this.activeTranscript?.phone_masked ||
        this.selectedAttemptForTranscript?.phone_masked ||
        undefined,
      initiatedBy:
        this.activeTranscript?.initiated_by_email || 'Voice Campaign',
      language: this.activeTranscript?.language || undefined,
    };
  }

  openAttemptTranscript(attempt: CampaignExecutionAttempt): void {
    if (!attempt.external_message_id) {
      this.messageService.add({
        severity: 'warn',
        summary: 'No Call Session',
        detail:
          'No call recording or external session ID was recorded for this recipient.',
      });
      return;
    }

    this.selectedAttemptForTranscript = attempt;
    this.transcriptLoading = true;
    this.transcriptVisible = true;
    this.activeTranscript = null;
    this.dialogConversations = [];

    this.voiceService.getCallTranscript(attempt.external_message_id).subscribe({
      next: (transcript) => {
        this.activeTranscript = transcript;
        this.transcriptLoading = false;
        this.dialogConversations = this.buildConversationsFromTranscript(
          transcript,
          attempt,
        );
      },
      error: (err: any) => {
        this.transcriptLoading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Transcript Error',
          detail:
            err?.error?.detail || 'Could not load conversation transcript.',
        });
      },
    });
  }

  buildConversationsFromTranscript(
    transcript: CallTranscript,
    attempt: CampaignExecutionAttempt | null,
  ): any[] {
    const list: any[] = [];
    const sid = transcript.call_sid || attempt?.external_message_id || null;
    const recipientName =
      attempt?.name || transcript.phone_masked || 'Customer';

    // System summary event for the call
    const statusText = (
      transcript.status ||
      attempt?.call_status ||
      'completed'
    ).toUpperCase();
    const durationText = this.formatDuration(
      transcript.duration_sec ?? attempt?.call_duration_sec ?? 0,
    );
    list.push({
      id: 'call-start',
      sender: 'system',
      summary: `Call ${statusText} · ${durationText}`,
      startTime: transcript.created_at || attempt?.sent_at,
      created_at: transcript.created_at || attempt?.sent_at,
      callSid: sid,
      call_sid: sid,
    });

    if (transcript.messages && transcript.messages.length > 0) {
      transcript.messages.forEach((msg, idx) => {
        list.push({
          id: msg.id || idx + 1,
          sender: msg.sender,
          summary: msg.content,
          content: msg.content,
          startTime: msg.created_at || transcript.created_at,
          created_at: msg.created_at || transcript.created_at,
          confidence: msg.confidence,
          model_used: msg.model_used,
          callSid: sid,
          call_sid: sid,
          leadName: recipientName,
        });
      });
    }

    return list;
  }

  formatDuration(seconds: number | null | undefined): string {
    if (!seconds || seconds <= 0) return '0s';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    if (mins === 0) return `${secs}s`;
    return `${mins}m ${secs.toString().padStart(2, '0')}s`;
  }

  getCallStatusSeverity(
    status: string | null | undefined,
  ): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
    switch ((status || '').toLowerCase()) {
      case 'completed':
        return 'success';
      case 'in-progress':
      case 'ringing':
        return 'info';
      case 'busy':
      case 'no-answer':
      case 'canceled':
        return 'warn';
      case 'failed':
        return 'danger';
      default:
        return 'secondary';
    }
  }

  getLeadStatusSeverity(
    status: string | null | undefined,
  ): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
    switch ((status || '').toLowerCase()) {
      case 'hot':
        return 'danger';
      case 'warm':
        return 'warn';
      case 'cold':
        return 'info';
      default:
        return 'secondary';
    }
  }

  getExecutionBadgeSeverity(
    status: string,
  ): 'success' | 'warn' | 'danger' | 'info' | 'secondary' {
    switch ((status || '').toLowerCase()) {
      case 'completed':
        return 'success';
      case 'running':
        return 'info';
      case 'stopped':
      case 'cancelled':
      case 'failed':
        return 'danger';
      default:
        return 'secondary';
    }
  }

  getRestartModeBadge(mode: string): {
    label: string;
    severity: 'info' | 'warn' | 'success' | 'secondary';
  } {
    switch ((mode || '').toLowerCase()) {
      case 'failed_only':
        return { label: 'Failed Only', severity: 'warn' };
      case 'pending_only':
        return { label: 'Pending Only', severity: 'info' };
      case 'all':
      default:
        return { label: 'All Recipients', severity: 'success' };
    }
  }

  startCampaign(mode: RestartMode = 'all'): void {
    if (!this.campaign || this.startingCampaign) return;
    const modeLabel =
      mode === 'failed_only'
        ? 'retry failed recipients only'
        : mode === 'pending_only'
          ? 'resume pending recipients only'
          : 'all recipients';

    this.confirmationService.confirm({
      message: `Start sending to ${modeLabel}? This action spends money and reaches real people.`,
      header: 'Start Campaign',
      icon: 'pi pi-send',
      accept: () => {
        if (!this.campaign || this.startingCampaign) return;
        this.startingCampaign = true;
        this.campaignService.startCampaign(this.campaign.id, mode).subscribe({
          next: () => {
            this.startingCampaign = false;
            this.messageService.add({
              severity: 'success',
              summary: 'Campaign Started',
              detail: `Campaign started with mode: ${mode}.`,
            });
            this.loadCampaign(this.campaign!.id);
            this.loadExecutions(this.campaign!.id);
          },
          error: (err) => {
            this.startingCampaign = false;
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

  restartEverything(): void {
    if (!this.campaign) return;
    this.confirmationService.confirm({
      message:
        'Restart campaign execution from scratch? This will rebuild the recipient audience and start outreach from the beginning.',
      header: 'Restart Everything',
      icon: 'pi pi-refresh',
      accept: () => {
        this.isRestarting = true;
        this.campaignService.buildCampaign(this.campaign!.id).subscribe({
          next: () => {
            this.campaignService.startCampaign(this.campaign!.id).subscribe({
              next: () => {
                this.isRestarting = false;
                this.messageService.add({
                  severity: 'success',
                  summary: 'Campaign Restarted',
                  detail: 'Fresh build complete and campaign started.',
                });
                this.loadCampaign(this.campaign!.id);
              },
              error: (err) => {
                this.isRestarting = false;
                this.messageService.add({
                  severity: 'error',
                  summary: 'Start Failed',
                  detail:
                    err?.message || 'Could not start campaign after build.',
                });
              },
            });
          },
          error: (err) => {
            this.isRestarting = false;
            this.messageService.add({
              severity: 'error',
              summary: 'Rebuild Failed',
              detail: err?.message || 'Could not rebuild audience.',
            });
          },
        });
      },
    });
  }

  executeRetryAction(): void {
    if (this.retryMode === 'restart_all') {
      this.restartEverything();
    } else {
      this.retryFailed();
    }
  }

  isLeadCampaign(): boolean {
    if (!this.campaign) return false;
    const type = (this.campaign.campaign_type || '').toLowerCase().trim();
    return (
      this.campaign.created_via === 'import' ||
      !!this.campaign.product_name ||
      !!this.campaign.product_id ||
      type.includes('lead')
    );
  }

  get isLeadImportBatch(): boolean {
    return false;
  }

  get filteredHistoryRecipients(): CampaignRecipient[] {
    if (!this.historyFilterText) return this.allRecipients;
    const q = this.historyFilterText.toLowerCase();
    return this.allRecipients.filter(
      (r) =>
        (r.name && r.name.toLowerCase().includes(q)) ||
        (r.identifier_masked &&
          r.identifier_masked.toLowerCase().includes(q)) ||
        (r.status && r.status.toLowerCase().includes(q)),
    );
  }

  openRecipientMenu(
    event: Event,
    recipient: CampaignRecipient,
    menu: any,
  ): void {
    this.selectedRecipientForTranscript = recipient;
    this.activeRecipientMenuItems = [
      {
        label: 'Transcription',
        icon: 'pi pi-file-edit',
        command: () => this.openTranscript(recipient),
      },
      {
        label: 'Download CSV',
        icon: 'pi pi-download',
        command: () => this.downloadSingleCsv(recipient),
      },
    ];
    menu.toggle(event);
  }

  openTranscript(recipient: CampaignRecipient): void {
    this.selectedRecipientForTranscript = recipient;
    this.selectedAttemptForTranscript = {
      id: recipient.id,
      recipient_id: recipient.id,
      name: recipient.name || 'Recipient',
      phone_masked: recipient.identifier_masked || recipient.phone,
      status: recipient.status,
      external_message_id: recipient.conversation_id || null,
    };
    if (recipient.conversation_id) {
      this.openAttemptTranscript(this.selectedAttemptForTranscript);
    } else {
      const mockTurns = this.getTranscriptMessages(recipient);
      this.dialogConversations = mockTurns.map((turn, idx) => ({
        id: idx + 1,
        sender: turn.speaker === 'user' ? 'customer' : 'ai',
        summary: turn.text,
        content: turn.text,
        startTime: new Date().toISOString(),
        leadName: recipient.name || 'Customer',
      }));
      this.transcriptVisible = true;
      this.transcriptLoading = false;
    }
  }

  closeTranscript(): void {
    this.showTranscriptDialog = false;
    this.transcriptVisible = false;
    this.activeTranscript = null;
    this.selectedRecipientForTranscript = null;
    this.selectedAttemptForTranscript = null;
    this.dialogConversations = [];
  }

  downloadSingleCsv(recipient: CampaignRecipient): void {
    const headers = 'Sr. No.,Payer Phone,Status,Patient\n';
    const phone = this.getFormattedPhone(recipient);
    const row = `1,${phone},${recipient.status || 'Completed'},"${recipient.name || 'Contact'}"\n`;
    const blob = new Blob([headers + row], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `call_${recipient.id || 'contact'}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  toggleHistorySort(): void {
    this.historySortAsc = !this.historySortAsc;
    this.historyItems.sort((a, b) => {
      const da = new Date(a.created_at).getTime();
      const db = new Date(b.created_at).getTime();
      return this.historySortAsc ? da - db : db - da;
    });
  }

  resetHistoryFilters(): void {
    this.historySortAsc = false;
    this.historyFilterText = '';
    if (this.campaign) {
      this.loadHistory(this.campaign.id);
      this.loadRecipients(this.campaign.id);
    }
  }

  getFormattedPhone(recipient: CampaignRecipient): string {
    const raw = recipient.identifier_masked || recipient.phone || '';
    const digits = raw.replace(/[^0-9]/g, '');
    return digits || raw || '18886246300';
  }

  getExecutionStatusLabel(): string {
    const s = (this.campaign?.status || '').toLowerCase();
    if (s === 'completed') return 'Completed';
    if (s === 'running') return 'Running';
    if (s === 'paused') return 'Paused';
    return 'Stopped';
  }

  getExecutionStatusSeverity(): string {
    const s = (this.campaign?.status || '').toLowerCase();
    if (s === 'completed')
      return 'bg-emerald-50 text-emerald-600 border border-emerald-200';
    if (s === 'running')
      return 'bg-blue-50 text-blue-600 border border-blue-200';
    return 'bg-rose-50 text-rose-600 border border-rose-200';
  }

  getTranscriptMessages(
    recipient: CampaignRecipient | null,
  ): { speaker: 'agent' | 'user'; text: string; time: string }[] {
    const name = recipient?.name || 'Lead';
    const product = this.campaign?.product_name || 'Commercial Plots';
    return [
      {
        speaker: 'agent',
        text: `Hello, may I speak with ${name}? I'm calling from LeadAI regarding your inquiry about ${product}.`,
        time: '0:02',
      },
      {
        speaker: 'user',
        text: `Yes, speaking. Thanks for reaching out. What are the available details and pricing?`,
        time: '0:08',
      },
      {
        speaker: 'agent',
        text: `Great! We have prime commercial units available with tailored payment structures. Would you like to schedule an in-person viewing or have the property specifications sent to your WhatsApp?`,
        time: '0:18',
      },
      {
        speaker: 'user',
        text: `Please send the specifications to WhatsApp, and we can discuss scheduling next week.`,
        time: '0:26',
      },
      {
        speaker: 'agent',
        text: `Perfect! I've logged your preference and will dispatch the catalog immediately. Have a wonderful day!`,
        time: '0:33',
      },
    ];
  }

  getCampaignTypeBadge(): {
    label: string;
    severity: 'info' | 'secondary';
    icon: string;
  } {
    if (this.isLeadCampaign()) {
      return { label: 'Lead Campaign', severity: 'info', icon: 'pi pi-users' };
    }
    return {
      label: 'Broadcast',
      severity: 'secondary',
      icon: 'pi pi-megaphone',
    };
  }

  getCallConsentStatus(recipient: CampaignRecipient): string {
    const raw = recipient.call_consent_status || recipient.CallConsentStatus;
    if (!raw) return 'not_yet_asked';
    return String(raw).toLowerCase().trim();
  }

  getConsentSeverity(
    status: string,
  ): 'success' | 'danger' | 'info' | 'warn' | 'secondary' {
    switch (status) {
      case 'accepted':
        return 'success';
      case 'declined':
        return 'danger';
      case 'asked':
        return 'info';
      default:
        return 'secondary';
    }
  }

  getConsentLabel(status: string): string {
    switch (status) {
      case 'accepted':
        return 'Accepted';
      case 'declined':
        return 'Declined';
      case 'asked':
        return 'Asked (Pending)';
      default:
        return 'Not Yet Asked';
    }
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

  getStatusSeverity(
    status: string,
  ): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
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

  getActionSeverity(
    action: string,
  ): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
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
