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
  PricingChannelOption,
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

/**
 * PlansPricingComponent provides a clean, modern Plans & Pricing experience.
 *
 * Workflow:
 * 1. Top Pricing Plans Grid (Start, Growth, Enterprise Custom Bundle)
 *    driven dynamically by /billing/available-plans API.
 * 2. Reveals Social Media Support Add-ons (WhatsApp, Instagram, Facebook, LinkedIn, etc.)
 *    and live side-by-side order summary.
 * 3. Redirects to Identity Server with the selected plan, voice minutes, and social channels.
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
    {
      icon: string;
      color: string;
      defaultName: string;
      defaultDesc: string;
      defaultFeatures: string[];
    }
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

  /** Checkout Redirect State */
  public isCheckingOut: boolean = false;

  ngOnInit(): void {
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
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }

  /**
   * Loads current billing summary to check if company already has an active subscription.
   */
  public loadCurrentSummary(): void {
    const hasToken =
      !!this.authService.getValue('accessToken') ||
      !!this.authService.getStaffToken();
    if (!hasToken) {
      this.isLoadingSummary = false;
      this.hasActivePlan = false;
      return;
    }

    this.isLoadingSummary = true;
    this.subscriptions.add(
      this.billingService.getCurrentPlan().subscribe({
        next: (summary) => {
          this.currentSummary = summary;
          const active = summary?.active_recharge;
          if (active && active.status === 'active') {
            const isNotExpired =
              !active.expires_at ||
              new Date(active.expires_at).getTime() > Date.now();
            this.hasActivePlan = isNotExpired;
          } else {
            this.hasActivePlan = false;
          }
          if (this.hasActivePlan) {
            this.selectedChannelsMap = {};
            this.autoSelectUpgradePlan();
          }
          this.isLoadingSummary = false;
        },
        error: () => {
          this.isLoadingSummary = false;
        },
      }),
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
          const filtered = (plans || []).filter(
            (p) =>
              p.plan_type === 'standard' &&
              p.plan_category !== 'client_self_bundle' &&
              p.plan_category !== 'channel_addon' &&
              p.plan_category !== 'voice_topup' &&
              !p.name.toLowerCase().includes('channel add-on') &&
              !p.name.toLowerCase().includes('booster') &&
              !p.name.toLowerCase().includes('yearly') &&
              p.validity_days <= 90,
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
          this.standardPlans = distinctPlans.slice(0, 5);

          if (
            this.standardPlans.length > 0 &&
            this.standardPlans[0].rate_per_minute > 0
          ) {
            this.customRatePerMinute = this.standardPlans[0].rate_per_minute;
          }

          // 2. Dynamically build add-on options from API templates with strict key deduplication
          const channelTemplates = (plans || []).filter(
            (p) => p.plan_category === 'channel_addon' && p.feature_key,
          );
          const seenChannelKeys = new Set<string>();
          const apiChannels: PricingChannelOption[] = [];

          for (const ct of channelTemplates) {
            let key = (ct.feature_key || '').toLowerCase().trim();
            if (
              key === 'voice_call_facilities' ||
              key === 'voice_call' ||
              key === 'voice-facilities'
            ) {
              key = 'voice_facilities';
            }
            if (!key || seenChannelKeys.has(key)) {
              continue; // Prevent duplicates: each add-on option appears once
            }
            seenChannelKeys.add(key);

            const meta = this.CHANNEL_STYLE_META[key];
            apiChannels.push({
              key,
              name: ct.name || meta?.defaultName || key,
              icon: meta?.icon || 'pi pi-sparkles',
              color: meta?.color || '#6366f1',
              monthlyPrice: ct.price,
              durationDays: ct.validity_days || 30,
              description: ct.description || meta?.defaultDesc || 'Automated 24/7 AI integration.',
              features:
                ct.features && ct.features.length > 0
                  ? ct.features
                  : (meta?.defaultFeatures || [
                      '24/7 AI conversational auto-replies',
                      'Instant lead qualification & CRM sync',
                      '30-day recurring synchronization',
                    ]),
            });
          }

          // Canonical order: WhatsApp, Instagram, Facebook, LinkedIn, Blog, Voice Facilities
          const order = [
            'whatsapp',
            'instagram',
            'facebook',
            'linkedin',
            'blog',
            'voice_facilities',
          ];
          apiChannels.sort((a, b) => {
            const idxA = order.indexOf(a.key);
            const idxB = order.indexOf(b.key);
            return (idxA === -1 ? 99 : idxA) - (idxB === -1 ? 99 : idxB);
          });
          this.channelOptions = apiChannels;

          // Pre-highlight the popular Growth plan (or next upgrade tier if plan active)
          if (this.standardPlans.length > 0) {
            this.autoSelectUpgradePlan();
          } else {
            this.isPlanSelected = false;
            this.selectedStandardPlan = null;
          }
          this.isLoadingPlans = false;
        },
        error: () => {
          this.isLoadingPlans = false;
          this.standardPlans = [];
          this.channelOptions = [];
          this.isPlanSelected = false;
          this.selectedStandardPlan = null;
          this.toastService.error(
            'Failed to load pricing plans. Please refresh or try again later.',
            'Plans Unavailable',
          );
        },
      }),
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
   * Checks if the given plan is the client's currently active plan.
   */
  public isCurrentPlan(plan: RechargePlanTemplate): boolean {
    const active = this.currentSummary?.active_recharge;
    if (!active) return false;
    return (
      active.plan_template_id === plan.id ||
      (active.plan_name_snapshot || '').trim().toLowerCase() ===
        (plan.name || '').trim().toLowerCase()
    );
  }

  /**
   * Checks if the given standard plan is a higher tier than current active plan.
   */
  public isHigherTier(plan: RechargePlanTemplate): boolean {
    if (!this.hasActivePlan) return true;
    if (this.isCurrentPlan(plan)) return false;
    const active = this.currentSummary?.active_recharge;
    if (!active) return true;
    const activePaid = active.price_paid || 0;
    const activeMinutes = active.purchased_minutes || 0;
    return plan.price > activePaid || plan.included_minutes > activeMinutes;
  }

  /**
   * Checks if custom/enterprise plan is higher tier than current active plan.
   */
  public isHigherTierCustom(): boolean {
    if (!this.hasActivePlan) return true;
    const active = this.currentSummary?.active_recharge;
    if (!active) return true;
    const activeMinutes = active.purchased_minutes || 0;
    const activePaid = active.price_paid || 0;
    const customPrice = this.customMinutes * this.customRatePerMinute;
    return this.customMinutes > activeMinutes || customPrice > activePaid;
  }

  /**
   * Checks if the currently selected configuration is an eligible upgrade.
   */
  public canUpgradeCurrentSelection(): boolean {
    if (!this.hasActivePlan) return true;
    if (this.selectedPlanType === 'standard') {
      return (
        !!this.selectedStandardPlan &&
        this.isHigherTier(this.selectedStandardPlan)
      );
    }
    if (this.selectedPlanType === 'custom') {
      return this.isHigherTierCustom();
    }
    return false;
  }

  /**
   * Auto-selects an upgrade plan or popular default.
   */
  public autoSelectUpgradePlan(): void {
    if (!this.standardPlans || this.standardPlans.length === 0) return;
    if (this.hasActivePlan) {
      const upgradePlan = this.standardPlans.find((p) => this.isHigherTier(p));
      if (upgradePlan) {
        this.selectedStandardPlan = upgradePlan;
        this.selectedPlanType = 'standard';
        this.isPlanSelected = true;
        return;
      }
    }
    if (!this.selectedStandardPlan) {
      if (this.standardPlans.length > 1) {
        this.selectedStandardPlan = this.standardPlans[1];
      } else if (this.standardPlans.length > 0) {
        this.selectedStandardPlan = this.standardPlans[0];
      }
      this.selectedPlanType = 'standard';
    }
  }

  /**
   * Selects a standard pre-configured master plan and reveals the Social Media Add-ons + Billing section.
   */
  public selectStandardPlan(
    plan: RechargePlanTemplate,
    scrollToAddons: boolean = true,
  ): void {
    if (this.hasActivePlan) {
      if (this.isCurrentPlan(plan)) {
        this.toastService.info(
          'This is your currently active subscription plan.',
          'Current Plan',
        );
        return;
      }
      if (!this.isHigherTier(plan)) {
        this.toastService.warn(
          'You already have an active subscription with equal or higher quota.',
          'Active Plan Running',
        );
        return;
      }
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
    if (this.hasActivePlan && !this.isHigherTierCustom()) {
      this.toastService.warn(
        'Enterprise quota must exceed your current plan minutes to upgrade.',
        'Custom Quota',
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
      Math.max(this.MIN_CUSTOM_MINUTES, minutes),
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
        'Active Plan Running',
      );
      return;
    }
    this.selectedChannelsMap[channelKey] =
      !this.selectedChannelsMap[channelKey];
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
      0,
    );
    return this.billingCycle === 'yearly' ? monthlySum * 12 : monthlySum;
  }

  /**
   * Returns combined gross amount before yearly discount.
   */
  public getGrossCycleSubtotalInclTax(): number {
    return (
      this.getBasePlanGrossCyclePrice() + this.getChannelsGrossCycleTotal()
    );
  }

  /**
   * Returns 15% yearly discount savings amount (0 when monthly).
   */
  public getYearlyDiscountAmount(): number {
    if (this.billingCycle !== 'yearly') return 0;
    return Math.round(
      this.getGrossCycleSubtotalInclTax() * this.YEARLY_DISCOUNT_RATE,
    );
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
    const tier =
      this.selectedStandardPlan.tier_label ||
      (this.selectedStandardPlan.included_minutes <= 500 ? 'Start' : 'Growth');
    return `${tier} — ${this.selectedStandardPlan.name}`;
  }

  /**
   * Generates comma-separated permissions string for Identity Server user creation
   * based on selected plan (voice) and selected social channels.
   * Format example: "social.instagram,social.facebook,voice.inbound"
   */
  public getSelectedPermissions(): string {
    const permissions: string[] = [];

    const channelPermissionMap: Record<string, string> = {
      instagram: 'social.instagram',
      facebook: 'social.facebook',
      whatsapp: 'social.whatsapp',
      linkedin: 'social.linkedin',
      blog: 'social.blog',
      voice_facilities: 'voice.inbound',
    };

    const selectedChannels = this.getSelectedChannels();
    for (const ch of selectedChannels) {
      const perm =
        channelPermissionMap[ch.key] ||
        (ch.key.startsWith('social.') ? ch.key : `social.${ch.key}`);
      if (perm && !permissions.includes(perm)) {
        permissions.push(perm);
      }
    }

    // Include voice.inbound permission for base plans (Start, Growth, Enterprise)
    if (
      this.selectedPlanType === 'standard' ||
      this.selectedPlanType === 'custom'
    ) {
      if (!permissions.includes('voice.inbound')) {
        permissions.push('voice.inbound');
      }
    }

    return permissions.join(',');
  }

  /**
   * Returns selected plan identifier or name for query parameters.
   */
  public getSelectedPlanKey(): string {
    if (this.selectedPlanType === 'custom') {
      return 'custom';
    }
    if (this.selectedStandardPlan) {
      if (this.selectedStandardPlan.tier_label) {
        return this.selectedStandardPlan.tier_label.toLowerCase();
      }
      return this.selectedStandardPlan.included_minutes <= 500 ? 'start' : 'growth';
    }
    return 'growth';
  }

  /**
   * Returns comma-separated selected social media / channel keys.
   */
  public getSelectedSocialMediaKeys(): string {
    return this.getSelectedChannels()
      .map((ch) => ch.key)
      .join(',');
  }

  /**
   * Constructs the full Identity Server URL for account creation with selected permissions,
   * selected voice plan, and social media package.
   * e.g. https://localhost:7085/Account/Create?source=leadai&returnUrl=...&plan=growth&social_media=whatsapp,instagram&permissions=social.instagram,social.facebook,voice.inbound
   */
  /**
   * Constructs the full Identity Server URL for account creation with selected permissions,
   * selected voice plan, and social media package via AuthService endpoint builder.
   */
  public buildIdentityServerRegisterUrl(): string {
    return this.authService.buildRegisterUrl({
      source: 'leadai',
      permissions: this.getSelectedPermissions(),
      plan: this.getSelectedPlanKey(),
      planName: this.getBasePlanName(),
      selectedPlan: this.getSelectedPlanKey(),
      socialMedia: this.getSelectedSocialMediaKeys(),
      channels: this.getSelectedSocialMediaKeys(),
      billingCycle: this.billingCycle,
      cycle: this.billingCycle,
      minutes: this.getBasePlanMinutes(),
      voiceMinutes: this.getMonthlyVoiceMinutes(),
      planId: this.selectedStandardPlan?.id ? String(this.selectedStandardPlan.id) : undefined,
      planPrice: this.getBasePlanMonthlyPrice(),
      totalAmount: this.getTotalPayableInclusiveTax(),
    });
  }

  /**
   * Redirects the user to the Identity Server to create an account with selected plan permissions.
   */
  public redirectToIdentityServer(): void {
    // Persist current dynamic selection to localStorage so it is restored when returning to checkout
    try {
      const planIdentifier =
        this.selectedPlanType === 'custom'
          ? 'custom'
          : (this.selectedStandardPlan?.tier_label ||
             this.selectedStandardPlan?.name ||
             String(this.selectedStandardPlan?.id) ||
             'growth');

      const checkoutSelection = {
        billingCycle: this.billingCycle,
        selectedPlanType: this.selectedPlanType,
        selectedPlanIdentifier: planIdentifier,
        selectedPlanId: this.selectedStandardPlan?.id,
        customMinutes: this.customMinutes,
        selectedChannelsMap: { ...this.selectedChannelsMap },
      };
      localStorage.setItem('leadai_checkout_selection', JSON.stringify(checkoutSelection));
    } catch {
      // ignore
    }

    this.authService.redirectToRegister({
      source: 'leadai',
      permissions: this.getSelectedPermissions(),
      plan: this.getSelectedPlanKey(),
      planName: this.getBasePlanName(),
      selectedPlan: this.getSelectedPlanKey(),
      socialMedia: this.getSelectedSocialMediaKeys(),
      channels: this.getSelectedSocialMediaKeys(),
      billingCycle: this.billingCycle,
      cycle: this.billingCycle,
      minutes: this.getBasePlanMinutes(),
      voiceMinutes: this.getMonthlyVoiceMinutes(),
      planId: this.selectedStandardPlan?.id ? String(this.selectedStandardPlan.id) : undefined,
      planPrice: this.getBasePlanMonthlyPrice(),
      totalAmount: this.getTotalPayableInclusiveTax(),
    });
  }

  /**
   * Initiates redirect to Identity Server to create user and register with selected permissions.
   */
  public initiateAutoPayCheckout(): void {
    if (this.hasActivePlan && !this.canUpgradeCurrentSelection()) {
      this.toastService.warn(
        'You already have an active subscription with equal or higher quota.',
        'Active Plan Running',
      );
      return;
    }

    this.isCheckingOut = true;
    this.redirectToIdentityServer();
  }

  /**
   * Redirects unauthenticated visitor to OIDC login flow.
   */
  public signIn(): void {
    this.authService.initiateOidcLogin();
  }

  /**
   * Navigates back to the workspace if subscription is active.
   */
  public navigateToUsage(): void {
    if (!this.hasActivePlan) {
      this.toastService.warn(
        'An active subscription plan is required before accessing the application.',
        'Subscription Required',
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
