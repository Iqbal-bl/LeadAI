import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule, Router } from '@angular/router';
import { Subscription, forkJoin } from 'rxjs';

import { BillingService } from '../../services/billing.service';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../shared/services/toast.service';
import { environment } from '../../../environments/environment';

import {
  AddonItemSelection,
  BillingSummary,
  BoosterOption,
  ChannelAddonQuote,
  ClientRecharge,
  RechargePlanTemplate,
  UsageLog,
} from '../../models/billing.models';

// PrimeNG Modules
import { TableModule } from 'primeng/table';
import { ButtonModule } from 'primeng/button';
import { TagModule } from 'primeng/tag';
import { ProgressBarModule } from 'primeng/progressbar';
import { SkeletonModule } from 'primeng/skeleton';
import { TooltipModule } from 'primeng/tooltip';
import { CheckboxModule } from 'primeng/checkbox';
import { DialogModule } from 'primeng/dialog';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { TabsModule } from 'primeng/tabs';

/**
 * UsageComponent provides the operational Account, Quota & Ledger Hub for clients.
 * 
 * Features:
 * 1. Overview KPIs: Minutes balance, AutoPay status, active channels, cycle end date.
 * 2. Tab 1: Invoices & Recharge Transaction History (Ledger with PDF download and failure tooltips).
 * 3. Tab 2: Minute Balance Ledger (Calls & Credits activity tracking).
 * 4. Tab 3: Top-up & Channel Addons with real-time mid-cycle proration, multi-selection, and unified pay.
 */
@Component({
  selector: 'app-usage',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterModule,
    TableModule,
    ButtonModule,
    TagModule,
    ProgressBarModule,
    SkeletonModule,
    TooltipModule,
    CheckboxModule,
    DialogModule,
    ProgressSpinnerModule,
    TabsModule,
  ],
  templateUrl: './usage.component.html',
  styleUrl: './usage.component.scss',
})
export class UsageComponent implements OnInit, OnDestroy {
  private billingService = inject(BillingService);
  private authService = inject(AuthService);
  private toastService = inject(ToastService);
  private router = inject(Router);

  private subscriptions = new Subscription();

  /** Active Tab identifier */
  public activeTab: 'invoices' | 'ledger' | 'addons' = 'invoices';

  /** Billing summary and active subscription snapshot */
  public summary: BillingSummary | null = null;

  /** Ledger data collections */
  public paymentHistory: ClientRecharge[] = [];
  public usageLogs: UsageLog[] = [];
  public boosterPlans: RechargePlanTemplate[] = [];

  /** Loading indicators */
  public loadingSummary: boolean = true;
  public loadingHistory: boolean = false;
  public loadingLogs: boolean = false;
  public loadingAddons: boolean = false;

  /** Payment Processing Modal & Overlay */
  public isProcessingPayment: boolean = false;
  public paymentStepMessage: string = '';

  /** Omni-channel metadata definitions */
  public readonly CHANNEL_METADATA = [
    {
      key: 'whatsapp',
      name: 'WhatsApp Business API',
      icon: 'pi pi-whatsapp',
      color: '#22c55e',
      monthlyPrice: 2000,
      description: 'Automate inbound & outbound client chats with official Meta Cloud API.',
      features: ['24/7 AI conversational auto-reply', 'Inbound lead qualification scorecard', 'Direct advisor handoff notification'],
    },
    {
      key: 'instagram',
      name: 'Instagram DM Automation',
      icon: 'pi pi-instagram',
      color: '#a855f7',
      monthlyPrice: 2000,
      description: 'Turn comments and DMs into high-intent inbound customers instantly.',
      features: ['Automated Story & Reel reply triggers', 'Direct inbox lead qualification', 'Multi-agent handoff alerts'],
    },
    {
      key: 'facebook',
      name: 'Facebook Messenger',
      icon: 'pi pi-facebook',
      color: '#3b82f6',
      monthlyPrice: 2000,
      description: 'Engage visitors contacting your Facebook business page around the clock.',
      features: ['Page message instant answers', 'Post comment-to-DM conversion', 'Omni-channel customer profile link'],
    },
    {
      key: 'linkedin',
      name: 'LinkedIn Lead Automation',
      icon: 'pi pi-linkedin',
      color: '#0284c7',
      monthlyPrice: 3000,
      description: 'Connect, qualify, and message B2B prospects directly on LinkedIn.',
      features: ['B2B profile qualification', 'Automated connection outreach', 'Seamless CRM contact creation'],
    },
  ];

  /** Standard Minute Booster Top-Ups */
  public readonly BOOSTER_OPTIONS: BoosterOption[] = [
    {
      id: 'booster_100',
      name: '100 Min Booster',
      minutes: 100,
      price: 400,
      ratePerMinute: 4.0,
      badge: 'Popular',
      description: 'Instant credit of 100 call minutes with zero expiration while plan is active.',
    },
    {
      id: 'booster_250',
      name: '250 Min Booster',
      minutes: 250,
      price: 1000,
      ratePerMinute: 4.0,
      badge: 'Best Value',
      description: 'Recommended for high call volume campaigns and active sales sprints.',
    },
    {
      id: 'booster_500',
      name: '500 Min Booster',
      minutes: 500,
      price: 2000,
      ratePerMinute: 4.0,
      description: 'Maximum booster capacity for enterprise calling and outbound batches.',
    },
  ];

  /** Dynamic boosters loaded from backend DB templates */
  public dynamicBoosters: BoosterOption[] = [];

  get activeBoosters(): BoosterOption[] {
    return this.dynamicBoosters.length > 0 ? this.dynamicBoosters : this.BOOSTER_OPTIONS;
  }

  /** Map of calculated prorated quotes for channels */
  public channelQuotes: Record<string, ChannelAddonQuote> = {};

  /** Single selected booster top-up (only one booster allowed at a time) */
  public selectedBooster: BoosterOption | null = null;

  /** Selected modular channel add-ons */
  public selectedChannelsMap: Record<string, boolean> = {};

  ngOnInit(): void {
    this.loadSummary();
    this.loadAvailablePlans();
    this.loadPaymentHistory();
    this.loadUsageLogs();
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }

  /**
   * Loads dynamic plan templates (minute boosters and channel prices) from the DB.
   */
  public loadAvailablePlans(): void {
    this.subscriptions.add(
      this.billingService.getAvailablePlans().subscribe({
        next: (plans) => {
          // 1. Minute Boosters (topup)
          const boosters = plans.filter(
            (p) => p.plan_type === 'topup' || p.plan_category === 'voice_topup'
          );
          if (boosters.length > 0) {
            this.dynamicBoosters = boosters.map((b) => ({
              id: b.id,
              name: b.name,
              minutes: b.included_minutes,
              price: b.price,
              ratePerMinute: b.rate_per_minute || 4.0,
              badge: b.included_minutes === 100 ? 'Popular' : (b.included_minutes === 250 ? 'Best Value' : undefined),
              description: b.description || `Instant credit of ${b.included_minutes} call minutes with zero expiration while plan is active.`,
            }));
          }

          // 2. Channel Addon Prices
          const channelTemplates = plans.filter(
            (p) => p.plan_category === 'channel_addon' && p.feature_key
          );
          channelTemplates.forEach((ct) => {
            const meta = this.CHANNEL_METADATA.find((m) => m.key === ct.feature_key);
            if (meta) {
              meta.monthlyPrice = ct.price;
            }
          });
        },
        error: (err) => {
          console.warn('Could not load dynamic plans for boosters and channels:', err);
        },
      })
    );
  }

  /**
   * Loads active subscription plan and minute quota summary.
   */
  public loadSummary(): void {
    this.loadingSummary = true;
    this.subscriptions.add(
      this.billingService.getCurrentPlan().subscribe({
        next: (res) => {
          this.summary = res;
          this.loadingSummary = false;
          // Pre-fetch quotes if active plan exists
          if (res?.active_recharge) {
            this.fetchChannelQuotes();
          }
        },
        error: (err) => {
          this.loadingSummary = false;
          console.warn('Could not load billing summary:', err);
        },
      })
    );
  }

  /**
   * Loads invoices and recharge transaction records for Tab 1.
   */
  public loadPaymentHistory(): void {
    this.loadingHistory = true;
    this.subscriptions.add(
      this.billingService.getPaymentHistory(100).subscribe({
        next: (data) => {
          this.paymentHistory = data;
          this.loadingHistory = false;
        },
        error: (err) => {
          this.loadingHistory = false;
          this.toastService.error(
            err?.error?.detail || 'Failed to load transaction history.',
            'History Error'
          );
        },
      })
    );
  }

  /**
   * Loads call minute deduction logs for Tab 2.
   */
  public loadUsageLogs(): void {
    this.loadingLogs = true;
    this.subscriptions.add(
      this.billingService.getUsageHistory(100).subscribe({
        next: (logs) => {
          this.usageLogs = logs;
          this.loadingLogs = false;
        },
        error: (err) => {
          this.loadingLogs = false;
          console.warn('Could not load call usage history:', err);
        },
      })
    );
  }

  /**
   * Fetches real-time prorated quotes for available channels.
   */
  public fetchChannelQuotes(): void {
    this.CHANNEL_METADATA.forEach((meta) => {
      if (!this.isChannelActive(meta.key)) {
        this.subscriptions.add(
          this.billingService.getChannelAddonQuote(meta.key).subscribe({
            next: (quote) => {
              this.channelQuotes[meta.key] = quote;
            },
            error: () => {
              // Quote may fail if plan has expired
            },
          })
        );
      }
    });
  }

  /**
   * Checks whether a channel is active in the company's current cycle.
   */
  public isChannelActive(channelKey: string): boolean {
    const activeChannels = this.summary?.active_recharge?.active_channels || [];
    return activeChannels.map((c) => c.toLowerCase()).includes(channelKey.toLowerCase());
  }

  /**
   * Checks whether a channel is set to renew next cycle.
   */
  public isChannelRenewing(channelKey: string): boolean {
    const nextChannels = this.summary?.active_recharge?.next_cycle_channels;
    if (!nextChannels || nextChannels.length === 0) {
      return this.isChannelActive(channelKey);
    }
    return nextChannels.map((c) => c.toLowerCase()).includes(channelKey.toLowerCase());
  }

  /**
   * Calculates quota percentage for progress indicator.
   */
  public getQuotaPercentage(): number {
    const active = this.summary?.active_recharge;
    if (!active || !active.purchased_minutes || active.purchased_minutes <= 0) return 0;
    const pct = (active.remaining_minutes / active.purchased_minutes) * 100;
    return Math.min(100, Math.max(0, Math.round(pct)));
  }

  /**
   * Downloads official PDF tax invoice with authenticated bearer token.
   */
  public downloadInvoice(recharge: ClientRecharge): void {
    const rechargeId = recharge.id;
    if (!rechargeId) return;

    const token = this.authService.getValue('accessToken') || this.authService.getStaffToken();
    const invoiceUrl = `${environment.apiPrefix}/billing/invoices/${rechargeId}/download?token=${encodeURIComponent(token || '')}`;
    window.open(invoiceUrl, '_blank');
  }

  /**
   * Selects or toggles a single minute booster top-up.
   * Only one booster can be selected at any given time.
   */
  public selectBooster(booster: BoosterOption): void {
    if (this.selectedBooster?.id === booster.id) {
      this.selectedBooster = null;
    } else {
      this.selectedBooster = booster;
    }
  }

  /**
   * Checks whether a specific booster is currently selected.
   */
  public isBoosterSelected(boosterId: string): boolean {
    return this.selectedBooster?.id === boosterId;
  }

  /**
   * Toggles selection of an omni-channel add-on.
   * Already active channels cannot be selected.
   */
  public toggleChannelSelection(channelKey: string): void {
    if (this.isChannelActive(channelKey)) {
      return;
    }
    this.selectedChannelsMap[channelKey] = !this.selectedChannelsMap[channelKey];
  }

  /**
   * Checks whether a channel is selected for purchase.
   */
  public isChannelSelected(channelKey: string): boolean {
    return !!this.selectedChannelsMap[channelKey] && !this.isChannelActive(channelKey);
  }

  /**
   * Returns list of selected channel metadata objects with prorated pricing.
   */
  public getSelectedChannelsList(): Array<{
    key: string;
    name: string;
    icon: string;
    color: string;
    monthlyPrice: number;
    proratedPrice: number;
    remainingDays?: number;
  }> {
    const list: Array<{
      key: string;
      name: string;
      icon: string;
      color: string;
      monthlyPrice: number;
      proratedPrice: number;
      remainingDays?: number;
    }> = [];

    this.CHANNEL_METADATA.forEach((meta) => {
      if (this.isChannelSelected(meta.key)) {
        const quote = this.channelQuotes[meta.key];
        const prorated = quote ? quote.prorated_price : meta.monthlyPrice;
        list.push({
          key: meta.key,
          name: meta.name,
          icon: meta.icon,
          color: meta.color,
          monthlyPrice: meta.monthlyPrice,
          proratedPrice: prorated,
          remainingDays: quote?.remaining_days,
        });
      }
    });

    return list;
  }

  /**
   * Cancels an active channel add-on from renewing on the next billing cycle.
   */
  public cancelChannelForNextCycle(channelKey: string): void {
    this.isProcessingPayment = true;
    this.paymentStepMessage = `Cancelling ${channelKey.toUpperCase()} for next cycle...`;
    this.subscriptions.add(
      this.billingService.cancelChannel(channelKey).subscribe({
        next: (res) => {
          this.isProcessingPayment = false;
          this.toastService.success(
            res.message || `${channelKey.toUpperCase()} will not renew on next cycle.`,
            'Auto-Renewal Updated'
          );
          this.loadSummary();
        },
        error: (err) => {
          this.isProcessingPayment = false;
          this.toastService.error(
            err?.error?.detail || `Failed to cancel ${channelKey}.`,
            'Action Failed'
          );
        },
      })
    );
  }

  /**
   * Restores an active channel add-on to renew automatically on next cycle (₹0 charge).
   */
  public resumeChannelForNextCycle(channelKey: string): void {
    this.isProcessingPayment = true;
    this.paymentStepMessage = `Restoring ${channelKey.toUpperCase()} auto-renewal for next cycle...`;
    this.subscriptions.add(
      this.billingService.resumeChannel(channelKey).subscribe({
        next: (res) => {
          this.isProcessingPayment = false;
          this.toastService.success(
            res.message || `${channelKey.toUpperCase()} auto-renewal restored for next cycle.`,
            'Auto-Renewal Restored'
          );
          this.loadSummary();
        },
        error: (err) => {
          this.isProcessingPayment = false;
          this.toastService.error(
            err?.error?.detail || `Failed to restore ${channelKey}.`,
            'Action Failed'
          );
        },
      })
    );
  }

  /**
   * Computes the base subtotal for all selected items.
   */
  public getSubtotal(): number {
    let sum = 0;
    if (this.selectedBooster) {
      sum += this.selectedBooster.price;
    }
    this.getSelectedChannelsList().forEach((ch) => {
      sum += ch.proratedPrice;
    });
    return sum;
  }

  /**
   * Calculates 18% standard GST on the subtotal.
   */
  public getGstAmount(): number {
    return Math.round(this.getSubtotal() * 0.18 * 100) / 100;
  }

  /**
   * Computes grand total payable including GST.
   */
  public getGrandTotal(): number {
    return Math.round((this.getSubtotal() + this.getGstAmount()) * 100) / 100;
  }

  /**
   * Checks if user has selected any items for checkout.
   */
  public hasCheckoutItems(): boolean {
    return !!this.selectedBooster || this.getSelectedChannelsList().length > 0;
  }

  /**
   * Initiates payment checkout from the right-docked calculator.
   */
  public initiateSelectedCheckout(): void {
    if (!this.hasCheckoutItems()) {
      this.toastService.warn('Please select a booster or channel to checkout.', 'Nothing Selected');
      return;
    }

    const items: AddonItemSelection[] = [];
    if (this.selectedBooster) {
      items.push({
        id: this.selectedBooster.id,
        type: 'topup',
        key: this.selectedBooster.id,
        name: this.selectedBooster.name,
        price: this.selectedBooster.price,
        minutes: this.selectedBooster.minutes,
      });
    }

    this.getSelectedChannelsList().forEach((ch) => {
      items.push({
        id: ch.key,
        type: 'channel',
        key: ch.key,
        name: ch.name,
        price: ch.proratedPrice,
        regularPrice: ch.monthlyPrice,
        isProrated: true,
        remainingDays: ch.remainingDays,
      });
    });

    this.processCheckoutQueue(items, 0);
  }

  /**
   * Sequential execution queue for multiple selected items with progressive feedback.
   */
  private processCheckoutQueue(items: AddonItemSelection[], index: number = 0): void {
    if (index >= items.length) {
      this.isProcessingPayment = false;
      this.selectedBooster = null;
      this.selectedChannelsMap = {};
      this.toastService.success(
        'All selected add-ons and top-ups have been successfully processed!',
        'Checkout Completed'
      );
      this.loadSummary();
      this.loadPaymentHistory();
      this.loadUsageLogs();
      return;
    }

    const current = items[index];
    this.isProcessingPayment = true;
    this.paymentStepMessage = `Preparing checkout (${index + 1} of ${items.length}): ${current.name}...`;

    if (current.type === 'channel') {
      this.billingService.createChannelAddonOrder(current.key).subscribe({
        next: (orderRes) => {
          this.launchRazorpayModal(
            orderRes,
            (paymentRes: any) => {
              this.verifyChannelPayment(
                orderRes,
                current.key,
                () => {
                  delete this.selectedChannelsMap[current.key];
                  this.processCheckoutQueue(items, index + 1);
                },
                paymentRes
              );
            },
            () => {
              this.isProcessingPayment = false;
              this.toastService.warn('Checkout window closed. Transaction was not completed.', 'Checkout Closed');
              this.loadPaymentHistory();
            }
          );
        },
        error: (err) => {
          this.isProcessingPayment = false;
          this.toastService.error(
            err?.error?.detail || `Failed to create order for ${current.name}.`,
            'Payment Error'
          );
        },
      });
    } else {
      // Booster top-up
      this.initiateBoosterDirectOrder(
        current,
        () => {
          this.selectedBooster = null;
          this.processCheckoutQueue(items, index + 1);
        },
        () => {
          this.isProcessingPayment = false;
          this.toastService.warn('Checkout window closed. Top-up was not completed.', 'Checkout Closed');
          this.loadPaymentHistory();
        }
      );
    }
  }

  /**
   * Verifies channel addon payment signature and updates client entitlements.
   */
  private verifyChannelPayment(
    orderRes: any,
    channelKey: string,
    onSuccess: () => void,
    paymentRes?: any
  ): void {
    this.isProcessingPayment = true;
    this.paymentStepMessage = 'Verifying payment with Razorpay and updating channel entitlement...';

    const payload = {
      razorpay_order_id: paymentRes?.razorpay_order_id || orderRes.order_id,
      razorpay_payment_id: paymentRes?.razorpay_payment_id || orderRes.payment_id || '',
      razorpay_signature: paymentRes?.razorpay_signature || orderRes.signature || '',
      channel: channelKey,
    };

    this.billingService.verifyChannelAddonPayment(payload).subscribe({
      next: () => {
        this.toastService.success(
          `${channelKey.toUpperCase()} has been successfully activated for your account!`,
          'Channel Activated'
        );
        this.loadSummary();
        this.loadPaymentHistory();
        onSuccess();
      },
      error: (err) => {
        this.isProcessingPayment = false;
        this.toastService.error(
          err?.error?.detail || 'Signature verification failed. Please contact support.',
          'Verification Error'
        );
      },
    });
  }

  /**
   * Booster direct order launcher.
   */
  private initiateBoosterDirectOrder(
    item: AddonItemSelection,
    onSuccess: () => void,
    onDismiss?: () => void
  ): void {
    this.billingService.getAvailablePlans().subscribe({
      next: (plans) => {
        const directMatch = plans.find((p) => p.id === item.id);
        const fallbackMatch = plans.find(
          (p) => p.included_minutes === item.minutes || p.name.toLowerCase().includes(item.name.toLowerCase())
        );
        const planId = directMatch ? directMatch.id : (fallbackMatch ? fallbackMatch.id : plans[0]?.id);

        if (!planId) {
          this.isProcessingPayment = false;
          this.toastService.error('No matching plan template found for this booster.', 'Plan Error');
          return;
        }

        this.billingService.createRazorpayOrder(planId).subscribe({
          next: (orderRes) => {
            this.launchRazorpayModal(
              orderRes,
              (paymentRes: any) => {
                this.isProcessingPayment = true;
                this.paymentStepMessage = 'Crediting minutes to your account balance...';
                this.billingService
                  .verifyRazorpayPayment({
                    razorpay_order_id: paymentRes.razorpay_order_id,
                    razorpay_payment_id: paymentRes.razorpay_payment_id,
                    razorpay_signature: paymentRes.razorpay_signature,
                    plan_template_id: planId,
                  })
                  .subscribe({
                    next: () => {
                      this.toastService.success(
                        `${item.minutes} Call Minutes have been added to your balance!`,
                        'Top-Up Successful'
                      );
                      onSuccess();
                    },
                    error: (verErr) => {
                      this.isProcessingPayment = false;
                      this.toastService.error(
                        verErr?.error?.detail || 'Failed to verify top-up.',
                        'Verification Error'
                      );
                    },
                  });
              },
              () => {
                if (onDismiss) {
                  onDismiss();
                } else {
                  this.isProcessingPayment = false;
                }
              }
            );
          },
          error: (err) => {
            this.isProcessingPayment = false;
            this.toastService.error(
              err?.error?.detail || 'Failed to initiate booster payment.',
              'Order Error'
            );
          },
        });
      },
      error: () => {
        this.isProcessingPayment = false;
      },
    });
  }

  /**
   * Opens standard Razorpay Checkout modal with callback handlers.
   */
  private launchRazorpayModal(
    orderRes: any,
    onSuccess: (paymentRes: any) => void,
    onDismiss: () => void
  ): void {
    if (typeof (window as any).Razorpay === 'undefined') {
      this.toastService.error(
        'Razorpay SDK not loaded. Please check your internet connection.',
        'Payment Gateway'
      );
      this.isProcessingPayment = false;
      onDismiss();
      return;
    }

    const options = {
      key: orderRes.key_id,
      amount: orderRes.amount,
      currency: orderRes.currency || 'INR',
      name: 'LeadAI Automation',
      description: orderRes.channel ? `Channel Add-on: ${orderRes.channel.toUpperCase()}` : 'Recharge Top-Up',
      order_id: orderRes.order_id,
      handler: (response: any) => {
        onSuccess(response);
      },
      modal: {
        ondismiss: () => {
          this.billingService
            .recordPaymentFailure({
              order_id: orderRes.order_id,
              error_code: 'CHECKOUT_DISMISSED',
              error_description: 'Checkout window closed before completing payment.',
            })
            .subscribe();
          onDismiss();
        },
      },
      theme: {
        color: '#6366f1',
      },
    };

    const rzp = new (window as any).Razorpay(options);
    rzp.on('payment.failed', (failRes: any) => {
      this.billingService
        .recordPaymentFailure({
          order_id: orderRes.order_id,
          razorpay_payment_id: failRes?.error?.metadata?.payment_id,
          payment_id: failRes?.error?.metadata?.payment_id,
          error_code: failRes?.error?.code || 'PAYMENT_FAILED',
          error_description: failRes?.error?.description || 'Payment was declined or failed at gateway.',
        })
        .subscribe();
      this.isProcessingPayment = false;
      this.toastService.error(failRes?.error?.description || 'Payment failed.', 'Payment Failed');
      this.loadPaymentHistory();
    });
    rzp.open();
  }

  /**
   * Navigates to the Plans & Pricing purchase funnel.
   */
  public navigateToPricing(): void {
    this.router.navigate(['/client/plans-pricing']);
  }
}
