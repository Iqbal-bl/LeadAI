import { Component, OnInit } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { BillingService } from '../../../services/billing.service';
import { ClientRecharge } from '../../../models/billing.models';
import { MessageService } from 'primeng/api';
import { environment } from '../../../../environments/environment';

@Component({
  selector: 'app-billing-invoices',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './billing-invoices.component.html',
  styleUrl: './billing-invoices.component.scss',
})
export class BillingInvoicesComponent implements OnInit {
  paymentHistory: ClientRecharge[] = [];
  filteredHistory: ClientRecharge[] = [];
  loading = true;

  // Filter states
  searchTerm = '';
  selectedStatus = 'ALL';
  statusOptions = [
    { label: 'All Statuses', value: 'ALL' },
    { label: 'Active Subscriptions', value: 'active' },
    { label: 'Top-Up Added', value: 'superseded' },
    { label: 'Queued / Pending', value: 'pending' },
    { label: 'Failed / Dismissed', value: 'failed' },
    { label: 'Exhausted', value: 'exhausted' },
    { label: 'Expired', value: 'expired' },
  ];

  constructor(
    private billingService: BillingService,
    private messageService: MessageService
  ) {}

  ngOnInit(): void {
    this.loadInvoices();
  }

  loadInvoices(): void {
    this.loading = true;
    this.billingService.getPaymentHistory(150).subscribe({
      next: (history) => {
        this.paymentHistory = history || [];
        this.applyFilters();
        this.loading = false;
      },
      error: (err) => {
        this.loading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Error Loading Invoices',
          detail: err?.error?.detail || 'Failed to fetch transaction history.',
        });
      },
    });
  }

  applyFilters(): void {
    let result = [...this.paymentHistory];

    if (this.selectedStatus && this.selectedStatus !== 'ALL') {
      result = result.filter((item) => item.status === this.selectedStatus);
    }

    if (this.searchTerm && this.searchTerm.trim()) {
      const term = this.searchTerm.toLowerCase().trim();
      result = result.filter(
        (item) =>
          item.plan_name_snapshot?.toLowerCase().includes(term) ||
          item.payment_reference?.toLowerCase().includes(term) ||
          item.razorpay_order_id?.toLowerCase().includes(term) ||
          item.id?.toLowerCase().includes(term)
      );
    }

    this.filteredHistory = result;
  }

  onSearchChange(): void {
    this.applyFilters();
  }

  onStatusChange(): void {
    this.applyFilters();
  }

  // Financial Metrics Calculations
  get totalAmountSpent(): number {
    return this.paymentHistory
      .filter((item) => ['active', 'superseded', 'exhausted', 'expired', 'pending'].includes(item.status))
      .reduce((acc, curr) => acc + (Number(curr.price_paid) || 0), 0);
  }

  get totalMinutesPurchased(): number {
    return this.paymentHistory
      .filter((item) => ['active', 'superseded', 'exhausted', 'expired', 'pending'].includes(item.status))
      .reduce((acc, curr) => acc + (Number(curr.purchased_minutes) || 0), 0);
  }

  get successfulRechargesCount(): number {
    return this.paymentHistory.filter(
      (item) => ['active', 'superseded', 'exhausted', 'expired', 'pending'].includes(item.status)
    ).length;
  }

  get failedAttemptsCount(): number {
    return this.paymentHistory.filter((item) => item.status === 'failed' || item.status === 'cancelled').length;
  }

  openInvoice(url?: string | null): void {
    if (url) {
      if (url.startsWith('http://') || url.startsWith('https://')) {
        window.open(url, '_blank');
      } else {
        const cleanPath = url.replace(/^\/api\/leadai/, '');
        const targetUrl = `${environment.apiPrefix}${cleanPath}`;
        window.open(targetUrl, '_blank');
      }
    } else {
      this.messageService.add({
        severity: 'info',
        summary: 'Invoice Unavailable',
        detail: 'Tax invoice PDF is not generated or available for this entry.',
      });
    }
  }

  copyToClipboard(text?: string | null): void {
    if (!text) return;
    navigator.clipboard.writeText(text);
    this.messageService.add({
      severity: 'success',
      summary: 'Copied',
      detail: 'ID copied to clipboard',
      life: 2000,
    });
  }
}
