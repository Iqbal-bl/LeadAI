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
  showGrantDialog = false;
  editingPlan: RechargePlanTemplate | null = null;

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

  // Form Fields for Direct Client Grant
  grantForm: RechargeAllocatePayload = {
    client_id: '',
    plan_template_id: undefined,
    custom_minutes: 500,
    custom_validity_days: 30,
    custom_price: 2000,
    custom_name: 'Custom Enterprise Recharge',
    payment_reference: 'Super Admin Manual Grant',
  };
  grantType: 'template' | 'custom' = 'template';

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

  openCreatePlanModal(type: 'standard' | 'custom' = 'standard'): void {
    this.editingPlan = null;
    this.planForm = {
      name: type === 'custom' ? 'Custom Enterprise Plan' : 'New Standard Plan',
      plan_type: type,
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
    this.showPlanDialog = true;
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
    if (!this.planForm.name.trim() || !this.planForm.included_minutes || !this.planForm.validity_days) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Validation Error',
        detail: 'Please fill in all required plan fields.',
      });
      return;
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

  openGrantModal(clientId?: string): void {
    this.grantForm = {
      client_id: clientId || (this.companies[0]?.id || ''),
      plan_template_id: this.plans[0]?.id,
      custom_minutes: 1000,
      custom_validity_days: 60,
      custom_price: 4000,
      custom_name: 'Custom Admin Grant',
      payment_reference: 'Super Admin Manual Grant',
    };
    this.grantType = 'template';
    this.showGrantDialog = true;
  }

  submitGrant(): void {
    if (!this.grantForm.client_id) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Validation Error',
        detail: 'Please select a target client company.',
      });
      return;
    }

    this.saving = true;
    const payload: RechargeAllocatePayload = {
      client_id: this.grantForm.client_id,
      payment_reference: this.grantForm.payment_reference,
    };

    if (this.grantType === 'template') {
      payload.plan_template_id = this.grantForm.plan_template_id;
    } else {
      payload.custom_minutes = this.grantForm.custom_minutes;
      payload.custom_validity_days = this.grantForm.custom_validity_days;
      payload.custom_price = this.grantForm.custom_price;
      payload.custom_name = this.grantForm.custom_name;
    }

    this.billingService.adminRechargeClient(payload).subscribe({
      next: (recharge) => {
        this.saving = false;
        this.showGrantDialog = false;
        this.messageService.add({
          severity: 'success',
          summary: 'Recharge Granted',
          detail: `Recharge "${recharge.plan_name_snapshot}" granted to client.`,
        });
        this.loadData();
      },
      error: (err) => {
        this.saving = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Grant Failed',
          detail: err?.error?.detail || 'Failed to allocate recharge to client.',
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
