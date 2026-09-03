import { Component, OnInit } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { BillingService } from '../../../services/billing.service';
import { CompanyService } from '../../../services/company.service';
import {
  BillingSummary,
  PlanTemplateCreatePayload,
  RechargeAllocatePayload,
  RechargePlanTemplate,
} from '../../../models/billing.models';
import { Company } from '../../../models/company.models';
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
    target_client_id: null,
    included_minutes: 500,
    validity_days: 30,
    price: 2000,
    rate_per_minute: 4.0,
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

    this.billingService.getAdminClientsSummary().subscribe({
      next: (res) => {
        this.clientSummaries = res;
      },
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
      name: type === 'custom' ? 'Custom Client Plan' : 'New Standard Plan',
      plan_type: type,
      target_client_id: null,
      included_minutes: 500,
      validity_days: 30,
      price: 2000,
      rate_per_minute: 4.0,
      description: '',
    };
    this.showPlanDialog = true;
  }

  openEditPlanModal(plan: RechargePlanTemplate): void {
    this.editingPlan = plan;
    this.planForm = {
      name: plan.name,
      plan_type: plan.plan_type,
      target_client_id: plan.target_client_id || null,
      included_minutes: plan.included_minutes,
      validity_days: plan.validity_days,
      price: plan.price,
      rate_per_minute: plan.rate_per_minute,
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
}
