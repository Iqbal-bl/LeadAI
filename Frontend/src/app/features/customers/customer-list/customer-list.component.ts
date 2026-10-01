import { Component, OnInit, ViewChild } from '@angular/core';
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
  @ViewChild('customerActionMenu') customerActionMenu!: any;

  customers: Customer[] = [];
  greetings: CustomerGreeting[] = [];
  loading = true;
  totalItems = 0;
  showCreateDialog = false;

  // Active filters
  stageFilter = '';
  statusFilter = '';
  searchText = '';

  hasReadAll = false;

  // Menu action state
  activeCustomerMenuItems: any[] = [];
  selectedCustomer: Customer | null = null;

  // Quick message dialog
  showMessageDialog = false;
  messageText = '';
  messageChannel: 'whatsapp' | 'sms' | 'email' | 'voice' = 'whatsapp';
  sendingMessage = false;

  stageOptions = [
    { label: 'All Stages', value: '' },
    { label: 'Opportunity', value: 'opportunity' },
    { label: 'Customer', value: 'customer' },
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
  ) { }

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
      search: this.searchText.trim() || undefined,
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

  clearFilters(): void {
    this.stageFilter = '';
    this.statusFilter = '';
    this.searchText = '';
    this.loadCustomers();
  }

  hasActiveFilters(): boolean {
    return !!(this.stageFilter || this.statusFilter || this.searchText.trim());
  }

  viewCustomer(customer: Customer): void {
    this.router.navigate(['/client/customers', customer.id]);
  }

  createFestiveCampaign(): void {
    this.router.navigate(['/client/campaigns'], { queryParams: { purpose: 'festive' } });
  }

  openCustomerMenu(event: Event, customer: Customer): void {
    event.stopPropagation();
    this.selectedCustomer = customer;
    this.activeCustomerMenuItems = this.getCustomerMenuItems(customer);
    this.customerActionMenu.toggle(event);
  }

  getCustomerMenuItems(customer: Customer): any[] {
    const items: any[] = [
      {
        label: 'View Details',
        icon: 'pi pi-eye',
        command: () => this.viewCustomer(customer),
      },
    ];

    if (!customer.do_not_disturb) {
      items.push({
        label: 'Send Message',
        icon: 'pi pi-send',
        command: () => this.openMessageDialog(customer),
      });
    }

    return items;
  }

  openMessageDialog(customer: Customer): void {
    this.selectedCustomer = customer;
    this.messageText = '';
    // Select first opted channel
    if (customer.opt_in_whatsapp) this.messageChannel = 'whatsapp';
    else if (customer.opt_in_sms) this.messageChannel = 'sms';
    else if (customer.opt_in_email) this.messageChannel = 'email';
    else if (customer.opt_in_call) this.messageChannel = 'voice';
    else this.messageChannel = 'whatsapp';

    this.showMessageDialog = true;
  }

  sendMessage(): void {
    if (!this.selectedCustomer || !this.messageText.trim()) return;
    this.sendingMessage = true;
    this.customerService.sendMessage(this.selectedCustomer.id, {
      channel: this.messageChannel,
      message: this.messageText.trim(),
    }).subscribe({
      next: () => {
        this.sendingMessage = false;
        this.showMessageDialog = false;
        this.messageText = '';
        this.messageService.add({
          severity: 'success',
          summary: 'Message Sent',
          detail: `Outreach sent via ${this.messageChannel}.`,
        });
      },
      error: (err) => {
        this.sendingMessage = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Send Failed',
          detail: err.error?.detail || 'Failed to send outreach message.',
        });
      },
    });
  }

  getStageSeverity(stage: string | null | undefined): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
    const s = (stage || '').toLowerCase();
    const map: Record<string, 'success' | 'info' | 'warn' | 'danger' | 'secondary'> = {
      opportunity: 'info',
      new: 'info',
      active: 'success',
      customer: 'success',
      vip: 'warn',
      churned: 'danger',
    };
    return map[s] || 'secondary';
  }

  getSourceIcon(source: string | null | undefined): string {
    const s = (source || '').toLowerCase();
    const icons: Record<string, string> = {
      instagram: 'pi pi-instagram',
      facebook: 'pi pi-facebook',
      messenger: 'pi pi-comments',
      whatsapp: 'pi pi-whatsapp',
      linkedin: 'pi pi-linkedin',
      voice: 'pi pi-phone',
      call: 'pi pi-phone',
      web: 'pi pi-globe',
      email: 'pi pi-envelope',
      sms: 'pi pi-mobile',
    };
    return icons[s] || 'pi pi-share-alt';
  }

  formatCurrency(val: number | null | undefined, curr: string | null | undefined): string {
    const amount = Number(val ?? 0).toFixed(2);
    const currency = curr || 'INR';
    if (currency === 'INR') {
      return `₹${amount}`;
    }
    return `${amount} ${currency}`;
  }

  openCreateCustomerDialog(): void {
    this.showCreateDialog = true;
  }

  onCustomerCreated(): void {
    this.showCreateDialog = false;
    this.loadCustomers();
  }
}
