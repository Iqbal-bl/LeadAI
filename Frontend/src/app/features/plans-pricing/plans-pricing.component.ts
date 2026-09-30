import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule, Router } from '@angular/router';
import { Subscription } from 'rxjs';

import { BillingService } from '../../services/billing.service';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../shared/services/toast.service';

import {
  BillingSummary,
  CustomBundlePayload,
  PricingChannelOption,
  RazorpaySubscriptionResponse,
  RechargePlanTemplate,
} from '../../models/billing.models';

// PrimeNG Modules
import { ButtonModule } from 'primeng/button';
import { TagModule } from 'primeng/tag';
import { SliderModule } from 'primeng/slider';
import { InputNumberModule } from 'primeng/inputnumber';
import { CheckboxModule } from 'primeng/checkbox';
import { SkeletonModule } from 'primeng/skeleton';
import { TooltipModule } from 'primeng/tooltip';
import { DialogModule } from 'primeng/dialog';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { DividerModule } from 'primeng/divider';

/**
 * PlansPricingComponent provides a guided, self-service purchase and upgrade funnel.
 *
 * Workflow:
 * 1. Step 1: Base Plan Selection (Monthly Basic, Monthly Pro + 1 Custom Plan slider up to 6,000 mins @ ₹4/min).
 * 2. Step 2: Omni-Channel Social Media Addon Selection (WhatsApp, Instagram, Facebook, LinkedIn for 30 days).
 * 3. Persistent Sticky Price Meter: Right-docked summary with line-item pricing and AutoPay total.
 * 4. Razorpay AutoPay Mandate Checkout with verification loading overlay and automatic redirect to /client/usage.
 */
@Component({
  selector: 'app-plans-pricing',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterModule,
    ButtonModule,
    TagModule,
    SliderModule,
    InputNumberModule,
    CheckboxModule,
    SkeletonModule,
    TooltipModule,
    DialogModule,
    ProgressSpinnerModule,
    DividerModule,
  ],
  templateUrl: './plans-pricing.component.html',
  styleUrl: './plans-pricing.component.scss',
})
export class PlansPricingComponent implements OnInit, OnDestroy {
  private billingService = inject(BillingService);
  private authService = inject(AuthService);
  private toastService = inject(ToastService);
  private router = inject(Router);

  private subscriptions = new Subscription();

  /** Current Funnel Step: 1 = Base Plan, 2 = Omni-Channel Addons */
  public currentStep: 1 | 2 = 1;

  /** Master standard plans loaded from backend */
  public standardPlans: RechargePlanTemplate[] = [];
  public isLoadingPlans: boolean = true;

  /** Current active subscription status to prevent duplicate base plan purchases */
  public currentSummary: BillingSummary | null = null;
  public hasActivePlan: boolean = false;
  public isLoadingSummary: boolean = true;

  /** Base Plan Selection Type: 'standard' or 'custom' */
  public selectedPlanType: 'standard' | 'custom' = 'standard';

  /** Selected standard plan template (if standard chosen) */
  public selectedStandardPlan: RechargePlanTemplate | null = null;

  /** Custom Plan Configuration */
  public customMinutes: number = 500;
  public readonly MIN_CUSTOM_MINUTES: number = 100;
  public readonly MAX_CUSTOM_MINUTES: number = 6000;
  public readonly CUSTOM_RATE_PER_MINUTE: number = 4.0;

  /** Omni-Channel Addon Options */
  public readonly CHANNEL_OPTIONS: PricingChannelOption[] = [
    {
      key: 'whatsapp',
      name: 'WhatsApp Business API',
      icon: 'pi pi-whatsapp',
      color: '#22c55e',
      monthlyPrice: 2000,
      durationDays: 30,
      description: 'Official Meta Cloud API integration for automated 24/7 lead chats and appointment scheduling.',
      features: [
        'Official Meta Cloud API webhook routing',
        '24/7 AI conversational auto-replies',
        'Instant qualification scorecard & handoff alerts',
        '30-day recurring synchronization',
      ],
    },
    {
      key: 'instagram',
      name: 'Instagram DM Automation',
      icon: 'pi pi-instagram',
      color: '#a855f7',
      monthlyPrice: 2000,
      durationDays: 30,
      description: 'Engage high-intent prospects reaching out via Instagram direct messages and reel comments.',
      features: [
        'Direct message automatic AI response funnel',
        'Post and Story comment-to-DM triggers',
        'Lead scoring and sentiment analysis',
        '30-day recurring synchronization',
      ],
    },
    {
      key: 'facebook',
      name: 'Facebook Messenger',
      icon: 'pi pi-facebook',
      color: '#3b82f6',
      monthlyPrice: 2000,
      durationDays: 30,
      description: 'Turn Facebook page visitors into qualified opportunities with zero delay.',
      features: [
        'Business page inbox AI integration',
        'Post comment auto-replies to Messenger',
        'Multi-channel customer contact linking',
        '30-day recurring synchronization',
      ],
    },
    {
      key: 'linkedin',
      name: 'LinkedIn Lead Automation',
      icon: 'pi pi-linkedin',
      color: '#0284c7',
      monthlyPrice: 3000,
      durationDays: 30,
      description: 'Automate connection messaging, B2B lead qualification, and CRM syncing on LinkedIn.',
      features: [
        'B2B profile qualification & matching',
        'Automated connection and InMail follow-ups',
        'Real-time CRM contact creation',
        '30-day recurring synchronization',
      ],
    },
  ];

  /** Selected channels map */
  public selectedChannelsMap: Record<string, boolean> = {};

  /** Active Plan States */
  public hasActiveAutoPay: boolean = false;
  public isManualPlan: boolean = false;
  public currentActivePlanId: string | null = null;
  public currentMinutes: number = 0;
  public currentPrice: number = 0;
  public activeSubscriptionChannels: string[] = [];

  /** Checkout & Verification States */
  public isCheckingOut: boolean = false;
  public isVerifyingPayment: boolean = false;
  public verificationMessage: string = '';

  ngOnInit(): void {
    this.loadCurrentSummary();
    this.loadPlans();
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }

  /**
   * Loads current billing summary to check if company already has an active subscription.
   */
  public loadCurrentSummary(): void {
    this.isLoadingSummary = true;
    this.subscriptions.add(
      this.billingService.getCurrentPlan().subscribe({
        next: (summary) => {
          this.currentSummary = summary;
          const active = summary?.active_recharge;
          if (active && active.status === 'active') {
            const isNotExpired = !active.expires_at || new Date(active.expires_at).getTime() > Date.now();
            this.hasActivePlan = isNotExpired;
            this.hasActiveAutoPay = isNotExpired && !!active.razorpay_subscription_id && !active.cancel_at_period_end;
            this.isManualPlan = isNotExpired && !active.razorpay_subscription_id;
            this.currentActivePlanId = (active as any).plan_template_id || null;
            this.currentMinutes = active.purchased_minutes || 0;
            this.currentPrice = active.price_paid || 0;
            this.activeSubscriptionChannels = active.active_channels || [];
          } else {
            this.hasActivePlan = false;
            this.hasActiveAutoPay = false;
            this.isManualPlan = false;
            this.currentActivePlanId = null;
            this.currentMinutes = 0;
            this.currentPrice = 0;
            this.activeSubscriptionChannels = [];
          }
          this.isLoadingSummary = false;
        },
        error: () => {
          this.isLoadingSummary = false;
        },
      })
    );
  }

  /**
   * Loads standard master plans from backend and selects the first available plan by default.
   */
  public loadPlans(): void {
    this.isLoadingPlans = true;
    this.subscriptions.add(
      this.billingService.getAvailablePlans().subscribe({
        next: (plans) => {
          // Base Voice Plans: strictly plans with positive minutes, validity <= 90 (monthly), excluding channel addons, boosters, and self bundles
          const filtered = plans.filter(
            (p) =>
              (p.plan_category === 'voice_standard' || (p.plan_type === 'standard' && (!p.plan_category || p.plan_category === 'voice_standard'))) &&
              p.included_minutes > 0 &&
              p.plan_category !== 'channel_addon' &&
              p.plan_category !== 'client_self_bundle' &&
              p.plan_category !== 'voice_topup' &&
              p.plan_type !== 'topup' &&
              !p.name.toLowerCase().includes('booster') &&
              !p.name.toLowerCase().includes('yearly') &&
              p.validity_days <= 90
          );

          // Deduplicate by plan ID
          const seenIds = new Set<string>();
          const distinctPlans: RechargePlanTemplate[] = [];
          for (const plan of filtered) {
            if (!seenIds.has(plan.id)) {
              seenIds.add(plan.id);
              distinctPlans.push(plan);
            }
          }

          // Sort by price ascending and display ALL active plans created by Super Admin
          distinctPlans.sort((a, b) => a.price - b.price);
          this.standardPlans = distinctPlans;

          // Dynamically sync channel addon prices from database templates
          const channelTemplates = plans.filter(
            (p) => p.plan_category === 'channel_addon' && p.feature_key
          );
          channelTemplates.forEach((ct) => {
            const opt = this.CHANNEL_OPTIONS.find((ch) => ch.key === ct.feature_key);
            if (opt) {
              opt.monthlyPrice = ct.price;
            }
          });

          if (this.standardPlans.length > 0) {
            // If user has active plan, try to pre-select an upgrade plan if available, else first plan
            const upgradePlan = this.standardPlans.find((p) => this.isHigherTier(p));
            this.selectedStandardPlan = upgradePlan || this.standardPlans[0];
            this.selectedPlanType = 'standard';
          }
          this.isLoadingPlans = false;
        },
        error: (err) => {
          this.isLoadingPlans = false;
          this.toastService.error(
            err?.error?.detail || 'Failed to load master plan templates.',
            'Plans Error'
          );
        },
      })
    );
  }

  /**
   * Formats a clean display title from the database plan template.
   */
  public getPlanDisplayTitle(plan: RechargePlanTemplate): string {
    return plan.name || 'Standard Voice Plan';
  }

  /**
   * Checks whether a plan template is configured for recurring AutoPay.
   * Driven dynamically by the database template's auto_pay_by_default field.
   */
  public isAutoPayPlan(plan: RechargePlanTemplate): boolean {
    return plan?.auto_pay_by_default !== false;
  }

  public isCurrentPlan(plan: RechargePlanTemplate): boolean {
    if (!this.hasActivePlan || !this.currentSummary?.active_recharge) return false;
    const active = this.currentSummary.active_recharge;
    if (this.currentActivePlanId && this.currentActivePlanId === plan.id) return true;
    if (active.plan_name_snapshot && plan.name && active.plan_name_snapshot.toLowerCase().trim() === plan.name.toLowerCase().trim()) return true;
    return active.purchased_minutes === plan.included_minutes && Math.abs(active.price_paid - plan.price) < 1;
  }

  public isHigherTier(plan: RechargePlanTemplate): boolean {
    if (!this.hasActivePlan) return false;
    return (plan.included_minutes || 0) > this.currentMinutes || (plan.price || 0) > this.currentPrice;
  }

  public isPlanLocked(plan: RechargePlanTemplate): boolean {
    if (this.isCurrentPlan(plan)) return true;
    if (this.hasActiveAutoPay) {
      return !this.isHigherTier(plan);
    }
    return false;
  }

  public getPlanActionLabel(plan: RechargePlanTemplate): string {
    if (this.isCurrentPlan(plan)) {
      return 'Current Plan Active';
    }
    if (this.hasActiveAutoPay) {
      if (this.isHigherTier(plan)) {
        return this.selectedStandardPlan?.id === plan.id ? 'Selected for Upgrade' : 'Upgrade to ' + (plan.name || 'Tier');
      }
      return 'Plan Active (Locked)';
    }
    if (this.isManualPlan) {
      if (this.selectedPlanType === 'standard' && this.selectedStandardPlan?.id === plan.id) {
        return 'Selected';
      }
      return this.isHigherTier(plan) ? 'Upgrade to ' + (plan.name || 'Tier') : 'Select ' + (plan.name || 'Tier');
    }
    if (this.selectedPlanType === 'standard' && this.selectedStandardPlan?.id === plan.id) {
      return 'Selected';
    }
    return 'Select ' + this.getPlanDisplayTitle(plan);
  }

  /**
   * Selects a standard pre-configured master plan.
   */
  public selectStandardPlan(plan: RechargePlanTemplate): void {
    if (this.isPlanLocked(plan)) return;
    this.selectedPlanType = 'standard';
    this.selectedStandardPlan = plan;
  }

  /**
   * Selects the custom plan card.
   */
  public selectCustomPlan(): void {
    if (this.hasActiveAutoPay) return;
    this.selectedPlanType = 'custom';
  }

  /**
   * Clamps and updates custom minute allocation.
   *
   * @param minutes Requested call minutes
   */
  public onCustomMinutesChange(minutes: number): void {
    if (this.hasActivePlan) return;
    if (isNaN(minutes)) minutes = this.MIN_CUSTOM_MINUTES;
    const clamped = Math.min(
      this.MAX_CUSTOM_MINUTES,
      Math.max(this.MIN_CUSTOM_MINUTES, minutes)
    );
    this.customMinutes = Math.round(clamped / 50) * 50;
  }

  /**
   * Toggles an omni-channel add-on on or off.
   *
   * @param channelKey Channel identifier ('whatsapp', 'instagram', etc.)
   */
  public toggleChannel(channelKey: string): void {
    if (this.hasActivePlan) {
      this.toastService.warn(
        'Omni-channel add-ons cannot be modified while an active subscription is running.',
        'Active Plan Running'
      );
      return;
    }
    this.selectedChannelsMap[channelKey] = !this.selectedChannelsMap[channelKey];
  }

  /**
   * Checks if an omni-channel add-on is currently active in the user's ongoing subscription.
   */
  public isChannelActiveOnSubscription(key: string): boolean {
    return this.activeSubscriptionChannels.includes(key);
  }

  /**
   * Returns list of selected channel options.
   */
  public getSelectedChannels(): PricingChannelOption[] {
    return this.CHANNEL_OPTIONS.filter((ch) => this.selectedChannelsMap[ch.key]);
  }

  /**
   * Calculates monthly price of the selected base plan (standard or custom).
   */
  public getBasePlanPrice(): number {
    if (this.selectedPlanType === 'custom') {
      return this.customMinutes * this.CUSTOM_RATE_PER_MINUTE;
    }
    return this.selectedStandardPlan?.price || 0;
  }

  /**
   * Returns minutes included in the selected base plan.
   */
  public getBasePlanMinutes(): number {
    if (this.selectedPlanType === 'custom') {
      return this.customMinutes;
    }
    return this.selectedStandardPlan?.included_minutes || 0;
  }

  /**
   * Returns formatted display name of the selected base plan.
   */
  public getBasePlanName(): string {
    if (this.selectedPlanType === 'custom') {
      return `Custom Bundle (${this.customMinutes} Mins)`;
    }
    return this.selectedStandardPlan?.name || 'Standard Plan';
  }

  /**
   * Calculates the combined monthly cost of all selected channels.
   */
  public getChannelsTotal(): number {
    return this.getSelectedChannels().reduce((acc, ch) => acc + ch.monthlyPrice, 0);
  }

  /**
   * Computes the grand total monthly recurring AutoPay charge.
   */
  public getTotalMonthlyPrice(): number {
    return this.getBasePlanPrice() + this.getChannelsTotal();
  }

  /**
   * Navigates between funnel stages.
   *
   * @param step Step number (1 or 2)
   */
  public goToStep(step: 1 | 2): void {
    if (this.hasActiveAutoPay && this.selectedStandardPlan && !this.isHigherTier(this.selectedStandardPlan)) {
      this.toastService.warn(
        'You already have an active subscription for this tier. You can upgrade to a higher tier or top up minutes.',
        'Active Plan Running'
      );
      return;
    }
    if (step === 2 && !this.selectedStandardPlan && this.selectedPlanType === 'standard') {
      this.toastService.warn('Please select a base plan to continue.', 'Selection Required');
      return;
    }
    this.currentStep = step;
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  /**
   * Initiates recurring AutoPay subscription checkout via Razorpay.
   */
  public initiateAutoPayCheckout(): void {
    if (this.hasActiveAutoPay && this.selectedStandardPlan && !this.isHigherTier(this.selectedStandardPlan)) {
      this.toastService.warn(
        'You already have an active subscription for this tier. You can upgrade to a higher tier or top up minutes.',
        'Active Plan Running'
      );
      return;
    }
    const selectedChannels = this.getSelectedChannels().map((ch) => ch.key);
    this.isCheckingOut = true;

    // Case 1: Custom Plan OR Standard Plan with modular channel add-ons
    // -> Use custom bundle subscription endpoint
    if (this.selectedPlanType === 'custom' || selectedChannels.length > 0) {
      const payload: CustomBundlePayload = {
        include_voice: true,
        voice_minutes: this.getBasePlanMinutes(),
        channels: selectedChannels,
        billing_cycle: 'monthly',
      };

      this.subscriptions.add(
        this.billingService.createCustomBundle(payload).subscribe({
          next: (subRes: RazorpaySubscriptionResponse) => {
            this.isCheckingOut = false;
            this.launchRazorpaySubscriptionModal(subRes);
          },
          error: (err: any) => {
            this.isCheckingOut = false;
            this.toastService.error(
              err?.error?.detail || 'Failed to initiate custom bundle subscription.',
              'Checkout Error'
            );
          },
        })
      );
    } else {
      // Case 2: Standard Master Plan with no extra channels
      const planId = this.selectedStandardPlan?.id;
      if (!planId) {
        this.isCheckingOut = false;
        this.toastService.error('Please select a valid plan.', 'Plan Error');
        return;
      }

      this.subscriptions.add(
        this.billingService.createRazorpaySubscription(planId).subscribe({
          next: (subRes: RazorpaySubscriptionResponse) => {
            this.isCheckingOut = false;
            this.launchRazorpaySubscriptionModal(subRes);
          },
          error: (err: any) => {
            this.isCheckingOut = false;
            this.toastService.error(
              err?.error?.detail || 'Failed to initiate subscription mandate.',
              'Checkout Error'
            );
          },
        })
      );
    }
  }

  /**
   * Opens standard Razorpay Checkout modal for recurring subscription mandate.
   *
   * @param subRes Razorpay subscription initialization response
   */
  private launchRazorpaySubscriptionModal(subRes: RazorpaySubscriptionResponse): void {
    if (typeof (window as any).Razorpay === 'undefined') {
      this.toastService.error(
        'Razorpay checkout SDK not loaded. Please verify your connection.',
        'Gateway Error'
      );
      return;
    }

    const options = {
      key: subRes.key_id,
      subscription_id: subRes.subscription_id,
      name: 'LeadAI Automation',
      description: `30-Day AutoPay Plan: ${subRes.plan_name}`,
      handler: (response: any) => {
        this.verifySubscriptionPayment(subRes, response);
      },
      modal: {
        ondismiss: () => {
          this.handleCheckoutDismiss(subRes);
        },
      },
      theme: {
        color: '#6366f1',
      },
    };

    const rzp = new (window as any).Razorpay(options);
    rzp.on('payment.failed', (failRes: any) => {
      this.handlePaymentFailure(subRes, failRes);
    });
    rzp.open();
  }

  /**
   * Verifies payment signature and redirects to /client/usage on success.
   *
   * @param subRes Subscription response details
   * @param paymentRes Razorpay payment verification callback
   */
  private verifySubscriptionPayment(subRes: RazorpaySubscriptionResponse, paymentRes: any): void {
    this.isVerifyingPayment = true;
    this.verificationMessage = 'Verifying AutoPay mandate and activating your 30-day plan...';

    const verifyPayload = {
      razorpay_subscription_id: paymentRes.razorpay_subscription_id || subRes.subscription_id,
      razorpay_payment_id: paymentRes.razorpay_payment_id,
      razorpay_signature: paymentRes.razorpay_signature,
      plan_template_id: subRes.plan_id,
    };

    this.subscriptions.add(
      this.billingService.verifyRazorpaySubscription(verifyPayload).subscribe({
        next: () => {
          this.verificationMessage = 'Subscription successfully activated! Redirecting to Usage...';
          this.toastService.success(
            'Your monthly AutoPay subscription is active! Welcome to LeadAI.',
            'Plan Activated'
          );

          // Redirect to Usage Hub as requested
          setTimeout(() => {
            this.isVerifyingPayment = false;
            this.router.navigate(['/client/usage']);
          }, 1500);
        },
        error: (err) => {
          this.isVerifyingPayment = false;
          this.toastService.error(
            err?.error?.detail || 'Signature verification failed. Please contact support.',
            'Verification Error'
          );
        },
      })
    );
  }

  /**
   * Handles checkout closure without payment completion.
   */
  private handleCheckoutDismiss(subRes: RazorpaySubscriptionResponse): void {
    this.billingService
      .recordPaymentFailure({
        subscription_id: subRes.subscription_id,
        error_code: 'CHECKOUT_DISMISSED',
        error_description: 'Checkout window closed before completing mandate authentication.',
      })
      .subscribe();

    this.toastService.warn(
      'Checkout closed. Your subscription has not been charged.',
      'Checkout Cancelled'
    );
  }

  /**
   * Handles gateway payment failure.
   */
  private handlePaymentFailure(subRes: RazorpaySubscriptionResponse, failRes: any): void {
    this.billingService
      .recordPaymentFailure({
        subscription_id: subRes.subscription_id,
        error_code: failRes?.error?.code || 'SUBSCRIPTION_AUTH_FAILED',
        error_description: failRes?.error?.description || 'Mandate authorization failed at bank.',
      })
      .subscribe();

    this.toastService.error(
      failRes?.error?.description || 'Payment mandate could not be authorized.',
      'Mandate Failed'
    );
  }

  /**
   * Navigates back to the Usage page.
   */
  public navigateToUsage(): void {
    this.router.navigate(['/client/usage']);
  }
}
