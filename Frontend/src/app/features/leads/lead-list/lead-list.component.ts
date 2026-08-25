import { Component, OnInit, OnDestroy, ViewChild } from '@angular/core';
import { Router } from '@angular/router';
import { Table } from 'primeng/table';
import { Subscription } from 'rxjs';
import { InboxService } from '../../../services/inbox.service';
import { AuthService } from '../../../services/auth.service';
import { LeadService } from '../../../services/lead.service';
import { CustomerService } from '../../../services/customer.service';
import { ToastService } from '../../../shared/services/toast.service';

import { SharedModule } from '../../../shared/shared.module';
import { Lead } from '../../../models/lead.models';

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
  selectedLeads: any[] = [];
  loading = false;

  statuses = [
    { label: 'New', value: 'New' },
    { label: 'Assigned', value: 'Assigned' },
    { label: 'Follow-up', value: 'Follow-up' },
    { label: 'Interested', value: 'Interested' },
    { label: 'Negotiation', value: 'Negotiation' },
    { label: 'Won', value: 'Won' },
    { label: 'Lost', value: 'Lost' },
    { label: 'Closed', value: 'Closed' },
  ];

  priorities = [
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
            next: (data: any) => {
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
    this.inboxService.getInbox().subscribe({
      next: (response: any) => {
        this.leads = response.items.map((item: any, idx: number) => {
          const score = item.lead?.score || 0;
          return {
            id: item.id,
            name: item.customer_name || item.customer_ref,
            email: item.customer_name ? `${item.customer_name.toLowerCase().replace(/\s+/g, '')}@example.com` : 'info@example.com',
            phone: item.customer_phone_masked,
            company: item.client_id || 'N/A',
            address: 'N/A',
            industry: item.lead?.product || 'N/A',
            tags: item.lead?.interest ? [item.lead.interest] : [],
            leadScore: score,
            priority: score > 75 ? 'High' : score > 45 ? 'Medium' : 'Low',
            status: item.status === 'needs_human' ? 'Assigned' : item.status === 'open' ? 'New' : 'Closed',
            source: item.channel || 'web',
            assignedTo: item.assigned_user_email || 'Unassigned',
            createdAt: item.created_at,
            updatedAt: item.last_message_at,
            avatar: '',
            leadStatus: item.lead?.status || '',
          };
        });
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      }
    });
  }

  getLeadMenuItems(lead: any): any[] {
    const items = [
      {
        label: 'View',
        icon: 'pi pi-eye',
        command: () => this.viewLead(lead),
      },
    ];

    // if (lead.leadStatus === 'qualified') {
    //   items.push({
    //     label: 'Convert to Customer',
    //     icon: 'pi pi-user-plus',
    //     command: () => this.convertToCustomer(lead)
    //   });
    // }

    // items.push({ separator: true } as any);
    // items.push({
    //   label: 'Delete',
    //   icon: 'pi pi-trash',
    //   styleClass: 'text-danger-500',
    // } as any);

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

  viewLead(lead: Lead): void {
    this.router.navigate(['/client/leads', lead.id]);
  }

  getStatusSeverity(status: string): "success" | "secondary" | "info" | "warn" | "danger" | "contrast" | undefined {
    const map: Record<string, "success" | "secondary" | "info" | "warn" | "danger" | "contrast"> = {
      'New': 'info',
      'Assigned': 'secondary',
      'Follow-up': 'warn',
      'Interested': 'success',
      'Negotiation': 'contrast',
      'Won': 'success',
      'Lost': 'danger',
      'Closed': 'secondary',
    };
    return map[status] || 'info';
  }

  getPriorityClass(priority: string): string {
    const map: Record<string, string> = {
      'High': 'text-danger-600 bg-danger-50 dark:text-danger-500 dark:bg-danger-500/10',
      'Medium': 'text-warning-600 bg-warning-50 dark:text-warning-500 dark:bg-warning-500/10',
      'Low': 'text-info-600 bg-info-50 dark:text-info-500 dark:bg-info-500/10',
    };
    return map[priority] || '';
  }

  getInitials(name: string): string {
    return name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
  }

  getAvatarColor(id: number): string {
    const colors = ['#6366f1', '#8b5cf6', '#ec4899', '#f43f5e', '#f59e0b', '#22c55e', '#06b6d4', '#3b82f6'];
    return colors[id % colors.length];
  }

  exportCSV(): void {
    this.dt.exportCSV();
  }
}
