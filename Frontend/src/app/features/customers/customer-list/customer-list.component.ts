import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';
import { SharedModule } from '../../../shared/shared.module';
import { CustomerService } from '../../../services/customer.service';
import { AuthService } from '../../../services/auth.service';
import { Customer, CustomerGreeting } from '../../../models/customer.models';
import { MessageService } from 'primeng/api';
import { CreateCustomerComponent } from '../create-customer/create-customer.component';

@Component({
  selector: 'app-customer-list',
  standalone: true,
  imports: [SharedModule, CreateCustomerComponent],
  templateUrl: './customer-list.component.html',
  styleUrl: './customer-list.component.scss',
})
export class CustomerListComponent implements OnInit {
  customers: Customer[] = [];
  greetings: CustomerGreeting[] = [];
  loading = true;
  totalItems = 0;
  showCreateDialog = false;

  // Filters
  stageFilter = '';
  statusFilter = '';
  searchText = '';
  followUpDueOnly = false;

  hasReadAll = false;

  stageOptions = [
    { label: 'All Stages', value: '' },
    { label: 'New', value: 'new' },
    { label: 'Active', value: 'active' },
    { label: 'VIP', value: 'vip' },
    { label: 'Churned', value: 'churned' },
  ];

  statusOptions = [
    { label: 'All Statuses', value: '' },
    { label: 'Active', value: 'active' },
    { label: 'Inactive', value: 'inactive' },
  ];

  constructor(
    private customerService: CustomerService,
    private authService: AuthService,
    private messageService: MessageService,
    private router: Router,
  ) {}

  ngOnInit(): void {
    const user = this.authService.getCurrentUser();
    this.hasReadAll = user?.permissions?.includes('customer.read.all') ?? false;

    this.loadCustomers();
    this.loadGreetings();
  }

  loadCustomers(): void {
    this.loading = true;
    this.customerService.getCustomers({
      stage: this.stageFilter || undefined,
      status: this.statusFilter || undefined,
      search: this.searchText || undefined,
      follow_up_due: this.followUpDueOnly || undefined,
    }).subscribe({
      next: (res) => {
        this.customers = res.items;
        this.totalItems = res.total_items;
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      },
    });
  }

  loadGreetings(): void {
    this.customerService.getUpcomingGreetings(7).subscribe({
      next: (res: any) => {
        this.greetings = Array.isArray(res) ? res : (res?.items || []);
      },
      error: () => {
        this.greetings = [];
      },
    });
  }

  onSearch(): void {
    this.loadCustomers();
  }

  viewCustomer(customer: Customer): void {
    this.router.navigate(['/client/customers', customer.id]);
  }

  createFestiveCampaign(): void {
    this.router.navigate(['/client/campaigns'], { queryParams: { purpose: 'festive' } });
  }

  getStageColor(stage: string): string {
    const colors: Record<string, string> = {
      new: '#3b82f6',
      active: '#22c55e',
      vip: '#f59e0b',
      churned: '#ef4444',
    };
    return colors[stage] || '#6b7280';
  }

  getStageSeverity(stage: string): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
    const map: Record<string, 'success' | 'info' | 'warn' | 'danger' | 'secondary'> = {
      new: 'info',
      active: 'success',
      vip: 'warn',
      churned: 'danger',
    };
    return map[stage] || 'secondary';
  }

  openCreateCustomerDialog(): void {
    this.showCreateDialog = true;
  }

  onCustomerCreated(): void {
    this.showCreateDialog = false;
    this.loadCustomers();
  }
}
