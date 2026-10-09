import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { Subscription } from 'rxjs';

import { BillingService } from '../../services/billing.service';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../shared/services/toast.service';
import { OnboardingService } from '../../services/onboarding.service';

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
import { TooltipModule } from 'primeng/tooltip';
import { DialogModule } from 'primeng/dialog';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { DividerModule } from 'primeng/divider';

export interface ChannelItem {
  key: string;
  name: string;
  category: string;
  icon: string;
  color: string;
  monthlyPrice: number;
  description: string;
  features: string[];
}

@Component({
  selector: 'app-billing-checkout',
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
    TooltipModule,
    DialogModule,
    ProgressSpinnerModule,
    DividerModule,
  ],
  templateUrl: './billing-checkout.component.html',
  styleUrl: './billing-checkout.component.scss',
})
export class BillingCheckoutComponent implements OnInit, OnDestroy {
  private billingService = inject(BillingService);
  private authService = inject(AuthService);
  private toastService = inject(ToastService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);
  private onboardingService = inject(OnboardingService);

  private subscriptions = new Subscription();

  public readonly YEARLY_DISCOUNT_RATE = 0.15;
  public readonly GST_RATE = 0.18;
  public readonly STORAGE_CHECKOUT_KEY = 'leadai_checkout_selection';

  // State
  public billingCycle: 'monthly' = 'monthly';
  public selectedPlanType: 'standard' | 'custom' = 'standard';
  public selectedStandardPlan: RechargePlanTemplate | null = null;
  public savedPlanIdentifier: string | null = null;
  public showTaxBreakdown = false;

  // Custom Minutes
  public customMinutes: number = 1500;
  public readonly MIN_CUSTOM_MINUTES = 100;
  public readonly MAX_CUSTOM_MINUTES = 6000;
  public customRatePerMinute = 4.0;

  // Master Plans from API
  public standardPlans: RechargePlanTemplate[] = [];
  public isLoadingPlans = true;

  // Fallback plans if API is slow
  private readonly FALLBACK_STANDARD_PLANS: RechargePlanTemplate[] = [
    {
      id: 'plan_start',
      name: 'Start',
      plan_type: 'standard',
      plan_category: 'standard_tier',
      included_minutes: 500,
      validity_days: 30,
      price: 2000,
      rate_per_minute: 4.0,
      tier_label: 'Start',
      description: 'Ideal for startups & small local businesses handling modest call volumes.',
      features: ['500 monthly call minutes included', 'AI voice dialler', 'Lead capture & CRM sync'],
      is_popular: false,
    } as any,
    {
      id: 'plan_growth',
      name: 'Growth',
      plan_type: 'standard',
      plan_category: 'standard_tier',
      included_minutes: 1000,
      validity_days: 30,
      price: 3500,
      rate_per_minute: 3.5,
      tier_label: 'Growth',
      description: 'High-converting capacity for scaling companies and busy outbound sales pipelines.',
      features: ['1,000 monthly call minutes included', 'Priority AI voice routing', 'Real-time scorecard'],
      is_popular: true,
    } as any,
  ];

  // Selected Channels map
  public selectedChannelsMap: Record<string, boolean> = {
    whatsapp: true,
    instagram: true,
    facebook: false,
    linkedin: false,
    blog: false,
    voice_facilities: false,
  };

  private readonly CHANNEL_STYLE_META: Record<string, { icon: string; color: string; category: string }> = {
    whatsapp: { icon: 'pi pi-whatsapp', color: '#25D366', category: 'Messaging' },
    instagram: { icon: 'pi pi-instagram', color: '#E1306C', category: 'Social DM' },
    facebook: { icon: 'pi pi-facebook', color: '#1877F2', category: 'Social DM' },
    linkedin: { icon: 'pi pi-linkedin', color: '#0A66C2', category: 'B2B Outreach' },
    blog: { icon: 'pi pi-file-edit', color: '#8B5CF6', category: 'Content Marketing' },
    voice_facilities: { icon: 'pi pi-phone', color: '#0EA5E9', category: 'Telephony' },
  };

  // Default Channels Catalog
  public readonly DEFAULT_CHANNELS: ChannelItem[] = [
    {
      key: 'whatsapp',
      name: 'WhatsApp Business AI',
      category: 'Messaging',
      icon: 'pi pi-whatsapp',
      color: '#25D366',
      monthlyPrice: 2000,
      description: 'Official WhatsApp Cloud API 24/7 conversational assistant with auto-replies.',
      features: ['24/7 Auto-responder', 'Instant Lead Capture', 'Template Broadcasts', 'CRM Sync'],
    },
    {
      key: 'instagram',
      name: 'Instagram Direct AI',
      category: 'Social DM',
      icon: 'pi pi-instagram',
      color: '#E1306C',
      monthlyPrice: 2000,
      description: 'Automate Instagram Direct DMs, story replies, and customer inquiries.',
      features: ['Story Mentions & DM bot', 'Keyword Triggers', 'Product Catalog Inquiries', 'Lead Handoff'],
    },
    {
      key: 'facebook',
      name: 'Facebook Messenger AI',
      category: 'Social DM',
      icon: 'pi pi-facebook',
      color: '#1877F2',
      monthlyPrice: 2000,
      description: 'Handle Page messages & convert ad click-to-messenger traffic 24/7.',
      features: ['Page Messenger Bot', 'Ad Traffic Conversion', 'Instant FAQs', 'Automated Qualification'],
    },
    {
      key: 'linkedin',
      name: 'LinkedIn Automation',
      category: 'B2B Outreach',
      icon: 'pi pi-linkedin',
      color: '#0A66C2',
      monthlyPrice: 3000,
      description: 'Smart B2B prospect nurturing, automated InMail responses, and CRM syncing.',
      features: ['InMail & DM Assistant', 'B2B Lead Nurturing', 'Executive Profile Sync', 'CRM Pipeline Push'],
    },
    {
      key: 'blog',
      name: 'Autonomous Blog & SEO',
      category: 'Content Marketing',
      icon: 'pi pi-file-edit',
      color: '#8B5CF6',
      monthlyPrice: 2000,
      description: 'Daily SEO article generation, keyword targeting, and automatic publishing.',
      features: ['Daily AI Articles', 'Keyword Research', 'One-Click WordPress Sync', 'Google SEO Ranking'],
    },
    {
      key: 'voice_facilities',
      name: 'Voice Facilities Bridge',
      category: 'Telephony',
      icon: 'pi pi-phone',
      color: '#0EA5E9',
      monthlyPrice: 2500,
      description: 'Dedicated phone line provisioning, custom caller ID, and high-concurrency telephony.',
      features: ['Dedicated Virtual Number', 'Smart IVR Inbound Call Tree', 'Custom Caller ID', 'Carrier Failover'],
    },
  ];

  public availableChannels: ChannelItem[] = [...this.DEFAULT_CHANNELS];

  // Auth & Context
  public isLoggedIn = false;
  public currentUserEmail = '';
  public companyName = '';
  public isCompanyAdmin = true;
  public hasActivePlan = false;

  // Checkout Execution
  public isProcessing = false;
  public isVerifying = false;
  public verificationMessage = '';

  ngOnInit(): void {
    this.checkAuthStatus();
    this.loadPlans();
    this.restoreSelectionFromParamsOrStorage();
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }

  private checkAuthStatus(): void {
    const token =
      this.authService.getValue('accessToken') ||
      this.authService.getStaffToken();
    this.isLoggedIn = !!token;
    if (this.isLoggedIn) {
      this.subscriptions.add(
        this.authService.getAccessMe().subscribe({
          next: (me) => {
            this.currentUserEmail = me.email;
            this.companyName = me.client_name || '';
            this.isCompanyAdmin =
              me.role === 'Admin' ||
              me.role === 'CompanyAdmin' ||
              this.authService.isSuperAdmin();
            this.hasActivePlan = !!me.has_active_subscription;
          },
        })
      );
    }
  }

  private loadPlans(): void {
    this.isLoadingPlans = true;
    this.subscriptions.add(
      this.billingService.getAvailablePlans().subscribe({
        next: (plans) => {
          // 1. Extract base voice plans dynamically from API
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

          const seenMinutes = new Set<number>();
          const distinctPlans: RechargePlanTemplate[] = [];
          for (const plan of filtered) {
            if (!seenMinutes.has(plan.included_minutes)) {
              seenMinutes.add(plan.included_minutes);
              distinctPlans.push(plan);
            }
          }
          distinctPlans.sort((a, b) => a.price - b.price);
          this.standardPlans = distinctPlans.length > 0 ? distinctPlans : [...this.FALLBACK_STANDARD_PLANS];

          if (this.standardPlans.length > 0 && this.standardPlans[0].rate_per_minute > 0) {
            this.customRatePerMinute = this.standardPlans[0].rate_per_minute;
          }

          // 2. Extract channel add-ons dynamically from API
          const channelTemplates = plans.filter(
            (p) => p.plan_category === 'channel_addon' && p.feature_key
          );
          if (channelTemplates.length > 0) {
            this.buildDynamicChannels(channelTemplates);
          }

          // 3. Resolve selected plan dynamically
          this.resolveSelectedPlan();
          this.isLoadingPlans = false;
        },
        error: () => {
          this.standardPlans = [...this.FALLBACK_STANDARD_PLANS];
          this.resolveSelectedPlan();
          this.isLoadingPlans = false;
        },
      })
    );
  }

  private buildDynamicChannels(channelTemplates: RechargePlanTemplate[]): void {
    const seen = new Set<string>();
    const dynamicList: ChannelItem[] = [];

    for (const ct of channelTemplates) {
      let key = (ct.feature_key || '').toLowerCase().trim();
      if (key === 'voice_call_facilities' || key === 'voice_call' || key === 'voice-facilities') {
        key = 'voice_facilities';
      }
      if (!key || seen.has(key)) continue;
      seen.add(key);

      const meta = this.CHANNEL_STYLE_META[key] || {
        icon: 'pi pi-sparkles',
        color: '#6366f1',
        category: 'Add-on',
      };

      dynamicList.push({
        key,
        name: ct.name,
        category: meta.category,
        icon: meta.icon,
        color: meta.color,
        monthlyPrice: ct.price,
        description: ct.description || '24/7 AI conversational automation and lead synchronization.',
        features: ct.features && ct.features.length > 0 ? ct.features : ['24/7 AI Automation', 'Lead Synchronization'],
      });
    }

    if (dynamicList.length > 0) {
      for (const def of this.DEFAULT_CHANNELS) {
        if (!seen.has(def.key)) {
          dynamicList.push(def);
        }
      }
      this.availableChannels = dynamicList;
    }
  }

  private resolveSelectedPlan(): void {
    if (this.selectedPlanType === 'custom') {
      return;
    }
    if (this.standardPlans.length === 0) return;

    if (this.savedPlanIdentifier) {
      const target = this.savedPlanIdentifier.toLowerCase().trim();
      const match = this.standardPlans.find(
        (p) =>
          (p.id && String(p.id).toLowerCase() === target) ||
          (p.tier_label && p.tier_label.toLowerCase() === target) ||
          (p.name && p.name.toLowerCase() === target) ||
          (target === 'start' && p.included_minutes <= 500) ||
          (target === 'growth' && p.included_minutes > 500)
      );
      if (match) {
        this.selectedStandardPlan = match;
        return;
      }
    }

    if (!this.selectedStandardPlan) {
      const popular = this.standardPlans.find((p) => p.is_popular);
      this.selectedStandardPlan = popular || this.standardPlans[1] || this.standardPlans[0];
    }
  }

  private restoreSelectionFromParamsOrStorage(): void {
    // 1. Check Query Params first
    this.subscriptions.add(
      this.route.queryParams.subscribe((params) => {
        let hasParams = false;

        if (params['cycle']) {
          this.billingCycle = 'monthly';
          hasParams = true;
        }

        if (params['plan']) {
          const planParam = params['plan'].toLowerCase().trim();
          this.savedPlanIdentifier = planParam;
          if (planParam === 'custom' || planParam === 'enterprise') {
            this.selectedPlanType = 'custom';
            hasParams = true;
          } else {
            this.selectedPlanType = 'standard';
            this.resolveSelectedPlan();
            hasParams = true;
          }
        }

        if (params['minutes']) {
          const mins = parseInt(params['minutes'], 10);
          if (!isNaN(mins) && mins >= this.MIN_CUSTOM_MINUTES) {
            this.customMinutes = Math.min(this.MAX_CUSTOM_MINUTES, mins);
            this.selectedPlanType = 'custom';
            hasParams = true;
          }
        }

        if (params['channels']) {
          const chList = params['channels'].split(',').map((c: string) => c.trim().toLowerCase());
          for (const key of Object.keys(this.selectedChannelsMap)) {
            this.selectedChannelsMap[key] = chList.includes(key);
          }
          hasParams = true;
        }

        // 2. If no query params, check localStorage
        if (!hasParams) {
          try {
            const saved = localStorage.getItem(this.STORAGE_CHECKOUT_KEY);
            if (saved) {
              const data = JSON.parse(saved);
              if (data.billingCycle) this.billingCycle = 'monthly';
              if (data.selectedPlanType) this.selectedPlanType = data.selectedPlanType;
              if (data.selectedPlanIdentifier) {
                this.savedPlanIdentifier = data.selectedPlanIdentifier;
              } else if (data.selectedStandardTier) {
                this.savedPlanIdentifier = data.selectedStandardTier;
              }
              if (data.customMinutes) this.customMinutes = data.customMinutes;
              if (data.selectedChannelsMap) this.selectedChannelsMap = data.selectedChannelsMap;
              this.resolveSelectedPlan();
            }
          } catch {
            // ignore
          }
        }

        this.persistSelection();
      })
    );
  }

  public persistSelection(): void {
    try {
      const planIdentifier =
        this.selectedPlanType === 'custom'
          ? 'custom'
          : (this.selectedStandardPlan?.tier_label ||
             this.selectedStandardPlan?.name ||
             String(this.selectedStandardPlan?.id) ||
             'growth');

      const data = {
        billingCycle: this.billingCycle,
        selectedPlanType: this.selectedPlanType,
        selectedPlanIdentifier: planIdentifier,
        selectedPlanId: this.selectedStandardPlan?.id,
        customMinutes: this.customMinutes,
        selectedChannelsMap: this.selectedChannelsMap,
      };
      localStorage.setItem(this.STORAGE_CHECKOUT_KEY, JSON.stringify(data));
    } catch {
      // ignore
    }
  }

  // Plan modifications
  public selectStandardPlan(plan: RechargePlanTemplate): void {
    this.selectedPlanType = 'standard';
    this.selectedStandardPlan = plan;
    this.savedPlanIdentifier = plan.tier_label || plan.name || String(plan.id);
    this.persistSelection();
  }

  public selectCustomPlan(): void {
    this.selectedPlanType = 'custom';
    this.savedPlanIdentifier = 'custom';
    this.persistSelection();
  }

  public setBillingCycle(cycle: 'monthly' = 'monthly'): void {
    this.billingCycle = 'monthly';
    this.persistSelection();
  }

  public onCustomMinutesChange(mins: number): void {
    if (isNaN(mins)) mins = this.MIN_CUSTOM_MINUTES;
    const clamped = Math.min(this.MAX_CUSTOM_MINUTES, Math.max(this.MIN_CUSTOM_MINUTES, mins));
    this.customMinutes = Math.round(clamped / 50) * 50;
    this.selectedPlanType = 'custom';
    this.persistSelection();
  }

  // Package / Channel modifications
  public toggleChannel(key: string): void {
    this.selectedChannelsMap[key] = !this.selectedChannelsMap[key];
    this.persistSelection();
  }

  public selectAllChannels(): void {
    for (const ch of this.availableChannels) {
      this.selectedChannelsMap[ch.key] = true;
    }
    this.persistSelection();
  }

  public clearAllChannels(): void {
    for (const ch of this.availableChannels) {
      this.selectedChannelsMap[ch.key] = false;
    }
    this.persistSelection();
  }

  public isChannelSelected(key: string): boolean {
    return !!this.selectedChannelsMap[key];
  }

  public getSelectedChannels(): ChannelItem[] {
    return this.availableChannels.filter((ch: ChannelItem) => this.selectedChannelsMap[ch.key]);
  }

  // =========================================================================
  // Billing Calculations (GST 18% inclusive in all displayed rates)
  // =========================================================================

  public getBasePlanMinutes(): number {
    return this.getMonthlyVoiceMinutes();
  }

  public getMonthlyVoiceMinutes(): number {
    if (this.selectedPlanType === 'custom') {
      return this.customMinutes;
    }
    return this.selectedStandardPlan?.included_minutes || 0;
  }

  public getBasePlanName(): string {
    if (this.selectedPlanType === 'custom') {
      return `Enterprise Custom (${this.customMinutes.toLocaleString('en-IN')} Calling Mins/mo)`;
    }
    if (this.selectedStandardPlan) {
      const tier = this.selectedStandardPlan.tier_label || this.selectedStandardPlan.name;
      return `${tier} Plan (${this.selectedStandardPlan.included_minutes.toLocaleString('en-IN')} Calling Mins/mo)`;
    }
    return 'Voice Calling Plan';
  }

  public getBasePlanMonthlyPrice(): number {
    if (this.selectedPlanType === 'custom') {
      return this.customMinutes * this.customRatePerMinute;
    }
    return this.selectedStandardPlan?.price || 0;
  }

  public getBasePlanGrossCyclePrice(): number {
    return this.getBasePlanMonthlyPrice();
  }

  public getChannelsMonthlyTotal(): number {
    return this.getSelectedChannels().reduce((sum: number, ch: ChannelItem) => sum + ch.monthlyPrice, 0);
  }

  public getChannelsGrossCycleTotal(): number {
    return this.getChannelsMonthlyTotal();
  }

  public getGrossCycleSubtotal(): number {
    return this.getBasePlanGrossCyclePrice() + this.getChannelsGrossCycleTotal();
  }

  public getYearlyDiscountAmount(): number {
    return 0;
  }

  public getGrandTotal(): number {
    return Math.max(0, this.getGrossCycleSubtotal());
  }

  /**
   * Back-calculated Net Taxable Amount (Base before 18% GST).
   * Grand Total = Net * 1.18  =>  Net = Grand Total / 1.18
   */
  public getNetTaxableAmount(): number {
    const total = this.getGrandTotal();
    return Math.round((total / (1 + this.GST_RATE)) * 100) / 100;
  }

  public getTaxCGST(): number {
    const net = this.getNetTaxableAmount();
    return Math.round((net * 0.09) * 100) / 100;
  }

  public getTaxSGST(): number {
    const net = this.getNetTaxableAmount();
    return Math.round((net * 0.09) * 100) / 100;
  }

  public getTotalTaxAmount(): number {
    return Math.round((this.getGrandTotal() - this.getNetTaxableAmount()) * 100) / 100;
  }

  public toggleTaxBreakdown(): void {
    this.showTaxBreakdown = !this.showTaxBreakdown;
  }

  // =========================================================================
  // Payment Flow
  // =========================================================================

  public proceedToPayment(): void {
    if (!this.isLoggedIn) {
      this.toastService.info(
        'Please sign in or register to attach your subscription to your workspace.',
        'Sign In Required'
      );
      this.persistSelection();
      this.redirectToAuth();
      return;
    }

    if (this.hasActivePlan) {
      this.toastService.warn(
        'Your workspace already has an active subscription. Manage plans from Usage & Billing.',
        'Active Plan Running'
      );
      return;
    }

    this.isProcessing = true;

    const channels = this.getSelectedChannels().map((c: ChannelItem) => c.key);
    const payload: CustomBundlePayload = {
      include_voice: true,
      voice_minutes: this.getMonthlyVoiceMinutes(),
      channels: channels,
      billing_cycle: this.billingCycle,
    };

    this.subscriptions.add(
      this.billingService.createCustomBundle(payload).subscribe({
        next: (subRes) => {
          this.isProcessing = false;
          this.launchRazorpayCheckout(subRes);
        },
        error: (err) => {
          this.isProcessing = false;
          this.toastService.error(
            err?.error?.detail || 'Failed to initialize subscription checkout. Please try again.',
            'Checkout Error'
          );
        },
      })
    );
  }

  private launchRazorpayCheckout(subRes: RazorpaySubscriptionResponse): void {
    if (typeof (window as any).Razorpay === 'undefined') {
      this.toastService.error(
        'Razorpay checkout SDK is not loaded. Please check your connection and reload the page.',
        'Payment Gateway Error'
      );
      return;
    }

    const cycleLabel = 'Monthly';
    const options = {
      key: subRes.key_id,
      subscription_id: subRes.subscription_id,
      name: 'LeadAI Automation',
      description: `${cycleLabel} Subscription: ${subRes.plan_name}`,
      handler: (response: any) => {
        this.verifyPayment(subRes, response);
      },
      modal: {
        ondismiss: () => {
          this.handlePaymentDismiss(subRes);
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

  private verifyPayment(subRes: RazorpaySubscriptionResponse, paymentRes: any): void {
    this.isVerifying = true;
    this.verificationMessage = 'Verifying AutoPay mandate and activating your subscription package...';

    const verifyPayload = {
      razorpay_subscription_id: paymentRes.razorpay_subscription_id || subRes.subscription_id,
      razorpay_payment_id: paymentRes.razorpay_payment_id,
      razorpay_signature: paymentRes.razorpay_signature,
      plan_template_id: subRes.plan_id,
    };

    this.subscriptions.add(
      this.billingService.verifyRazorpaySubscription(verifyPayload).subscribe({
        next: () => {
          this.verificationMessage = 'Payment successful! Taking you to onboarding steps...';
          this.toastService.success(
            'Payment verified and subscription activated successfully! Welcome to LeadAI.',
            'Plan Activated'
          );
          this.hasActivePlan = true;
          localStorage.removeItem(this.STORAGE_CHECKOUT_KEY);

          // Refresh session & redirect to onboarding
          this.authService.getAccessMe().subscribe({
            next: (me) => {
              this.onboardingService.initOnboarding(me?.client_id || this.authService.getSelectedCompanyId());
              setTimeout(() => {
                this.isVerifying = false;
                this.router.navigate(['/onboarding']);
              }, 1200);
            },
            error: () => {
              this.onboardingService.initOnboarding(this.authService.getSelectedCompanyId());
              setTimeout(() => {
                this.isVerifying = false;
                this.router.navigate(['/onboarding']);
              }, 1200);
            },
          });
        },
        error: (err) => {
          this.isVerifying = false;
          this.toastService.error(
            err?.error?.detail || 'Signature verification failed. Please contact support.',
            'Verification Failed'
          );
        },
      })
    );
  }

  private handlePaymentDismiss(subRes: RazorpaySubscriptionResponse): void {
    this.billingService
      .recordPaymentFailure({
        subscription_id: subRes.subscription_id,
        error_code: 'CHECKOUT_DISMISSED',
        error_description: 'Checkout window was closed before completing mandate authorization.',
      })
      .subscribe();

    this.toastService.warn(
      'Checkout closed. You have not been charged.',
      'Checkout Cancelled'
    );
  }

  private handlePaymentFailure(subRes: RazorpaySubscriptionResponse, failRes: any): void {
    this.billingService
      .recordPaymentFailure({
        subscription_id: subRes.subscription_id,
        error_code: failRes?.error?.code || 'PAYMENT_FAILED',
        error_description: failRes?.error?.description || 'Mandate authorization failed at bank.',
      })
      .subscribe();

    this.toastService.error(
      failRes?.error?.description || 'Payment mandate could not be authorized.',
      'Payment Failed'
    );
  }

  public redirectToAuth(): void {
    this.persistSelection();
    this.authService.initiateOidcLogin();
  }

  public redirectToRegister(): void {
    this.persistSelection();
    this.authService.redirectToRegister({
      source: 'leadai',
      returnUrl: `${window.location.origin}/checkout`,
      permissions: this.getSelectedPermissionsString(),
      plan:
        this.selectedPlanType === 'custom'
          ? 'custom'
          : (this.selectedStandardPlan?.tier_label?.toLowerCase() ||
             this.selectedStandardPlan?.name?.toLowerCase() ||
             'standard'),
      planName: this.getBasePlanName(),
      selectedPlan:
        this.selectedPlanType === 'custom'
          ? 'custom'
          : (this.selectedStandardPlan?.tier_label?.toLowerCase() ||
             this.selectedStandardPlan?.name?.toLowerCase() ||
             'standard'),
      socialMedia: this.getSelectedChannels().map((c: ChannelItem) => c.key).join(','),
      channels: this.getSelectedChannels().map((c: ChannelItem) => c.key).join(','),
      billingCycle: this.billingCycle,
      cycle: this.billingCycle,
      minutes: this.selectedPlanType === 'custom' ? this.customMinutes : undefined,
      voiceMinutes: this.getMonthlyVoiceMinutes(),
      planId: this.selectedPlanType !== 'custom' && this.selectedStandardPlan?.id ? String(this.selectedStandardPlan.id) : undefined,
      planPrice: this.getBasePlanMonthlyPrice(),
      totalAmount: this.getGrandTotal(),
    });
  }

  private getSelectedPermissionsString(): string {
    const list: string[] = ['voice.inbound'];
    const map: Record<string, string> = {
      whatsapp: 'social.whatsapp',
      instagram: 'social.instagram',
      facebook: 'social.facebook',
      linkedin: 'social.linkedin',
      blog: 'social.blog',
      voice_facilities: 'voice.inbound',
    };
    for (const ch of this.getSelectedChannels()) {
      const p = map[ch.key];
      if (p && !list.includes(p)) list.push(p);
    }
    return list.join(',');
  }
}
