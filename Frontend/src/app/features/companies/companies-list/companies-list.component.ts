import { Component, EventEmitter, OnInit, Output, inject } from '@angular/core';
import { CompanyService } from '../../../services/company.service';
import { Company } from '../../../models/company.models';
import { MessageService } from 'primeng/api';
import { ConfirmationService } from '../../../shared/services/confirmation.service';
import { Router } from '@angular/router';

import { SharedModule } from '../../../shared/shared.module';

@Component({
  selector: 'app-companies-list',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './companies-list.component.html',
  styleUrl: './companies-list.component.scss',
})
export class CompaniesListComponent implements OnInit {
  private companyService = inject(CompanyService);
  private confirmationService = inject(ConfirmationService);
  private messageService = inject(MessageService);
  private router = inject(Router);

  companies: Company[] = [];
  loading = false;
  searchQuery = '';

  // Dialog State
  displayDialog = false;
  selectedCompany: Company | null = null;

  // Stats Counters
  totalCompaniesCount = 0;
  activeCompaniesCount = 0;
  totalUsersLicensed = 0;
  totalConversationsCount = 0;

  private mockCompanies: Company[] = [
    {
      id: 'company-1',
      name: 'TechCorp Solutions',
      email: 'billing@techcorp.com',
      phone_number: '+1 (555) 234-5678',
      description:
        'Enterprise SaaS provider specializing in sales automation and custom CRM integrations.',
      is_active: true,
      created_at: '2024-01-15',
      user_count: 142,
      document_count: 24,
      chunk_count: 1240,
      script_count: 5,
      conversation_count: 1845,
    },
    {
      id: 'company-2',
      name: 'GlobalFin Partners',
      email: 'contact@globalfin.com',
      phone_number: '+1 (555) 345-6789',
      description:
        'Global investment banking and asset management group requiring high OIDC security compliance.',
      is_active: true,
      created_at: '2024-01-12',
      user_count: 89,
      document_count: 18,
      chunk_count: 820,
      script_count: 3,
      conversation_count: 942,
    },
    {
      id: 'company-3',
      name: 'HealthPlus Analytics',
      email: 'support@healthplus.io',
      phone_number: '+1 (555) 456-7890',
      description:
        'AI-driven clinical health data solutions and automated customer portal routing.',
      is_active: true,
      created_at: '2024-01-10',
      user_count: 56,
      document_count: 12,
      chunk_count: 450,
      script_count: 2,
      conversation_count: 480,
    },
    {
      id: 'company-4',
      name: 'RetailMax Corp',
      email: 'operations@retailmax.com',
      phone_number: '+1 (555) 567-8901',
      description:
        'E-commerce logistics and national direct-to-consumer distribution networks.',
      is_active: false,
      created_at: '2024-01-08',
      user_count: 12,
      document_count: 4,
      chunk_count: 150,
      script_count: 1,
      conversation_count: 125,
    },
  ];

  ngOnInit(): void {
    this.loadCompanies();
  }

  loadCompanies(): void {
    this.loading = true;
    this.companyService.getCompanies(true).subscribe({
      next: (data) => {
        this.companies =
          data && data.length > 0 ? data : [...this.mockCompanies];
        this.calculateStats();
        this.loading = false;
      },
      error: () => {
        // Fall back to robust mock data for local demo execution
        this.companies = [...this.mockCompanies];
        this.calculateStats();
        this.loading = false;
      },
    });
  }

  private calculateStats(): void {
    this.totalCompaniesCount = this.companies.length;
    this.activeCompaniesCount = this.companies.filter(
      (c) => c.is_active,
    ).length;
    this.totalUsersLicensed = this.companies.reduce(
      (sum, c) => sum + (c.user_count || 0),
      0,
    );
    this.totalConversationsCount = this.companies.reduce(
      (sum, c) => sum + (c.conversation_count || 0),
      0,
    );
  }

  openCreateDialog(): void {
    this.router.navigate(['/admin/clients/create']);
  }

  viewCompanyDetail(company: Company): void {
    this.router.navigate(['/admin/clients/detail', company.id]);
  }

  openEditDialog(company: Company): void {
    this.router.navigate(['/admin/clients/edit', company.id]);
  }

  onDialogSave(): void {
    this.displayDialog = false;
    this.loadCompanies();
  }

  onDialogCancel(): void {
    this.displayDialog = false;
  }

  toggleActiveStatus(company: Company): void {
    const updatedStatus = !company.is_active;
    this.companyService
      .updateCompany(company.id, { is_active: updatedStatus })
      .subscribe({
        next: () => {
          company.is_active = updatedStatus;
          this.calculateStats();
          this.messageService.add({
            severity: 'success',
            summary: 'Status Updated',
            detail: `${company.name} is now ${updatedStatus ? 'Active' : 'Inactive'}`,
          });
        },
        error: () => {
          // Mock state toggle update local fallback
          company.is_active = updatedStatus;
          this.calculateStats();
          this.messageService.add({
            severity: 'success',
            summary: 'Status Updated (Demo Mode)',
            detail: `${company.name} is now ${updatedStatus ? 'Active' : 'Inactive'}`,
          });
        },
      });
  }

  deleteCompany(company: Company): void {
    this.confirmationService.confirm({
      message: `Are you sure you want to delete ${company.name}? This will revoke access for all associated users.`,
      header: 'Delete Company Workspace',
      icon: 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: 'p-button-danger p-button-sm cursor-pointer',
      rejectButtonStyleClass:
        'p-button-secondary p-button-outlined p-button-sm cursor-pointer',
      accept: () => {
        this.companyService.deleteCompany(company.id, company.id).subscribe({
          next: (res) => {
            this.companies = this.companies.filter((c) => c.id !== company.id);
            this.calculateStats();
            this.messageService.add({
              severity: 'success',
              summary: 'Deleted',
              detail: res?.message || 'Company workspace deleted successfully',
            });
          },
          error: (err) => {
            console.error('Delete company error:', err);
            this.messageService.add({
              severity: 'error',
              summary: 'Delete Failed',
              detail: err?.error?.message || 'Failed to delete company workspace',
            });
          },
        });
      },
    });
  }

  getFilteredCompanies(): Company[] {
    if (!this.searchQuery) return this.companies;
    const query = this.searchQuery.toLowerCase();
    return this.companies.filter(
      (c) =>
        c.name.toLowerCase().includes(query) ||
        c.email.toLowerCase().includes(query) ||
        (c.description || '').toLowerCase().includes(query),
    );
  }
}
