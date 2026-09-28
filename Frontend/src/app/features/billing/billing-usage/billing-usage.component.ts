import { Component, OnInit } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { BillingService } from '../../../services/billing.service';
import { BillingSummary, CallDetailsResponse, CallTranscriptTurn, UsageLog } from '../../../models/billing.models';
import { MessageService } from 'primeng/api';

@Component({
  selector: 'app-billing-usage',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './billing-usage.component.html',
  styleUrl: './billing-usage.component.scss',
})
export class BillingUsageComponent implements OnInit {
  usageLogs: UsageLog[] = [];
  filteredLogs: UsageLog[] = [];
  summary: BillingSummary | null = null;
  loading = true;

  searchTerm = '';

  // Call Details Dialog state
  showCallDetailsDialog = false;
  callDetailsLoading = false;
  selectedLog: UsageLog | null = null;
  activeCallDetails: CallDetailsResponse | null = null;

  constructor(
    private billingService: BillingService,
    private messageService: MessageService
  ) {}

  ngOnInit(): void {
    this.loadUsageData();
  }

  loadUsageData(): void {
    this.loading = true;

    this.billingService.getCurrentPlan().subscribe({
      next: (sum) => {
        this.summary = sum;
      },
      error: () => {},
    });

    this.billingService.getUsageHistory(100).subscribe({
      next: (logs) => {
        // Filter strictly to voice call deductions only (exclude topup balance additions)
        this.usageLogs = (logs || []).filter(
          (log) => Number(log.minutes_deducted) > 0 && !log.call_sid?.startsWith('TOPUP')
        );
        this.applyFilter();
        this.loading = false;
      },
      error: (err) => {
        this.loading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Error Loading Usage',
          detail: err?.error?.detail || 'Failed to fetch minute usage records.',
        });
      },
    });
  }

  applyFilter(): void {
    if (!this.searchTerm || !this.searchTerm.trim()) {
      this.filteredLogs = [...this.usageLogs];
      return;
    }

    const term = this.searchTerm.toLowerCase().trim();
    this.filteredLogs = this.usageLogs.filter(
      (log) =>
        log.call_sid?.toLowerCase().includes(term) ||
        log.conversation_id?.toLowerCase().includes(term) ||
        log.recharge_id?.toLowerCase().includes(term)
    );
  }

  onSearchChange(): void {
    this.applyFilter();
  }

  // Call Details Modal Handling
  openCallDetails(log: UsageLog): void {
    this.selectedLog = log;
    this.showCallDetailsDialog = true;
    this.callDetailsLoading = true;
    this.activeCallDetails = null;

    this.billingService.getCallDetails(log.call_sid).subscribe({
      next: (details) => {
        this.activeCallDetails = details;
        this.callDetailsLoading = false;
      },
      error: (err) => {
        this.callDetailsLoading = false;
        this.messageService.add({
          severity: 'warn',
          summary: 'Could Not Load Full Details',
          detail: err?.error?.detail || 'Loaded basic session info only.',
        });
        // Create a fallback object with known usage log info
        this.activeCallDetails = {
          call_sid: log.call_sid,
          conversation_id: log.conversation_id,
          duration_seconds: log.call_duration_seconds,
          minutes_deducted: log.minutes_deducted,
          recording_url: null,
          created_at: log.deducted_at,
          messages: [],
        };
      },
    });
  }

  closeCallDetails(): void {
    this.showCallDetailsDialog = false;
    this.selectedLog = null;
    this.activeCallDetails = null;
    this.callDetailsLoading = false;
  }

  copyFullTranscript(): void {
    if (!this.activeCallDetails?.messages || this.activeCallDetails.messages.length === 0) {
      this.messageService.add({
        severity: 'info',
        summary: 'No Transcript',
        detail: 'There are no transcript messages to copy.',
      });
      return;
    }

    const formattedTranscript = this.activeCallDetails.messages
      .map((msg) => {
        const role = msg.sender === 'ai' ? 'AI Agent' : msg.sender === 'customer' ? 'Customer' : msg.sender.toUpperCase();
        return `[${role}]: ${msg.text}`;
      })
      .join('\n\n');

    navigator.clipboard.writeText(formattedTranscript);
    this.messageService.add({
      severity: 'success',
      summary: 'Transcript Copied',
      detail: 'Full conversation transcript copied to clipboard.',
      life: 2500,
    });
  }

  // Consumption KPIs
  get totalMinutesConsumed(): number {
    return this.usageLogs.reduce((acc, curr) => acc + (Number(curr.minutes_deducted) || 0), 0);
  }

  get totalCallsCount(): number {
    return this.usageLogs.length;
  }

  get averageDurationSeconds(): number {
    if (!this.usageLogs.length) return 0;
    const totalSecs = this.usageLogs.reduce((acc, curr) => acc + (Number(curr.call_duration_seconds) || 0), 0);
    return Math.round(totalSecs / this.usageLogs.length);
  }

  formatDuration(seconds: number): string {
    if (!seconds && seconds !== 0) return '0s';
    if (seconds < 60) return `${seconds}s`;
    const mins = Math.floor(seconds / 60);
    const remainingSecs = seconds % 60;
    return remainingSecs > 0 ? `${mins}m ${remainingSecs}s` : `${mins}m`;
  }

  copyToClipboard(text?: string | null, label: string = 'Call SID'): void {
    if (!text) return;
    navigator.clipboard.writeText(text);
    this.messageService.add({
      severity: 'success',
      summary: 'Copied',
      detail: `${label} copied to clipboard`,
      life: 2000,
    });
  }
}
