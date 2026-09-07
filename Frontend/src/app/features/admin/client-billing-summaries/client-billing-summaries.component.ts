import { Component, OnInit } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { BillingService } from '../../../services/billing.service';
import { CompanyService } from '../../../services/company.service';
import {
  BillingSummary,
  RechargeAllocatePayload,
  RechargePlanTemplate,
} from '../../../models/billing.models';
import { Company } from '../../../models/company.models';
import { MessageService } from 'primeng/api';

@Component({
  selector: 'app-client-billing-summaries',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './client-billing-summaries.component.html',
  styleUrl: './client-billing-summaries.component.scss',
})
export class ClientBillingSummariesComponent implements OnInit {
  plans: RechargePlanTemplate[] = [];
  clientSummaries: BillingSummary[] = [];
  companies: Company[] = [];
  loading = true;
  saving = false;

  // Modal Flags
  showGrantDialog = false;

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
    this.billingService.getAdminClientsSummary().subscribe({
      next: (res) => {
        this.clientSummaries = res;
        this.loading = false;
      },
      error: () => (this.loading = false),
    });

    this.billingService.getAdminPlans().subscribe({
      next: (res) => {
        this.plans = res;
      },
    });

    this.companyService.getCompanies().subscribe({
      next: (companies) => {
        this.companies = companies;
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
        const detailMsg = typeof err?.error?.detail === 'string'
          ? err.error.detail
          : 'Failed to allocate recharge to client.';
        this.messageService.add({
          severity: 'error',
          summary: 'Grant Failed',
          detail: detailMsg,
        });
      },
    });
  }

  getCompanyName(clientId: string): string {
    const comp = this.companies.find((c) => c.id === clientId);
    return comp ? comp.name : clientId;
  }
}
