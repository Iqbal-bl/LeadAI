import { Component, OnInit } from '@angular/core';
import { SharedModule } from '../../../../shared/shared.module';
import { BillingService } from '../../../../services/billing.service';
import { CompanyService } from '../../../../services/company.service';
import {
  BillingSummary,
  RechargeAllocatePayload,
  RechargePlanTemplate,
} from '../../../../models/billing.models';
import { Company } from '../../../../models/company.models';
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

  // Modal Flags & Modes
  showGrantDialog = false;
  isRowSpecific = false;
  lockedClientId = '';
  selectedClientIds: string[] = [];

  // Form Fields for Direct Client Grant
  grantForm: RechargeAllocatePayload = {
    client_id: '',
    plan_template_id: undefined,
    custom_minutes: 500,
    custom_validity_days: 30,
    custom_price: 0,
    custom_name: 'SuperAdmin Direct Grant',
    payment_reference: 'SuperAdmin Direct Grant',
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

  openTopGrantModal(): void {
    this.isRowSpecific = false;
    this.lockedClientId = '';
    this.selectedClientIds = [];
    this.grantForm = {
      client_id: '',
      plan_template_id: this.plans[0]?.id,
      custom_minutes: 500,
      custom_validity_days: 30,
      custom_price: 0,
      custom_name: 'SuperAdmin Direct Grant',
      payment_reference: 'SuperAdmin Direct Grant',
    };
    this.grantType = 'template';
    this.showGrantDialog = true;
  }

  openRowGrantModal(clientId: string): void {
    this.isRowSpecific = true;
    this.lockedClientId = clientId;
    this.selectedClientIds = [clientId];
    this.grantForm = {
      client_id: clientId,
      plan_template_id: this.plans[0]?.id,
      custom_minutes: 500,
      custom_validity_days: 30,
      custom_price: 0,
      custom_name: 'SuperAdmin Direct Grant',
      payment_reference: 'SuperAdmin Direct Grant',
    };
    this.grantType = 'template';
    this.showGrantDialog = true;
  }

  submitGrant(): void {
    const clientIds = this.isRowSpecific
      ? (this.lockedClientId ? [this.lockedClientId] : [])
      : this.selectedClientIds;

    if (!clientIds || clientIds.length === 0) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Validation Error',
        detail: 'Please select at least one target client company.',
      });
      return;
    }

    if (this.grantType === 'template' && !this.grantForm.plan_template_id) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Validation Error',
        detail: 'Please select a plan template.',
      });
      return;
    }

    if (this.grantType === 'custom') {
      if (!this.grantForm.custom_minutes || this.grantForm.custom_minutes <= 0) {
        this.messageService.add({
          severity: 'warn',
          summary: 'Validation Error',
          detail: 'Minutes to credit must be strictly greater than 0.',
        });
        return;
      }
      if (!this.grantForm.custom_validity_days || this.grantForm.custom_validity_days <= 0) {
        this.messageService.add({
          severity: 'warn',
          summary: 'Validation Error',
          detail: 'Validity days must be strictly greater than 0.',
        });
        return;
      }
    }

    this.saving = true;
    const payload: RechargeAllocatePayload = {
      client_id: clientIds[0],
      client_ids: clientIds,
      payment_reference: this.grantForm.payment_reference || 'SuperAdmin Direct Grant',
    };

    if (this.grantType === 'template') {
      payload.plan_template_id = this.grantForm.plan_template_id;
    } else {
      payload.custom_minutes = this.grantForm.custom_minutes;
      payload.custom_validity_days = this.grantForm.custom_validity_days;
      payload.custom_price = 0;
      payload.custom_name = this.grantForm.custom_name || 'SuperAdmin Direct Grant';
    }

    this.billingService.adminRechargeClient(payload).subscribe({
      next: (recharge) => {
        this.saving = false;
        this.showGrantDialog = false;
        this.messageService.add({
          severity: 'success',
          summary: 'Recharge Granted',
          detail: `Recharge "${recharge.plan_name_snapshot}" granted to ${clientIds.length} company/companies.`,
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

  cleanPlanName(name: string | null | undefined): string {
    if (!name) return 'No Active Plan';
    return name.replace(/\s*\+\s*.*?(?=\s*-\s*|\s*\()/i, '').trim();
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

  getSelectedPlan(): RechargePlanTemplate | undefined {
    return this.plans.find((p) => p.id === this.grantForm.plan_template_id);
  }

  getCategoryBadge(category: string | undefined): { label: string; class: string; icon: string } {
    switch (category) {
      case 'voice_topup':
        return { label: 'Booster', class: 'bg-amber-100 text-amber-800 dark:bg-amber-950/70 dark:text-amber-300', icon: 'pi pi-bolt' };
      case 'channel_addon':
        return { label: 'Add-on', class: 'bg-sky-100 text-sky-800 dark:bg-sky-950/70 dark:text-sky-300', icon: 'pi pi-comments' };
      case 'omni_channel':
        return { label: 'Omni', class: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300', icon: 'pi pi-share-alt' };
      default:
        return { label: 'Base Plan', class: 'bg-purple-100 text-purple-800 dark:bg-purple-950/70 dark:text-purple-300', icon: 'pi pi-phone' };
    }
  }
}
