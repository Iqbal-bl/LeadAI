import { Component, OnInit, ViewChild, inject } from '@angular/core';
import { CompanyService } from '../../../../../services/company.service';
import { Company } from '../../../../../models/company.models';
import { CompaniesListComponent } from '../../../../../features/companies/companies-list/companies-list.component';
import { Router } from '@angular/router';

import { SharedModule } from '../../../../../shared/shared.module';

@Component({
  selector: 'admin-client-list',
  standalone: true,
  imports: [SharedModule, CompaniesListComponent],
  templateUrl: './client-list.component.html',
  styleUrl: './client-list.component.scss',
})
export class ClientListComponent implements OnInit {
  private companyService = inject(CompanyService);
  private router = inject(Router);

  @ViewChild(CompaniesListComponent) companiesList!: CompaniesListComponent;

  // KPI Stats
  totalCompaniesCount = 0;
  activeCompaniesCount = 0;
  totalUsersLicensed = 0;
  totalConversationsCount = 0;

  ngOnInit(): void {
    this.loadStats();
  }

  loadStats(): void {
    this.companyService.getCompanies(true).subscribe({
      next: (data) => {
        const companies = data;
        this.calculateStats(companies);
      },
      error: () => {},
    });
  }

  private calculateStats(companies: Company[]): void {
    this.totalCompaniesCount = companies.length;
    this.activeCompaniesCount = companies.filter((c) => c.is_active).length;
    this.totalUsersLicensed = companies.reduce(
      (sum, c) => sum + (c.user_count || 0),
      0,
    );
    this.totalConversationsCount = companies.reduce(
      (sum, c) => sum + (c.conversation_count || 0),
      0,
    );
  }

  openCreateDialog(): void {
    this.router.navigate(['/admin/clients/create']);
  }
}
