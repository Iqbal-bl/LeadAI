import { Component, OnInit } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { BillingService } from '../../../services/billing.service';
import {
  BillingSummary,
  ClientRecharge,
  RechargePlanTemplate,
  UsageLog,
} from '../../../models/billing.models';
import { MessageService } from 'primeng/api';

@Component({
  selector: 'app-billing-dashboard',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './billing-dashboard.component.html',
  styleUrl: './billing-dashboard.component.scss',
})
export class BillingDashboardComponent implements OnInit {
  summary: BillingSummary | null = null;
  availablePlans: RechargePlanTemplate[] = [];
  usageLogs: UsageLog[] = [];
  loading = true;
  rechargeLoading = false;
  selectedPlan: RechargePlanTemplate | null = null;
  showRechargeModal = false;

  constructor(
    private billingService: BillingService,
    private messageService: MessageService,
  ) {}

  ngOnInit(): void {
    this.loadData();
  }

  loadData(): void {
    this.loading = true;
    this.billingService.getCurrentPlan().subscribe({
      next: (res) => {
        this.summary = res;
        this.loading = false;
      },
      error: (err) => {
        this.loading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Billing Error',
          detail: err?.error?.detail || 'Failed to load billing information.',
        });
      },
    });

    this.billingService.getAvailablePlans().subscribe({
      next: (plans) => {
        this.availablePlans = plans;
      },
    });

    this.billingService.getUsageHistory(30).subscribe({
      next: (logs) => {
        this.usageLogs = logs;
      },
    });
  }

  openRechargeModal(plan?: RechargePlanTemplate): void {
    this.selectedPlan = plan || null;
    this.showRechargeModal = true;
  }

  confirmRecharge(plan: RechargePlanTemplate): void {
    this.rechargeLoading = true;
    this.billingService
      .recharge({
        plan_template_id: plan.id,
        payment_reference: 'Card / Prepaid Self-Service',
      })
      .subscribe({
        next: (recharge: ClientRecharge) => {
          this.rechargeLoading = false;
          this.showRechargeModal = false;
          this.messageService.add({
            severity: 'success',
            summary: 'Recharge Applied',
            detail: `Plan "${recharge.plan_name_snapshot}" (${recharge.purchased_minutes} mins) added successfully!`,
          });
          this.loadData();
        },
        error: (err) => {
          this.rechargeLoading = false;
          const detailMsg = typeof err?.error?.detail === 'string'
            ? err.error.detail
            : Array.isArray(err?.error?.detail)
            ? err.error.detail.map((e: any) => e.msg || e.detail).join(', ')
            : (err?.message || 'Recharge processing failed.');
          this.messageService.add({
            severity: 'error',
            summary: 'Recharge Failed',
            detail: detailMsg,
          });
        },
      });
  }

  calculateUsagePercent(recharge: ClientRecharge): number {
    if (!recharge || !recharge.purchased_minutes) return 0;
    const used = recharge.purchased_minutes - recharge.remaining_minutes;
    const pct = Math.round((used / recharge.purchased_minutes) * 100);
    return Math.min(100, Math.max(0, pct));
  }

  getDaysRemaining(expiresAt?: string | null): number {
    if (!expiresAt) return 0;
    const exp = new Date(expiresAt).getTime();
    const now = new Date().getTime();
    const diff = exp - now;
    return Math.max(0, Math.ceil(diff / (1000 * 60 * 60 * 24)));
  }
}
