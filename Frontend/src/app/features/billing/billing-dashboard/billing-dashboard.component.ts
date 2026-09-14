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
import { environment } from '../../../../environments/environment';

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
  paymentHistory: ClientRecharge[] = [];
  loading = true;
  rechargeLoading = false;
  paymentHistoryLoading = false;
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

    this.loadPaymentHistory();
  }

  loadPaymentHistory(): void {
    this.paymentHistoryLoading = true;
    this.billingService.getPaymentHistory(100).subscribe({
      next: (history) => {
        this.paymentHistory = history;
        this.paymentHistoryLoading = false;
      },
      error: () => {
        this.paymentHistoryLoading = false;
      },
    });
  }

  openRechargeModal(plan?: RechargePlanTemplate): void {
    this.selectedPlan = plan || null;
    this.showRechargeModal = true;
  }

  confirmRecharge(plan: RechargePlanTemplate): void {
    this.rechargeLoading = true;
    this.showRechargeModal = false;

    this.billingService.createRazorpayOrder(plan.id).subscribe({
      next: (orderRes) => {
        this.rechargeLoading = false;
        this.launchRazorpayCheckout(plan, orderRes);
      },
      error: (err) => {
        this.rechargeLoading = false;
        const detailMsg = typeof err?.error?.detail === 'string'
          ? err.error.detail
          : Array.isArray(err?.error?.detail)
          ? err.error.detail.map((e: any) => e.msg || e.detail).join(', ')
          : (err?.message || 'Failed to initiate payment order.');
        this.messageService.add({
          severity: 'error',
          summary: 'Order Initiation Failed',
          detail: detailMsg,
        });
      },
    });
  }

  private launchRazorpayCheckout(plan: RechargePlanTemplate, orderRes: any): void {
    if (typeof (window as any).Razorpay === 'undefined') {
      this.messageService.add({
        severity: 'error',
        summary: 'Payment Gateway Error',
        detail: 'Razorpay SDK is not loaded. Please refresh the page and try again.',
      });
      return;
    }

    const options: any = {
      key: orderRes.key_id,
      amount: orderRes.amount,
      currency: orderRes.currency || 'INR',
      name: 'LeadAI',
      description: `Recharge Plan: ${orderRes.plan_name} (${orderRes.included_minutes} mins)`,
      order_id: orderRes.order_id,
      handler: (response: any) => {
        this.rechargeLoading = true;
        this.billingService
          .verifyRazorpayPayment({
            razorpay_order_id: response.razorpay_order_id,
            razorpay_payment_id: response.razorpay_payment_id,
            razorpay_signature: response.razorpay_signature,
            plan_template_id: plan.id,
          })
          .subscribe({
            next: (recharge: ClientRecharge) => {
              this.rechargeLoading = false;
              this.messageService.add({
                severity: 'success',
                summary: 'Payment & Recharge Successful!',
                detail: `Plan "${recharge.plan_name_snapshot}" (${recharge.purchased_minutes} mins) activated successfully.`,
              });
              this.loadData();
            },
            error: (err) => {
              this.rechargeLoading = false;
              this.messageService.add({
                severity: 'error',
                summary: 'Verification Failed',
                detail: err?.error?.detail || 'Signature verification failed. Please contact support.',
              });
              this.loadData();
            },
          });
      },
      modal: {
        ondismiss: () => {
          this.rechargeLoading = false;
          this.billingService
            .recordPaymentFailure({
              razorpay_order_id: orderRes.order_id,
              error_code: 'DISMISSED',
              error_description: 'Payment popup was closed without completing the transaction',
            })
            .subscribe(() => {
              this.loadPaymentHistory();
            });
        },
      },
      theme: {
        color: '#4f46e5',
      },
    };

    const rzp = new (window as any).Razorpay(options);
    rzp.on('payment.failed', (failRes: any) => {
      this.rechargeLoading = false;
      this.billingService
        .recordPaymentFailure({
          razorpay_order_id: orderRes.order_id,
          error_code: failRes?.error?.code || 'PAYMENT_FAILED',
          error_description: failRes?.error?.description || 'Payment failed at gateway or bank',
        })
        .subscribe(() => {
          this.loadPaymentHistory();
        });
      this.messageService.add({
        severity: 'error',
        summary: 'Payment Failed',
        detail: failRes?.error?.description || 'Your payment attempt could not be processed.',
      });
    });
    rzp.open();
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
        detail: 'Invoice link is not available for this transaction.',
      });
    }
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
