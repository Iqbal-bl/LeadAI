import { Component, OnInit } from '@angular/core';
import { SharedModule } from '../../../../shared/shared.module';
import { BillingService } from '../../../../services/billing.service';
import { CompanyService } from '../../../../services/company.service';
import {
  BillingSummary,
  PlanTemplateCreatePayload,
  RechargeAllocatePayload,
  RechargePlanTemplate,
} from '../../../../models/billing.models';
import { Company } from '../../../../models/company.models';
import { MessageService } from 'primeng/api';

@Component({
  selector: 'app-plan-management',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './plan-management.component.html',
  styleUrl: './plan-management.component.scss',
})
export class PlanManagementComponent implements OnInit {
  plans: RechargePlanTemplate[] = [];
  clientSummaries: BillingSummary[] = [];
  companies: Company[] = [];
  loading = true;
  saving = false;

  // Dialog Flags
  showPlanDialog = false;
  editingPlan: RechargePlanTemplate | null = null;
  autoCalculatePrice: boolean = true;

  // Form Fields for Master / Custom Plan
  planForm: PlanTemplateCreatePayload = {
    name: '',
    plan_type: 'standard',
    plan_category: 'voice_standard',
    feature_key: null,
    target_client_id: null,
    target_client_ids: [],
    addon_channels: [],
    included_minutes: 500,
    validity_days: 30,
    price: 2000,
    rate_per_minute: 4.0,
    auto_pay_by_default: true,
    description: '',
  };

  constructor(
    private billingService: BillingService,
    private companyService: CompanyService,
    private messageService: MessageService,
  ) {}

  ngOnInit(): void {
    this.loadData();
  }

  loadData(): void {
    this.loading = true;
    this.billingService.getAdminPlans().subscribe({
      next: (res) => {
        this.plans = res;
        this.loading = false;
      },
      error: () => (this.loading = false),
    });


    this.companyService.getCompanies().subscribe({
      next: (companies) => {
        this.companies = companies;
      },
    });
  }

  // Category Filter Tab
  selectedCategoryTab: 'all' | 'voice_standard' | 'voice_topup' | 'channel_addon' = 'all';

  // Multi-Company Selection for Direct Grants
  grantClientIds: string[] = [];

  get filteredPlans(): RechargePlanTemplate[] {
    if (this.selectedCategoryTab === 'all') return this.plans;
    return this.plans.filter((p) => (p.plan_category || 'voice_standard') === this.selectedCategoryTab);
  }

  openCreatePlanModal(
    category: 'voice_standard' | 'voice_topup' | 'channel_addon' = 'voice_standard',
    type: 'standard' | 'custom' = 'standard'
  ): void {
    this.editingPlan = null;
    let name = 'New Standard Plan';
    if (category === 'voice_topup') name = 'New Minute Booster';
    else if (category === 'channel_addon') name = 'New Channel Add-on';
    else if (type === 'custom') name = 'Custom Enterprise Plan';

    this.autoCalculatePrice = true;
    const defaultMins = category === 'channel_addon' ? 0 : (category === 'voice_topup' ? 250 : 500);
    const defaultRate = 4.0;
    const defaultPrice = category === 'channel_addon' ? 1000 : Math.round(defaultMins * defaultRate);

    this.planForm = {
      name: name,
      plan_type: category === 'voice_topup' ? 'topup' : type,
      plan_category: category,
      feature_key: category === 'channel_addon' ? 'whatsapp' : null,
      target_client_id: null,
      target_client_ids: [],
      addon_channels: [],
      included_minutes: defaultMins,
      validity_days: 30, // Locked strictly at 30 days
      price: defaultPrice,
      rate_per_minute: defaultRate,
      auto_pay_by_default: category !== 'voice_topup',
      description: '',
    };
    this.showPlanDialog = true;
  }

  onAutoCalcToggle(): void {
    if (this.autoCalculatePrice) {
      this.recalculatePrice();
    }
  }

  onRateOrMinutesChange(): void {
    if (this.autoCalculatePrice) {
      this.recalculatePrice();
    }
  }

  recalculatePrice(): void {
    if (this.planForm.plan_category === 'channel_addon') return;
    const mins = Number(this.planForm.included_minutes || 0);
    const rate = Number(this.planForm.rate_per_minute || 0);
    if (mins > 0 && rate > 0) {
      this.planForm.price = Math.round(mins * rate);
    }
  }

  onCategoryChange(): void {
    this.planForm.validity_days = 30; // Always 30 days
    if (this.planForm.plan_category === 'voice_topup') {
      this.planForm.plan_type = 'topup';
      this.planForm.auto_pay_by_default = false;
      this.planForm.feature_key = null;
      if (!this.planForm.included_minutes || this.planForm.included_minutes === 0) {
        this.planForm.included_minutes = 250;
      }
    } else if (this.planForm.plan_category === 'channel_addon') {
      this.planForm.plan_type = 'standard';
      this.planForm.included_minutes = 0;
      this.planForm.auto_pay_by_default = true;
      if (!this.planForm.feature_key) this.planForm.feature_key = 'whatsapp';
    } else {
      this.planForm.feature_key = null;
      if (this.planForm.plan_type === 'topup') this.planForm.plan_type = 'standard';
      if (!this.planForm.included_minutes) this.planForm.included_minutes = 500;
      this.planForm.auto_pay_by_default = true;
    }
    if (this.autoCalculatePrice) {
      this.recalculatePrice();
    }
  }

  openEditPlanModal(plan: RechargePlanTemplate): void {
    this.editingPlan = plan;
    const clientIds = plan.target_client_ids || (plan.target_client_id ? [plan.target_client_id] : []);
    this.planForm = {
      name: plan.name,
      plan_type: plan.plan_type,
      plan_category: plan.plan_category || 'voice_standard',
      feature_key: plan.feature_key || null,
      target_client_id: plan.target_client_id || null,
      target_client_ids: clientIds,
      addon_channels: plan.addon_channels || [],
      included_minutes: plan.included_minutes,
      validity_days: plan.validity_days,
      price: plan.price,
      rate_per_minute: plan.rate_per_minute,
      auto_pay_by_default: plan.auto_pay_by_default ?? true,
      description: plan.description || '',
    };
    this.showPlanDialog = true;
  }

  savePlan(): void {
    if (!this.planForm.name.trim()) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Validation Error',
        detail: 'Plan name is required.',
      });
      return;
    }

    if (this.planForm.plan_category === 'voice_standard') {
      if (!this.planForm.included_minutes || this.planForm.included_minutes <= 0) {
        this.messageService.add({
          severity: 'warn',
          summary: 'Validation Error',
          detail: 'Included minutes must be greater than 0 for base voice plans.',
        });
        return;
      }
      if (!this.planForm.validity_days || this.planForm.validity_days <= 0) {
        this.messageService.add({
          severity: 'warn',
          summary: 'Validation Error',
          detail: 'Validity days must be greater than 0 for base voice plans.',
        });
        return;
      }
    } else if (this.planForm.plan_category === 'voice_topup') {
      if (!this.planForm.included_minutes || this.planForm.included_minutes <= 0) {
        this.messageService.add({
          severity: 'warn',
          summary: 'Validation Error',
          detail: 'Booster call minutes must be greater than 0.',
        });
        return;
      }
      this.planForm.validity_days = 30;
      this.planForm.auto_pay_by_default = false;
      this.planForm.plan_type = 'topup';
    } else if (this.planForm.plan_category === 'channel_addon') {
      if (!this.planForm.feature_key) {
        this.messageService.add({
          severity: 'warn',
          summary: 'Validation Error',
          detail: 'Please select a channel (WhatsApp, Instagram, Facebook, or LinkedIn).',
        });
        return;
      }
      this.planForm.included_minutes = 0;
      this.planForm.validity_days = 30;
      this.planForm.plan_type = 'standard';
      this.planForm.auto_pay_by_default = true;
    }

    if (this.planForm.plan_type === 'custom') {
      if (this.planForm.target_client_ids && this.planForm.target_client_ids.length > 0) {
        this.planForm.target_client_id = this.planForm.target_client_ids[0];
      }
      this.planForm.auto_pay_by_default = true;
    }

    this.saving = true;

    if (this.editingPlan) {
      // Edit existing plan (modifies future recharges only)
      this.billingService.updateAdminPlan(this.editingPlan.id, this.planForm).subscribe({
        next: () => {
          this.saving = false;
          this.showPlanDialog = false;
          this.messageService.add({
            severity: 'success',
            summary: 'Plan Updated',
            detail: 'Master plan template updated! Existing recharges remain untouched.',
          });
          this.loadData();
        },
        error: (err) => {
          this.saving = false;
          this.messageService.add({
            severity: 'error',
            summary: 'Save Failed',
            detail: err?.error?.detail || 'Failed to update plan template.',
          });
        },
      });
    } else {
      // Create new plan template
      this.billingService.createAdminPlan(this.planForm).subscribe({
        next: () => {
          this.saving = false;
          this.showPlanDialog = false;
          this.messageService.add({
            severity: 'success',
            summary: 'Plan Created',
            detail: 'New recharge plan template created successfully.',
          });
          this.loadData();
        },
        error: (err) => {
          this.saving = false;
          this.messageService.add({
            severity: 'error',
            summary: 'Creation Failed',
            detail: err?.error?.detail || 'Failed to create plan template.',
          });
        },
      });
    }
  }

  deletePlan(plan: RechargePlanTemplate): void {
    if (
      !confirm(
        `Are you sure you want to retire "${plan.name}"?\n\nExisting clients who already purchased this plan will keep their remaining minutes and validity, but no new clients will be able to purchase it.`
      )
    ) {
      return;
    }

    this.billingService.deleteAdminPlan(plan.id).subscribe({
      next: () => {
        this.messageService.add({
          severity: 'success',
          summary: 'Plan Retired',
          detail: `Plan "${plan.name}" has been retired and removed from available plans.`,
        });
        this.loadData();
      },
      error: (err) => {
        this.messageService.add({
          severity: 'error',
          summary: 'Delete Failed',
          detail: err?.error?.detail || 'Failed to retire plan template.',
        });
      },
    });
  }

  getCompanyName(clientId: string): string {
    const comp = this.companies.find((c) => c.id === clientId);
    return comp ? comp.name : clientId;
  }

  getCompanyNames(plan: RechargePlanTemplate): string[] {
    const ids = (plan.target_client_ids && plan.target_client_ids.length > 0)
      ? plan.target_client_ids
      : (plan.target_client_id ? [plan.target_client_id] : []);
    return ids.map((id) => this.getCompanyName(id));
  }

  getCompanySummary(plan: RechargePlanTemplate): string {
    const names = this.getCompanyNames(plan);
    if (names.length === 0) return 'Global (All Clients)';
    if (names.length === 1) return names[0];
    return `${names[0]} (+${names.length - 1} more)`;
  }

  getAllCompanyNamesTooltip(plan: RechargePlanTemplate): string {
    const names = this.getCompanyNames(plan);
    return names.join(', ');
  }

  readonly CHANNEL_CONFIG: { [key: string]: { name: string; icon: string } } = {
    whatsapp: { name: 'WhatsApp', icon: 'pi pi-whatsapp' },
    instagram: { name: 'Instagram', icon: 'pi pi-instagram' },
    facebook: { name: 'Facebook', icon: 'pi pi-facebook' },
    linkedin: { name: 'LinkedIn', icon: 'pi pi-linkedin' },
  };

  getChannelInfo(ch: string): { name: string; icon: string } {
    const key = (ch || '').toLowerCase().trim();
    return this.CHANNEL_CONFIG[key] || { name: ch, icon: 'pi pi-globe' };
  }
}
