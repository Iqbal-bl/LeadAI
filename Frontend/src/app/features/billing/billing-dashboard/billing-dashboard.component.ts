import { Component, OnInit } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { BillingService } from '../../../services/billing.service';
import {
  BillingSummary,
  ChannelAddonOrderResponse,
  ChannelAddonQuote,
  ClientRecharge,
  RechargePlanTemplate,
  UsageLog,
} from '../../../models/billing.models';
import { MessageService } from 'primeng/api';
import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../services/auth.service';

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
    private authService: AuthService,
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
    if (plan) {
      this.confirmRecharge(plan);
    } else {
      const el = document.getElementById('plans-section');
      if (el) {
        el.scrollIntoView({ behavior: 'smooth' });
      }
    }
  }

  confirmRecharge(plan: RechargePlanTemplate): void {
    this.rechargeLoading = true;
    const isRecurring =
      plan.plan_type === 'standard' &&
      (!plan.plan_category || plan.plan_category === 'voice_standard');

    if (isRecurring) {
      this.billingService.createRazorpaySubscription(plan.id).subscribe({
        next: (subRes) => {
          this.rechargeLoading = false;
          this.launchRazorpaySubscriptionCheckout(plan, subRes);
        },
        error: (err) => {
          this.rechargeLoading = false;
          const detailMsg =
            typeof err?.error?.detail === 'string'
              ? err.error.detail
              : Array.isArray(err?.error?.detail)
              ? err.error.detail.map((e: any) => e.msg || e.detail).join(', ')
              : err?.message || 'Failed to initiate recurring subscription mandate.';
          this.messageService.add({
            severity: 'error',
            summary: 'AutoPay Mandate Failed',
            detail: detailMsg,
            life: 8000,
          });
        },
      });
    } else {
      this.billingService.createRazorpayOrder(plan.id).subscribe({
        next: (orderRes) => {
          this.rechargeLoading = false;
          this.launchRazorpayCheckout(plan, orderRes);
        },
        error: (err) => {
          this.rechargeLoading = false;
          const detailMsg =
            typeof err?.error?.detail === 'string'
              ? err.error.detail
              : Array.isArray(err?.error?.detail)
              ? err.error.detail.map((e: any) => e.msg || e.detail).join(', ')
              : err?.message || 'Failed to initiate payment order.';
          this.messageService.add({
            severity: 'error',
            summary: 'Order Initiation Failed',
            detail: detailMsg,
          });
        },
      });
    }
  }

  private launchRazorpaySubscriptionCheckout(plan: RechargePlanTemplate, subRes: any): void {
    if (typeof (window as any).Razorpay === 'undefined') {
      this.messageService.add({
        severity: 'error',
        summary: 'Payment Gateway Error',
        detail: 'Razorpay SDK is not loaded. Please refresh the page and try again.',
      });
      return;
    }

    const options: any = {
      key: subRes.key_id,
      subscription_id: subRes.subscription_id,
      name: 'LeadAI',
      description: `AutoPay Mandate: ${subRes.plan_name} (${subRes.included_minutes} mins/mo)`,
      handler: (response: any) => {
        this.rechargeLoading = true;
        this.billingService
          .verifyRazorpaySubscription({
            razorpay_subscription_id: response.razorpay_subscription_id,
            razorpay_payment_id: response.razorpay_payment_id,
            razorpay_signature: response.razorpay_signature,
            plan_template_id: plan.id,
          })
          .subscribe({
            next: (recharge: ClientRecharge) => {
              this.rechargeLoading = false;
              this.messageService.add({
                severity: 'success',
                summary: 'AutoPay Mandate Activated!',
                detail: `Plan "${recharge.plan_name_snapshot}" (${recharge.purchased_minutes} mins) activated with recurring auto-renew.`,
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
          this.customBundleLoading = false;
          this.billingService
            .recordPaymentFailure({
              subscription_id: subRes.subscription_id,
              razorpay_subscription_id: subRes.subscription_id,
              error_code: 'DISMISSED',
              error_description: 'Subscription mandate checkout was closed without completing setup',
            })
            .subscribe(() => {
              this.loadPaymentHistory();
              this.loadData();
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
      this.customBundleLoading = false;
      this.billingService
        .recordPaymentFailure({
          subscription_id: subRes.subscription_id,
          razorpay_subscription_id: subRes.subscription_id,
          error_code: failRes?.error?.code || 'PAYMENT_FAILED',
          error_description: failRes?.error?.description || 'Mandate registration failed at gateway or bank',
        })
        .subscribe(() => {
          this.loadPaymentHistory();
          this.loadData();
        });
      this.messageService.add({
        severity: 'error',
        summary: 'Mandate Setup Failed',
        detail: failRes?.error?.description || 'Your subscription mandate could not be processed.',
      });
    });
    rzp.open();
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
      const token = this.authService.getValue('accessToken') || this.authService.getStaffToken();
      if (url.startsWith('http://') || url.startsWith('https://')) {
        const separator = url.includes('?') ? '&' : '?';
        const finalUrl = token ? `${url}${separator}token=${encodeURIComponent(token)}` : url;
        window.open(finalUrl, '_blank');
      } else {
        const cleanPath = url.replace(/^\/api\/leadai/, '');
        const separator = cleanPath.includes('?') ? '&' : '?';
        const targetUrl = token
          ? `${environment.apiPrefix}${cleanPath}${separator}token=${encodeURIComponent(token)}`
          : `${environment.apiPrefix}${cleanPath}`;
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

  hasActivePlan(): boolean {
    if (!this.summary?.active_recharge) return false;
    const ar = this.summary.active_recharge;
    if (ar.status !== 'active') return false;
    if (ar.expires_at) {
      const exp = new Date(ar.expires_at).getTime();
      if (exp <= Date.now()) return false;
    }
    return true;
  }

  isBasePlanDisabled(plan: RechargePlanTemplate): boolean {
    const isTopup = plan.plan_type === 'topup' || plan.plan_category === 'voice_topup';
    return this.hasActivePlan() && !isTopup;
  }

  showCancelModal = false;
  cancelLoading = false;

  openCancelModal(): void {
    this.showCancelModal = true;
  }

  confirmCancelSubscription(): void {
    this.cancelLoading = true;
    this.billingService.cancelSubscription().subscribe({
      next: (res) => {
        this.cancelLoading = false;
        this.showCancelModal = false;
        this.messageService.add({
          severity: 'success',
          summary: 'AutoPay Cancelled',
          detail: res.message,
          life: 8000,
        });
        this.loadData();
      },
      error: (err) => {
        this.cancelLoading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Cancellation Failed',
          detail: err?.error?.detail || 'Could not cancel subscription. Please try again.',
        });
      },
    });
  }

  // Custom Bundle Builder
  customIncludeVoice = true;
  customVoiceMinutes = 500;
  customChannels: { [key: string]: boolean } = {
    whatsapp: false,
    instagram: false,
    facebook: false,
    linkedin: false,
  };
  customBillingCycle: 'monthly' | 'yearly' = 'monthly';
  customBundleLoading = false;

  readonly CHANNEL_PRICING: { [key: string]: { name: string; price: number; icon: string; desc: string } } = {
    whatsapp: { name: 'WhatsApp Bot', price: 1499, icon: 'pi pi-whatsapp', desc: 'AI WhatsApp messaging & inbound lead routing' },
    instagram: { name: 'Instagram DM Automation', price: 799, icon: 'pi pi-instagram', desc: 'Auto-reply to DMs & story mentions' },
    facebook: { name: 'Facebook Messenger', price: 799, icon: 'pi pi-facebook', desc: 'Page inbox conversational agent' },
    linkedin: { name: 'LinkedIn Lead Gen', price: 1999, icon: 'pi pi-linkedin', desc: 'InMail outreach & prospect follow-up' },
  };

  getChannelInfo(ch: string): { name: string; price: number; icon: string; desc: string } {
    const key = (ch || '').toLowerCase().trim();
    return (
      this.CHANNEL_PRICING[key] || {
        name: ch || 'Channel',
        price: 0,
        icon: 'pi pi-globe',
        desc: 'Omni-channel messaging integration',
      }
    );
  }

  cleanPlanName(name?: string | null): string {
    if (!name) return 'No Active Plan';
    return name
      .replace(/\s*\+\s*(whatsapp|instagram|facebook|linkedin)[^-\)]*/gi, '')
      .replace(/\s{2,}/g, ' ')
      .trim();
  }

  get selectedChannelsList(): string[] {
    return Object.keys(this.customChannels).filter((k) => this.customChannels[k]);
  }

  get customMonthlyTotal(): number {
    const voicePrice = this.customIncludeVoice ? (this.customVoiceMinutes * 4.0) : 0;
    const addonTotal = this.selectedChannelsList.reduce(
      (sum, ch) => sum + (this.CHANNEL_PRICING[ch]?.price || 0),
      0
    );
    return voicePrice + addonTotal;
  }

  get customBundleTotal(): number {
    if (this.customBillingCycle === 'yearly') {
      return Math.round(this.customMonthlyTotal * 12 * 0.85);
    }
    return this.customMonthlyTotal;
  }

  subscribeCustomBundle(): void {
    if (this.hasActivePlan()) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Active Plan Exists',
        detail: 'You already have an active subscription. Additional base plans cannot be added until cycle end.',
      });
      return;
    }
    if (this.customMonthlyTotal <= 0) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Empty Bundle',
        detail: 'Please select voice minutes or at least one social media channel.',
      });
      return;
    }

    this.customBundleLoading = true;
    this.billingService.createCustomBundle({
      include_voice: this.customIncludeVoice,
      voice_minutes: this.customVoiceMinutes,
      channels: this.selectedChannelsList,
      billing_cycle: this.customBillingCycle,
    }).subscribe({
      next: (subRes) => {
        this.customBundleLoading = false;
        const syntheticPlan: RechargePlanTemplate = {
          id: subRes.plan_id,
          name: subRes.plan_name,
          plan_type: 'custom',
          included_minutes: subRes.included_minutes,
          validity_days: this.customBillingCycle === 'yearly' ? 365 : 30,
          price: subRes.amount / 100,
          rate_per_minute: 4.0,
          is_active: true,
        };
        this.launchRazorpaySubscriptionCheckout(syntheticPlan, subRes);
      },
      error: (err) => {
        this.customBundleLoading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Custom Bundle Failed',
          detail: err?.error?.detail || 'Failed to initiate custom bundle subscription.',
        });
      },
    });
  }

  // =========================================================================
  // Mid-Cycle Modular Channel Add-Ons
  // =========================================================================
  showAddonModal = false;
  selectedAddonChannel = '';
  addonQuote: ChannelAddonQuote | null = null;
  addonQuoteLoading = false;
  addonPurchaseLoading = false;

  isChannelActive(channelKey: string): boolean {
    const activeChannels = this.summary?.active_recharge?.active_channels || [];
    return activeChannels.includes(channelKey.toLowerCase().trim());
  }

  isChannelInNextCycle(channelKey: string): boolean {
    const nextChannels = this.summary?.active_recharge?.next_cycle_channels || [];
    return nextChannels.includes(channelKey.toLowerCase().trim());
  }

  openAddonModal(channelKey: string): void {
    if (!this.hasActivePlan()) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Active Plan Required',
        detail: 'Please activate a base plan before adding modular channel add-ons.',
      });
      return;
    }

    if (this.isChannelActive(channelKey)) {
      this.messageService.add({
        severity: 'info',
        summary: 'Already Active',
        detail: `The ${this.CHANNEL_PRICING[channelKey]?.name || channelKey} channel is already active on your account.`,
      });
      return;
    }

    this.selectedAddonChannel = channelKey;
    this.showAddonModal = true;
    this.addonQuoteLoading = true;
    this.addonQuote = null;

    this.billingService.getChannelAddonQuote(channelKey).subscribe({
      next: (quote) => {
        this.addonQuote = quote;
        this.addonQuoteLoading = false;
      },
      error: (err) => {
        this.addonQuoteLoading = false;
        this.showAddonModal = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Quote Failed',
          detail: err?.error?.detail || 'Failed to calculate prorated add-on pricing.',
        });
      },
    });
  }

  confirmAddonPurchase(): void {
    if (!this.selectedAddonChannel || !this.addonQuote) return;

    this.addonPurchaseLoading = true;
    this.billingService.createChannelAddonOrder(this.selectedAddonChannel).subscribe({
      next: (orderRes) => {
        this.addonPurchaseLoading = false;
        this.launchChannelAddonCheckout(orderRes);
      },
      error: (err) => {
        this.addonPurchaseLoading = false;
        const detailMsg =
          typeof err?.error?.detail === 'string'
            ? err.error.detail
            : 'Failed to initiate channel add-on payment.';
        this.messageService.add({
          severity: 'error',
          summary: 'Order Failed',
          detail: detailMsg,
        });
      },
    });
  }

  private launchChannelAddonCheckout(orderRes: ChannelAddonOrderResponse): void {
    if (typeof (window as any).Razorpay === 'undefined') {
      this.messageService.add({
        severity: 'error',
        summary: 'Payment Gateway Error',
        detail: 'Razorpay SDK is not loaded. Please refresh the page and try again.',
      });
      return;
    }

    const channelInfo = this.CHANNEL_PRICING[orderRes.channel] || { name: orderRes.channel };
    const options: any = {
      key: orderRes.key_id,
      amount: orderRes.amount,
      currency: orderRes.currency || 'INR',
      name: 'LeadAI',
      description: `Add-on: ${channelInfo.name} (${orderRes.remaining_days} days prorated)`,
      order_id: orderRes.order_id,
      handler: (response: any) => {
        this.addonPurchaseLoading = true;
        this.billingService
          .verifyChannelAddonPayment({
            razorpay_order_id: response.razorpay_order_id,
            razorpay_payment_id: response.razorpay_payment_id,
            razorpay_signature: response.razorpay_signature,
            channel: orderRes.channel,
          })
          .subscribe({
            next: (recharge: ClientRecharge) => {
              this.addonPurchaseLoading = false;
              this.showAddonModal = false;
              this.messageService.add({
                severity: 'success',
                summary: 'Channel Activated!',
                detail: `${channelInfo.name} has been activated for your current cycle and synced with upcoming AutoPay.`,
                life: 8000,
              });
              this.loadData();
              this.loadPaymentHistory();
            },
            error: (err) => {
              this.addonPurchaseLoading = false;
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
          this.addonPurchaseLoading = false;
          this.billingService
            .recordPaymentFailure({
              razorpay_order_id: orderRes.order_id,
              error_code: 'DISMISSED',
              error_description: 'Channel add-on payment popup was closed without completion',
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
      this.addonPurchaseLoading = false;
      this.billingService
        .recordPaymentFailure({
          razorpay_order_id: orderRes.order_id,
          error_code: failRes?.error?.code || 'PAYMENT_FAILED',
          error_description: failRes?.error?.description || 'Channel add-on payment failed at gateway',
        })
        .subscribe(() => {
          this.loadPaymentHistory();
        });
      this.messageService.add({
        severity: 'error',
        summary: 'Payment Failed',
        detail: failRes?.error?.description || 'Your channel add-on payment could not be processed.',
      });
    });
    rzp.open();
  }

  isSuccessStatus(status: string | null | undefined): boolean {
    const s = (status || '').toLowerCase().trim();
    return ['active', 'success', 'superseded', 'exhausted', 'expired'].includes(s);
  }
}

