import { Component, OnInit, OnDestroy, ViewChild } from '@angular/core';
import { Router } from '@angular/router';
import { Table } from 'primeng/table';
import { Subscription } from 'rxjs';
import { InboxService, InboxQueryParams } from '../../../services/inbox.service';
import { AuthService } from '../../../services/auth.service';
import { LeadService } from '../../../services/lead.service';
import { CustomerService } from '../../../services/customer.service';
import { ToastService } from '../../../shared/services/toast.service';
import { SharedModule } from '../../../shared/shared.module';

@Component({
  selector: 'app-lead-list',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './lead-list.component.html',
  styleUrl: './lead-list.component.scss'
})
export class LeadListComponent implements OnInit, OnDestroy {
  @ViewChild('dt') dt!: Table;

  leads: any[] = [];
  filteredLeads: any[] = [];
  selectedLeads: any[] = [];
  loading = false;

  // Filters
  selectedChannel = '';
  selectedStatus = '';
  selectedPriority = '';
  showAllLeads = true;
  searchText = '';

  channelOptions = [
    { label: 'All Channels', value: '' },
    { label: 'Web Chat', value: 'web' },
    { label: 'WhatsApp', value: 'whatsapp' },
    { label: 'Facebook Messenger', value: 'messenger' },
    { label: 'Instagram', value: 'instagram' },
    { label: 'SMS', value: 'sms' },
    { label: 'Email', value: 'email' },
    { label: 'Voice Dialler', value: 'voice' },
  ];

  statusOptions = [
    { label: 'All Statuses', value: '' },
    { label: 'New', value: 'New' },
    { label: 'Assigned', value: 'Assigned' },
    { label: 'Follow-up', value: 'Follow-up' },
    { label: 'Interested', value: 'Interested' },
    { label: 'Negotiation', value: 'Negotiation' },
    { label: 'Won', value: 'Won' },
    { label: 'Lost', value: 'Lost' },
    { label: 'Closed', value: 'Closed' },
  ];

  priorityOptions = [
    { label: 'All Priorities', value: '' },
    { label: 'High', value: 'High' },
    { label: 'Medium', value: 'Medium' },
    { label: 'Low', value: 'Low' },
  ];

  private inboxMsgSub?: Subscription;
  private companySub?: Subscription;

  constructor(
    private router: Router,
    private inboxService: InboxService,
    private authService: AuthService,
    private leadService: LeadService,
    private customerService: CustomerService,
    private toastService: ToastService,
  ) {}

  ngOnInit(): void {
    this.loading = true;
    this.loadLeads();
    this.setupWebsocket();
  }

  ngOnDestroy(): void {
    if (this.inboxMsgSub) {
      this.inboxMsgSub.unsubscribe();
    }
    if (this.companySub) {
      this.companySub.unsubscribe();
    }
  }

  setupWebsocket(): void {
    this.companySub = this.authService.selectedCompanyId$.subscribe({
      next: (clientId: string | null) => {
        if (this.inboxMsgSub) {
          this.inboxMsgSub.unsubscribe();
        }

        if (clientId) {
          this.inboxMsgSub = this.leadService.inboxMessages$.subscribe({
            next: () => {
              this.loadLeads();
            },
            error: (err: any) => {
              console.warn('Inbox WS error:', err);
            }
          });
        }
      }
    });
  }

  loadLeads(): void {
    this.loading = true;
    const params: InboxQueryParams = {};
    if (this.selectedChannel) {
      params.channel = this.selectedChannel;
    }
    if (!this.showAllLeads) {
      params.above_threshold = true;
    }

    this.inboxService.getInbox(params).subscribe({
      next: (response: any) => {
        this.leads = (response?.items || []).map((item: any) => {
          const score = item.lead?.score || 0;
          return {
            id: item.id,
            name: item.customer_name || item.customer_ref || 'Unknown Lead',
            email: item.customer_name
              ? `${item.customer_name.toLowerCase().replace(/\s+/g, '')}@example.com`
              : 'info@example.com',
            phone: item.customer_phone_masked || '',
            company: item.client_id || 'N/A',
            address: 'N/A',
            industry: item.lead?.product || 'N/A',
            tags: item.lead?.interest ? [item.lead.interest] : [],
            leadScore: score,
            priority: score > 75 ? 'High' : score > 45 ? 'Medium' : 'Low',
            status:
              item.status === 'needs_human'
                ? 'Assigned'
                : item.status === 'open'
                ? 'New'
                : item.status === 'closed'
                ? 'Closed'
                : 'New',
            source: item.channel || 'web',
            assignedTo: item.assigned_user_email || 'AI Assistant',
            createdAt: item.created_at || '',
            updatedAt: item.last_message_at || item.created_at || '',
            summary: item.summary || 'Lead inquiry details.',
            avatar: '',
            leadStatus: item.lead?.status || '',
            aboveThreshold: item.above_threshold,
          };
        });
        this.applyLocalFilters();
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      }
    });
  }

  onFilterChange(): void {
    if (this.selectedChannel || !this.showAllLeads) {
      this.loadLeads();
    } else {
      this.applyLocalFilters();
    }
  }

  applyLocalFilters(): void {
    let result = [...this.leads];

    if (this.selectedChannel) {
      result = result.filter(
        (lead) => lead.source?.toLowerCase() === this.selectedChannel.toLowerCase()
      );
    }

    if (this.selectedStatus) {
      result = result.filter(
        (lead) => lead.status?.toLowerCase() === this.selectedStatus.toLowerCase()
      );
    }

    if (this.selectedPriority) {
      result = result.filter(
        (lead) => lead.priority?.toLowerCase() === this.selectedPriority.toLowerCase()
      );
    }

    this.filteredLeads = result;
  }

  getLeadMenuItems(lead: any): any[] {
    const items: any[] = [
      {
        label: 'View Details',
        icon: 'pi pi-eye',
        command: () => this.viewLead(lead),
      },
    ];

    if (lead.leadStatus === 'qualified') {
      items.push({
        label: 'Convert to Customer',
        icon: 'pi pi-user-plus',
        command: () => this.convertToCustomer(lead),
      });
    }

    return items;
  }

  convertToCustomer(lead: any): void {
    this.customerService.convertLead({ lead_id: lead.id }).subscribe({
      next: () => {
        this.toastService.success(
          `${lead.name} has been promoted to a Customer.`,
          'Lead Converted',
        );
        lead.leadStatus = 'converted';
        this.loadLeads();
      },
      error: (err) => {
        this.toastService.error(
          err?.error?.detail || 'Failed to convert lead to customer.',
          'Conversion Failed',
        );
      },
    });
  }

  onGlobalFilter(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.dt.filterGlobal(value, 'contains');
  }

  viewLead(lead: any): void {
    if (this.router.url.includes('/admin/')) {
      this.router.navigate(['/admin/leads/detail', lead.id]);
    } else {
      this.router.navigate(['/client/leads', lead.id]);
    }
  }

  getStatusSeverity(status: string): 'success' | 'secondary' | 'info' | 'warn' | 'danger' | 'contrast' | undefined {
    const map: Record<string, 'success' | 'secondary' | 'info' | 'warn' | 'danger' | 'contrast'> = {
      'New': 'info',
      'Assigned': 'secondary',
      'Follow-up': 'warn',
      'Interested': 'success',
      'Negotiation': 'contrast',
      'Won': 'success',
      'Lost': 'danger',
      'Closed': 'secondary',
      'Completed': 'success',
      'In Progress': 'info',
    };
    return map[status] || 'info';
  }

  getPrioritySeverity(priority: string): 'danger' | 'warn' | 'info' | 'secondary' {
    const map: Record<string, 'danger' | 'warn' | 'info' | 'secondary'> = {
      High: 'danger',
      Medium: 'warn',
      Low: 'info',
    };
    return map[priority] || 'secondary';
  }

  getChannelIcon(channel: string): string {
    const icons: Record<string, string> = {
      whatsapp: 'pi pi-whatsapp',
      messenger: 'pi pi-facebook',
      instagram: 'pi pi-instagram',
      sms: 'pi pi-mobile',
      email: 'pi pi-envelope',
      voice: 'pi pi-phone',
      web: 'pi pi-desktop',
    };
    return icons[channel?.toLowerCase()] || 'pi pi-comment';
  }

  getChannelColor(channel: string): string {
    const colors: Record<string, string> = {
      whatsapp: '#25D366',
      messenger: '#0084FF',
      instagram: '#E4405F',
      sms: '#8b5cf6',
      email: '#ef4444',
      voice: '#f59e0b',
      web: '#3b82f6',
    };
    return colors[channel?.toLowerCase()] || '#6b7280';
  }

  getInitials(name: string): string {
    if (!name) return 'L';
    return name
      .split(' ')
      .filter((n) => !!n)
      .map((n) => n[0])
      .join('')
      .toUpperCase()
      .slice(0, 2);
  }

  getAvatarColor(id: any): string {
    const colors = [
      '#6366f1',
      '#8b5cf6',
      '#ec4899',
      '#f43f5e',
      '#f59e0b',
      '#22c55e',
      '#06b6d4',
      '#3b82f6',
    ];
    const numId = typeof id === 'number' ? id : (id?.toString().charCodeAt(0) || 0);
    return colors[numId % colors.length];
  }

  exportCSV(): void {
    this.dt.exportCSV();
  }
}
