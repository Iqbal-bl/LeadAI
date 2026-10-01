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
 * PlansPricingComponent provides a modern, high-converting Plans & Pricing experience.
 *
 * Workflow:
 * 1. Top Pricing Plans Grid (Start, Growth [Best Choice dark card], Enterprise Custom Bundle)
 *    with Yearly (-15%) / Monthly billing toggle, driven by /billing/available-plans API.
 * 2. Upon selecting any plan, reveals the side-by-side configuration workspace:
 *    - Left: Social Media Support Add-ons (WhatsApp, Instagram, Facebook, LinkedIn) from API.
 *    - Right: Live Side-by-Side Billing Summary with back-calculated 18% GST tax breakdown
 *      (DB prices are inclusive of 18% GST).
 * 3. Razorpay AutoPay Mandate Checkout with verification overlay and redirect to /client/usage.
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

  /** Billing cycle toggle: 'monthly' or 'yearly' (15% discount on yearly) */
  public billingCycle: 'monthly' | 'yearly' = 'monthly';
  public readonly YEARLY_DISCOUNT_RATE: number = 0.15;
  public readonly GST_TAX_RATE: number = 18.0;

  /** Master standard plans loaded from backend API */
  public standardPlans: RechargePlanTemplate[] = [];
  public isLoadingPlans: boolean = true;

  /** Current active subscription status to prevent duplicate base plan purchases */
  public currentSummary: BillingSummary | null = null;
  public hasActivePlan: boolean = false;
  public isLoadingSummary: boolean = true;

  /** User role and workspace context */
  public isCompanyAdmin: boolean = true;
  public currentUserEmail: string = '';
  public companyName: string = '';

  /** Whether the user has selected a base plan to reveal Social Media Add-ons + Billing Summary */
  public isPlanSelected: boolean = false;

  /** Base Plan Selection Type: 'standard' or 'custom' */
  public selectedPlanType: 'standard' | 'custom' = 'standard';

  /** Selected standard plan template (if standard chosen) */
  public selectedStandardPlan: RechargePlanTemplate | null = null;

  /** Custom / Enterprise Plan Configuration */
  public customMinutes: number = 1500;
  public readonly MIN_CUSTOM_MINUTES: number = 100;
  public readonly MAX_CUSTOM_MINUTES: number = 6000;
  public customRatePerMinute: number = 4.0;

  /** Default visual styling metadata for social media channels */
  private readonly CHANNEL_STYLE_META: Record<
    string,
    { icon: string; color: string; defaultName: string; defaultDesc: string; defaultFeatures: string[] }
  > = {
    whatsapp: {
      icon: 'pi pi-whatsapp',
      color: '#22c55e',
      defaultName: 'WhatsApp Business API',
      defaultDesc:
        'Official Meta Cloud API integration for automated 24/7 lead chats and appointment scheduling.',
      defaultFeatures: [
        'Official Meta Cloud API webhook routing',
        '24/7 AI conversational auto-replies',
        'Instant qualification scorecard & handoff alerts',
        '30-day recurring synchronization',
      ],
    },
    instagram: {
      icon: 'pi pi-instagram',
      color: '#a855f7',
      defaultName: 'Instagram DM Automation',
      defaultDesc:
        'Engage high-intent prospects reaching out via Instagram direct messages and reel comments.',
      defaultFeatures: [
        'Direct message automatic AI response funnel',
        'Post and Story comment-to-DM triggers',
        'Lead scoring and sentiment analysis',
        '30-day recurring synchronization',
      ],
    },
    facebook: {
      icon: 'pi pi-facebook',
      color: '#3b82f6',
      defaultName: 'Facebook Messenger',
      defaultDesc:
        'Turn Facebook page visitors into qualified opportunities with zero response delay.',
      defaultFeatures: [
        'Business page inbox AI integration',
        'Post comment auto-replies to Messenger',
        'Multi-channel customer contact linking',
        '30-day recurring synchronization',
      ],
    },
    linkedin: {
      icon: 'pi pi-linkedin',
      color: '#0284c7',
      defaultName: 'LinkedIn Lead Automation',
      defaultDesc:
        'Automate connection messaging, B2B lead qualification, and CRM syncing on LinkedIn.',
      defaultFeatures: [
        'B2B profile qualification & matching',
        'Automated connection and InMail follow-ups',
        'Real-time CRM contact creation',
        '30-day recurring synchronization',
      ],
    },
    blog: {
      icon: 'pi pi-file-edit',
      color: '#f59e0b',
      defaultName: 'AI Blog & Content Automation',
      defaultDesc:
        'Automated SEO blog generation, Ghost/WordPress publishing, and high-ranking lead acquisition articles.',
      defaultFeatures: [
        'SEO-optimized long-form AI article generation',
        'One-click WordPress & Ghost auto-publishing',
        'Keyword intent scoring & organic lead capture',
        '30-day recurring synchronization',
      ],
    },
    voice_facilities: {
      icon: 'pi pi-phone',
      color: '#06b6d4',
      defaultName: 'Voice Call Facilities',
      defaultDesc:
        'Dedicated virtual phone numbers, inbound IVR auto-receptionist, and smart multi-agent call routing.',
      defaultFeatures: [
        'Dedicated business virtual DID phone line',
        'Inbound IVR auto-receptionist & smart menu',
        'Live call transfers & agent hunt groups',
        'High-fidelity audio & cloud call recordings',
      ],
    },
  };

  /** Omni-Channel Addon Options dynamically populated from API */
  public channelOptions: PricingChannelOption[] = [];

  /** Selected channels map */
  public selectedChannelsMap: Record<string, boolean> = {};

  /** Checkout & Verification States */
  public isCheckingOut: boolean = false;
  public isVerifyingPayment: boolean = false;
  public verificationMessage: string = '';

  ngOnInit(): void {
    this.initDefaultChannelOptions();
    this.initUserContext();
    this.loadCurrentSummary();
    this.loadPlans();
  }

  /**
   * Identifies logged-in user role, company name, and subscription state.
   */
  private initUserContext(): void {
    const user = this.authService.getCurrentUser();
    if (user) {
      this.isCompanyAdmin = this.authService.isCompanyAdmin();
      this.currentUserEmail = user.email || '';
      this.companyName = user.client_name || '';
      if (user.has_active_subscription !== undefined) {
        this.hasActivePlan = user.has_active_subscription;
      }
    }
    this.subscriptions.add(
      this.authService.getAccessMe().subscribe({
        next: (u) => {
          this.isCompanyAdmin = this.authService.isCompanyAdmin();
          this.currentUserEmail = u.email || '';
          this.companyName = u.client_name || '';
          if (u.has_active_subscription !== undefined) {
            this.hasActivePlan = u.has_active_subscription;
          }
        },
        error: () => {},
      })
    );
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }

  /**
   * Initializes fallback channel options before API response arrives.
   */
  private initDefaultChannelOptions(): void {
    const defaultPrices: Record<string, number> = {
      whatsapp: 2000,
      instagram: 2000,
      facebook: 2000,
      linkedin: 3000,
      blog: 2000,
      voice_facilities: 2500,
    };
    this.channelOptions = Object.keys(this.CHANNEL_STYLE_META).map((key) => {
      const meta = this.CHANNEL_STYLE_META[key];
      return {
        key,
        name: meta.defaultName,
        icon: meta.icon,
        color: meta.color,
        monthlyPrice: defaultPrices[key] || 2000,
        durationDays: 30,
        description: meta.defaultDesc,
        features: [...meta.defaultFeatures],
      };
    });
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
            const isNotExpired =
              !active.expires_at || new Date(active.expires_at).getTime() > Date.now();
            this.hasActivePlan = isNotExpired;
          } else {
            this.hasActivePlan = false;
          }
          if (this.hasActivePlan) {
            this.selectedChannelsMap = {};
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
   * Loads master plans and social media channel add-ons from backend API.
   */
  public loadPlans(): void {
    this.isLoadingPlans = true;
    this.subscriptions.add(
      this.billingService.getAvailablePlans().subscribe({
        next: (plans) => {
          // 1. Extract base voice plans
          const filtered = plans.filter(
            (p) =>
              p.plan_type === 'standard' &&
              p.plan_category !== 'client_self_bundle' &&
              p.plan_category !== 'channel_addon' &&
              p.plan_category !== 'voice_topup' &&
              !p.name.toLowerCase().includes('channel add-on') &&
              !p.name.toLowerCase().includes('booster') &&
              !p.name.toLowerCase().includes('yearly') &&
              p.validity_days <= 90
          );

          // Deduplicate so each distinct minute quota appears once
          const seenMinutes = new Set<number>();
          const distinctPlans: RechargePlanTemplate[] = [];
          for (const plan of filtered) {
            if (!seenMinutes.has(plan.included_minutes)) {
              seenMinutes.add(plan.included_minutes);
              distinctPlans.push(plan);
            }
          }

          // Sort by price ascending: Start (500 Mins), Growth (1000 Mins)
          distinctPlans.sort((a, b) => a.price - b.price);
          this.standardPlans = distinctPlans.slice(0, 2);

          if (this.standardPlans.length > 0 && this.standardPlans[0].rate_per_minute > 0) {
            this.customRatePerMinute = this.standardPlans[0].rate_per_minute;
          }

          // 2. Dynamically build add-on options from API templates with strict key deduplication
          const channelTemplates = plans.filter(
            (p) => p.plan_category === 'channel_addon' && p.feature_key
          );
          const seenChannelKeys = new Set<string>();
          const apiChannels: PricingChannelOption[] = [];

          for (const ct of channelTemplates) {
            let key = (ct.feature_key || '').toLowerCase().trim();
            if (key === 'voice_call_facilities' || key === 'voice_call' || key === 'voice-facilities') {
              key = 'voice_facilities';
            }
            if (!key || seenChannelKeys.has(key)) {
              continue; // Prevent duplicates: each add-on option appears once
            }
            seenChannelKeys.add(key);

            const meta = this.CHANNEL_STYLE_META[key] || {
              icon: 'pi pi-sparkles',
              color: '#6366f1',
              defaultName: ct.name,
              defaultDesc: ct.description || 'Automated 24/7 AI integration.',
              defaultFeatures: [
                '24/7 AI conversational auto-replies',
                'Instant lead qualification & CRM sync',
                '30-day recurring synchronization',
              ],
            };
            apiChannels.push({
              key,
              name: ct.name || meta.defaultName,
              icon: meta.icon,
              color: meta.color,
              monthlyPrice: ct.price,
              durationDays: ct.validity_days || 30,
              description: ct.description || meta.defaultDesc,
              features:
                ct.features && ct.features.length > 0
                  ? ct.features
                  : [...meta.defaultFeatures],
            });
          }

          // Ensure all required add-ons (WhatsApp, Instagram, Facebook, LinkedIn, Blog, Voice Facilities) are present
          const requiredKeys = ['whatsapp', 'instagram', 'facebook', 'linkedin', 'blog', 'voice_facilities'];
          const defaultPrices: Record<string, number> = {
            whatsapp: 2000,
            instagram: 2000,
            facebook: 2000,
            linkedin: 3000,
            blog: 2000,
            voice_facilities: 2500,
          };
          for (const reqKey of requiredKeys) {
            if (!seenChannelKeys.has(reqKey) && this.CHANNEL_STYLE_META[reqKey]) {
              const meta = this.CHANNEL_STYLE_META[reqKey];
              seenChannelKeys.add(reqKey);
              apiChannels.push({
                key: reqKey,
                name: meta.defaultName,
                icon: meta.icon,
                color: meta.color,
                monthlyPrice: defaultPrices[reqKey] || 2000,
                durationDays: 30,
                description: meta.defaultDesc,
                features: [...meta.defaultFeatures],
              });
            }
          }

          // Canonical order: WhatsApp, Instagram, Facebook, LinkedIn, Blog, Voice Facilities
          const order = ['whatsapp', 'instagram', 'facebook', 'linkedin', 'blog', 'voice_facilities'];
          apiChannels.sort((a, b) => {
            const idxA = order.indexOf(a.key);
            const idxB = order.indexOf(b.key);
            return (idxA === -1 ? 99 : idxA) - (idxB === -1 ? 99 : idxB);
          });
          this.channelOptions = apiChannels;

          // Pre-highlight the popular Growth plan (or first plan)
          if (this.standardPlans.length > 1) {
            this.selectedStandardPlan = this.standardPlans[1];
          } else if (this.standardPlans.length > 0) {
            this.selectedStandardPlan = this.standardPlans[0];
          }
          this.selectedPlanType = 'standard';
          this.isLoadingPlans = false;
        },
        error: (err) => {
          this.isLoadingPlans = false;
          this.toastService.error(
            err?.error?.detail || 'Failed to load plans & pricing from server.',
            'Plans Error'
          );
        },
      })
    );
  }

  /**
   * Switches between Monthly and Yearly billing cycles.
   */
  public setBillingCycle(cycle: 'monthly' | 'yearly'): void {
    this.billingCycle = cycle;
  }

  /**
   * Returns clean tier heading matching the screenshot ("Start", "Growth").
   */
  public getPlanTierTitle(plan: RechargePlanTemplate, index: number): string {
    if (plan.tier_label) {
      return plan.tier_label;
    }
    return index === 0 ? 'Start' : 'Growth';
  }

  /**
   * Returns whether this card should use the highlighted dark "best choice" theme.
   */
  public isPopularPlan(plan: RechargePlanTemplate, index: number): boolean {
    if (plan.is_popular === true) {
      return true;
    }
    const name = (plan.name || '').toLowerCase();
    const tier = (plan.tier_label || '').toLowerCase();
    if (tier === 'growth' || name.includes('growth')) {
      return true;
    }
    return index === 1;
  }

  /**
   * Returns feature list for a standard plan from API (or fallback).
   */
  public getPlanFeatures(plan: RechargePlanTemplate, index: number): string[] {
    const mins = this.getPlanCycleMinutes(plan);
    const cycleUnit = this.billingCycle === 'yearly' ? 'year' : 'month';
    if (plan.features && plan.features.length > 0) {
      return plan.features.map((f: string) => {
        if (f.toLowerCase().includes('call minutes')) {
          return `${mins.toLocaleString('en-IN')} AI voice call minutes / ${cycleUnit}`;
        }
        return f;
      });
    }
    if (index === 0) {
      return [
        `Up to ${mins.toLocaleString('en-IN')} AI voice call minutes / ${cycleUnit}`,
        'Conversational Voice AI Agent',
        'Call recordings & full transcripts',
        'Automated lead scoring & CRM sync',
        'Support 24/7',
      ];
    }
    return [
      'Everything in Start',
      `${mins.toLocaleString('en-IN')} AI voice call minutes / ${cycleUnit}`,
      'Advanced lead sentiment & intent insights',
      'Real-time qualification & webhook routing',
      'Multi-campaign outbound calling',
      'Priority customer support 24/7',
    ];
  }

  /**
   * Returns effective monthly price for display on the pricing card.
   * When 'yearly' is active, applies the 15% yearly discount.
   */
  public getCardDisplayMonthlyPrice(monthlyPrice: number): number {
    if (this.billingCycle === 'yearly') {
      return Math.round(monthlyPrice * (1 - this.YEARLY_DISCOUNT_RATE));
    }
    return monthlyPrice;
  }

  /**
   * Returns total minutes allocated for the selected cycle on a standard plan.
   */
  public getPlanCycleMinutes(plan: RechargePlanTemplate): number {
    return this.billingCycle === 'yearly'
      ? plan.included_minutes * 12
      : plan.included_minutes;
  }

  /**
   * Selects a standard pre-configured master plan and reveals the Social Media Add-ons + Billing section.
   */
  public selectStandardPlan(plan: RechargePlanTemplate, scrollToAddons: boolean = true): void {
    if (this.hasActivePlan) {
      this.toastService.warn(
        'You already have an active subscription. Additional base plans cannot be purchased until renewal.',
        'Active Plan Running'
      );
      return;
    }
    this.selectedPlanType = 'standard';
    this.selectedStandardPlan = plan;
    this.isPlanSelected = true;

    if (scrollToAddons) {
      this.scrollToConfigSection();
    }
  }

  /**
   * Selects the Enterprise / Custom plan card and reveals the Social Media Add-ons + Billing section.
   */
  public selectCustomPlan(scrollToAddons: boolean = true): void {
    if (this.hasActivePlan) {
      this.toastService.warn(
        'You already have an active subscription. Additional base plans cannot be purchased until renewal.',
        'Active Plan Running'
      );
      return;
    }
    this.selectedPlanType = 'custom';
    this.isPlanSelected = true;

    if (scrollToAddons) {
      this.scrollToConfigSection();
    }
  }

  private scrollToConfigSection(): void {
    setTimeout(() => {
      const el = document.getElementById('addons-billing-section');
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }, 80);
  }

  /**
   * Clamps and updates custom minute allocation.
   */
  public onCustomMinutesChange(minutes: number): void {
    if (this.hasActivePlan) return;
    if (isNaN(minutes)) minutes = this.MIN_CUSTOM_MINUTES;
    const clamped = Math.min(
      this.MAX_CUSTOM_MINUTES,
      Math.max(this.MIN_CUSTOM_MINUTES, minutes)
    );
    this.customMinutes = Math.round(clamped / 50) * 50;
    this.selectedPlanType = 'custom';
    this.isPlanSelected = true;
  }

  /**
   * Toggles a social media support add-on on or off.
   */
  public toggleChannel(channelKey: string): void {
    if (this.hasActivePlan) {
      this.toastService.warn(
        'Omni-channel add-ons for an active subscription can be added from Usage & Top-Ups.',
        'Active Plan Running'
      );
      return;
    }
    this.selectedChannelsMap[channelKey] = !this.selectedChannelsMap[channelKey];
  }

  /**
   * Selects or deselects all social media channels at once.
   */
  public toggleAllChannels(): void {
    if (this.hasActivePlan) return;
    const allSelected = this.areAllChannelsSelected();
    for (const ch of this.channelOptions) {
      this.selectedChannelsMap[ch.key] = !allSelected;
    }
  }

  public areAllChannelsSelected(): boolean {
    return (
      this.channelOptions.length > 0 &&
      this.channelOptions.every((ch) => !!this.selectedChannelsMap[ch.key])
    );
  }

  /**
   * Returns list of selected social media channel options.
   */
  public getSelectedChannels(): PricingChannelOption[] {
    return this.channelOptions.filter((ch) => this.selectedChannelsMap[ch.key]);
  }

  /**
   * Returns undiscounted monthly base plan price (inclusive of GST).
   */
  public getBasePlanMonthlyPrice(): number {
    if (this.selectedPlanType === 'custom') {
      return this.customMinutes * this.customRatePerMinute;
    }
    return this.selectedStandardPlan?.price || 0;
  }

  /**
   * Returns billing-cycle adjusted base plan price (before yearly discount if shown separately, or gross cycle price).
   */
  public getBasePlanGrossCyclePrice(): number {
    const monthly = this.getBasePlanMonthlyPrice();
    return this.billingCycle === 'yearly' ? monthly * 12 : monthly;
  }

  /**
   * Returns effective cycle price for a single channel add-on.
   */
  public getChannelCyclePrice(ch: PricingChannelOption): number {
    if (this.billingCycle === 'yearly') {
      return Math.round(ch.monthlyPrice * 12 * (1 - this.YEARLY_DISCOUNT_RATE));
    }
    return ch.monthlyPrice;
  }

  /**
   * Returns gross cycle price for all selected channels (before yearly discount).
   */
  public getChannelsGrossCycleTotal(): number {
    const monthlySum = this.getSelectedChannels().reduce(
      (acc, ch) => acc + ch.monthlyPrice,
      0
    );
    return this.billingCycle === 'yearly' ? monthlySum * 12 : monthlySum;
  }

  /**
   * Returns combined gross amount before yearly discount.
   */
  public getGrossCycleSubtotalInclTax(): number {
    return this.getBasePlanGrossCyclePrice() + this.getChannelsGrossCycleTotal();
  }

  /**
   * Returns 15% yearly discount savings amount (0 when monthly).
   */
  public getYearlyDiscountAmount(): number {
    if (this.billingCycle !== 'yearly') return 0;
    return Math.round(this.getGrossCycleSubtotalInclTax() * this.YEARLY_DISCOUNT_RATE);
  }

  /**
   * Grand Total Payable (Inclusive of 18% GST).
   * Matches the exact DB price charged via Razorpay.
   */
  public getTotalPayableInclusiveTax(): number {
    const gross = this.getGrossCycleSubtotalInclTax();
    if (this.billingCycle === 'yearly') {
      return Math.round(gross * (1 - this.YEARLY_DISCOUNT_RATE));
    }
    return Math.round(gross);
  }

  /**
   * Back-calculates Net Taxable Amount (Excl. 18% GST) from the GST-inclusive total.
   * Formula: Total / 1.18
   */
  public getTaxableSubtotalExclTax(): number {
    const total = this.getTotalPayableInclusiveTax();
    return Math.round((total / (1 + this.GST_TAX_RATE / 100)) * 100) / 100;
  }

  /**
   * Back-calculates total 18% GST included in the total payable amount.
   */
  public getIncludedGstAmount(): number {
    const total = this.getTotalPayableInclusiveTax();
    const taxable = this.getTaxableSubtotalExclTax();
    return Math.round((total - taxable) * 100) / 100;
  }

  /**
   * Returns minutes included in the selected base plan for the active billing cycle.
   */
  public getBasePlanMinutes(): number {
    const monthlyMins =
      this.selectedPlanType === 'custom'
        ? this.customMinutes
        : this.selectedStandardPlan?.included_minutes || 0;
    return this.billingCycle === 'yearly' ? monthlyMins * 12 : monthlyMins;
  }

  /**
   * Returns monthly minutes (used by backend custom bundle payload).
   */
  public getMonthlyVoiceMinutes(): number {
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
      return `Enterprise Custom (${this.customMinutes.toLocaleString('en-IN')} Mins/mo)`;
    }
    if (!this.selectedStandardPlan) return 'Select a Plan';
    const tier = this.selectedStandardPlan.tier_label || (this.selectedStandardPlan.included_minutes <= 500 ? 'Start' : 'Growth');
    return `${tier} — ${this.selectedStandardPlan.name}`;
  }

  /**
   * Initiates recurring AutoPay subscription checkout via Razorpay.
   */
  public initiateAutoPayCheckout(): void {
    if (this.hasActivePlan) {
      this.toastService.warn(
        'You already have an active subscription. Additional plans cannot be purchased while your current plan is active.',
        'Active Plan Running'
      );
      return;
    }

    const selectedChannels = this.getSelectedChannels().map((ch) => ch.key);
    this.isCheckingOut = true;

    // Case 1: Custom Plan OR Yearly Cycle OR Standard Plan with modular social media add-ons
    if (
      this.selectedPlanType === 'custom' ||
      this.billingCycle === 'yearly' ||
      selectedChannels.length > 0
    ) {
      const payload: CustomBundlePayload = {
        base_plan_template_id:
          this.selectedPlanType === 'standard' ? this.selectedStandardPlan?.id || null : null,
        include_voice: true,
        voice_minutes: this.getMonthlyVoiceMinutes(),
        channels: selectedChannels,
        billing_cycle: this.billingCycle,
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
              err?.error?.detail || 'Failed to initiate subscription bundle.',
              'Checkout Error'
            );
          },
        })
      );
    } else {
      // Case 2: Standard Monthly Master Plan with no extra social channels
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
   */
  private launchRazorpaySubscriptionModal(subRes: RazorpaySubscriptionResponse): void {
    if (typeof (window as any).Razorpay === 'undefined') {
      this.toastService.error(
        'Razorpay checkout SDK not loaded. Please verify your connection.',
        'Gateway Error'
      );
      return;
    }

    const cycleLabel = this.billingCycle === 'yearly' ? 'Yearly' : '30-Day';
    const options = {
      key: subRes.key_id,
      subscription_id: subRes.subscription_id,
      name: 'LeadAI Automation',
      description: `${cycleLabel} AutoPay Plan: ${subRes.plan_name}`,
      handler: (response: any) => {
        this.verifySubscriptionPayment(subRes, response);
      },
      modal: {
        ondismiss: () => {
          this.handleCheckoutDismiss(subRes);
        },
      },
      theme: {
        color: '#0f172a',
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
   */
  private verifySubscriptionPayment(subRes: RazorpaySubscriptionResponse, paymentRes: any): void {
    this.isVerifyingPayment = true;
    this.verificationMessage = 'Verifying AutoPay mandate and activating your plan & channels...';

    const verifyPayload = {
      razorpay_subscription_id: paymentRes.razorpay_subscription_id || subRes.subscription_id,
      razorpay_payment_id: paymentRes.razorpay_payment_id,
      razorpay_signature: paymentRes.razorpay_signature,
      plan_template_id: subRes.plan_id,
    };

    this.subscriptions.add(
      this.billingService.verifyRazorpaySubscription(verifyPayload).subscribe({
        next: () => {
          this.verificationMessage = 'Subscription successfully activated! Unlocking workspace...';
          this.toastService.success(
            'Your AutoPay subscription is now active! Welcome to LeadAI.',
            'Plan Activated'
          );
          this.hasActivePlan = true;

          // Re-fetch user session so has_active_subscription becomes true across the entire app
          this.authService.getAccessMe().subscribe({
            next: () => {
              setTimeout(() => {
                this.isVerifyingPayment = false;
                this.router.navigate(['/client/dashboard']);
              }, 1200);
            },
            error: () => {
              setTimeout(() => {
                this.isVerifyingPayment = false;
                this.router.navigate(['/client/dashboard']);
              }, 1200);
            },
          });
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
   * Navigates back to the workspace if subscription is active.
   */
  public navigateToUsage(): void {
    if (!this.hasActivePlan) {
      this.toastService.warn(
        'An active subscription plan is required before accessing the application.',
        'Subscription Required'
      );
      return;
    }
    this.router.navigate(['/client/dashboard']);
  }

  /**
   * Logs out the user.
   */
  public logout(): void {
    this.authService.logout();
  }
}
// End of PlansPricingComponent


