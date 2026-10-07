import { Component, OnInit, OnDestroy, ViewChild } from '@angular/core';
import { Router, ActivatedRoute } from '@angular/router';
import { Subject, Subscription } from 'rxjs';
import { debounceTime, distinctUntilChanged } from 'rxjs/operators';
import { Table, TableLazyLoadEvent } from 'primeng/table';
import { Menu } from 'primeng/menu';
import {
  InboxService,
  InboxQueryParams,
} from '../../../services/inbox.service';
import { AuthService } from '../../../services/auth.service';
import { LeadService } from '../../../services/lead.service';
import { CustomerService } from '../../../services/customer.service';
import { ProductService } from '../../../services/product.service';
import { ToastService } from '../../../shared/services/toast.service';
import { SharedModule } from '../../../shared/shared.module';
import { CLIENT_PERMISSIONS } from '../../../modules/client/constants/permission.constants';

@Component({
  selector: 'app-lead-list',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './lead-list.component.html',
  styleUrl: './lead-list.component.scss',
})
export class LeadListComponent implements OnInit, OnDestroy {
  @ViewChild('dt') dt!: Table;
  @ViewChild('leadActionMenu') leadActionMenu!: Menu;
  PERMISSIONS = CLIENT_PERMISSIONS;

  leads: any[] = [];
  selectedLeads: any[] = [];
  loading = false;
  totalRecords = 0;
  currentPage = 1;
  pageSize = 10;

  // Filters
  selectedChannel = '';
  selectedStatus = '';
  selectedPriority = '';
  selectedLeadSource = '';
  selectedProduct = '';
  showAllLeads = true;
  searchText = '';

  productOptions: { label: string; value: string }[] = [
    { label: 'All Products', value: '' },
  ];

  leadSourceOptions = [
    { label: 'All sources', value: '' },
    { label: 'Inbound', value: 'inbound' },
    { label: 'From import', value: 'import' },
    { label: 'From broadcast', value: 'broadcast' },
  ];

  private searchSubject = new Subject<string>();

  channelOptions = [
    { label: 'All Channels', value: '' },
    { label: 'Web Chat', value: 'web' },
    { label: 'WhatsApp', value: 'whatsapp' },
    { label: 'Facebook Messenger', value: 'messenger' },
    { label: 'Instagram', value: 'instagram' },
    { label: 'LinkedIn', value: 'linkedin' },
    { label: 'SMS', value: 'sms' },
    { label: 'Email', value: 'email' },
    { label: 'Voice Dialler', value: 'voice' },
  ];

  statusOptions = [
    { label: 'All Statuses', value: '' },
    { label: 'Hot', value: 'hot' },
    { label: 'Warm', value: 'warm' },
    { label: 'Cold', value: 'cold' },
    { label: 'Qualified', value: 'qualified' },
    { label: 'Needs Human', value: 'needs_human' },
    { label: 'Assigned', value: 'assigned' },
    { label: 'Open', value: 'open' },
    { label: 'Closed', value: 'closed' },
    { label: 'Lost', value: 'lost' },
  ];

  priorityOptions = [
    { label: 'All Priorities', value: '' },
    { label: 'High', value: 'High' },
    { label: 'Medium', value: 'Medium' },
    { label: 'Low', value: 'Low' },
  ];

  private inboxMsgSub?: Subscription;
  private companySub?: Subscription;
  private searchSub?: Subscription;

  constructor(
    private router: Router,
    private route: ActivatedRoute,
    private inboxService: InboxService,
    private authService: AuthService,
    private leadService: LeadService,
    private customerService: CustomerService,
    private productService: ProductService,
    private toastService: ToastService,
  ) {}

  ngOnInit(): void {
    this.route.queryParams.subscribe((params) => {
      if (params['product']) {
        this.selectedProduct = params['product'];
      }
    });
    this.setupSearchDebounce();
    this.loadProducts();
    this.loadLeads();
    this.setupWebsocket();
  }

  loadProducts(): void {
    this.productService.getProducts().subscribe({
      next: (res) => {
        const items = res?.items || [];
        const options = items.map((p) => ({
          label: p.product_name,
          value: p.product_name,
        }));
        this.productOptions = [
          { label: 'All Products', value: '' },
          ...options,
        ];
      },
      error: (err) => {
        console.warn('Failed to load products for filter:', err);
      },
    });
  }

  ngOnDestroy(): void {
    if (this.inboxMsgSub) {
      this.inboxMsgSub.unsubscribe();
    }
    if (this.companySub) {
      this.companySub.unsubscribe();
    }
    if (this.searchSub) {
      this.searchSub.unsubscribe();
    }
  }

  setupSearchDebounce(): void {
    this.searchSub = this.searchSubject
      .pipe(debounceTime(350), distinctUntilChanged())
      .subscribe((term) => {
        this.searchText = term;
        this.currentPage = 1;
        if (this.dt) {
          this.dt.first = 0;
        }
        this.loadLeads();
      });
  }

  onSearchInput(event: Event): void {
    const value = (event.target as HTMLInputElement).value || '';
    this.searchSubject.next(value);
  }

  setupWebsocket(): void {
    this.companySub = this.authService.selectedCompanyId$.subscribe({
      next: (clientId: string | null) => {
        if (this.inboxMsgSub) {
          this.inboxMsgSub.unsubscribe();
        }

        if (clientId) {
          this.loadProducts();
          this.inboxMsgSub = this.leadService.inboxMessages$.subscribe({
            next: () => {
              this.loadLeads();
            },
            error: (err: any) => {
              console.warn('Inbox WS error:', err);
            },
          });
        }
      },
    });
  }

  loadLeads(): void {
    this.loading = true;
    const params: InboxQueryParams = {
      page: this.currentPage,
      page_size: this.pageSize,
    };

    if (this.selectedChannel) {
      params.channel = this.selectedChannel;
    }

    if (this.selectedStatus) {
      const st = this.selectedStatus.toLowerCase();
      if (['cold', 'warm', 'hot', 'qualified', 'lost'].includes(st)) {
        params.lead_status = st;
      } else {
        params.status = st;
      }
    }

    if (this.selectedPriority) {
      if (this.selectedPriority === 'High') {
        params.min_score = 75;
      } else if (this.selectedPriority === 'Medium') {
        params.min_score = 45;
      }
    }

    if (this.selectedLeadSource) {
      params.lead_source = this.selectedLeadSource;
    }

    if (this.selectedProduct) {
      params.product = this.selectedProduct;
    }

    if (this.searchText && this.searchText.trim()) {
      params.search = this.searchText.trim();
    }

    if (!this.showAllLeads) {
      params.above_threshold = true;
    }

    this.inboxService.getInbox(params).subscribe({
      next: (response: any) => {
        this.totalRecords = response?.total_items || response?.total || 0;
        let mapped = (response?.items || []).map((item: any) => {
          const score = item.lead?.score || 0;
          const prod = item.lead?.product || 'N/A';
          return {
            id: item.id,
            name: item.customer_name || item.customer_ref || 'Unknown Lead',
            email: item.customer_email || item.email || '',
            phone: item.customer_phone_masked || item.phone || '',
            customerRef: item.customer_ref || '',
            company: item.client_id || 'N/A',
            address: 'N/A',
            industry: prod,
            product: prod,
            tags: item.lead?.interest ? [item.lead.interest] : [],
            leadScore: score,
            priority: score > 75 ? 'High' : score > 45 ? 'Medium' : 'Low',
            status: item.lead?.converted_account_id
              ? 'CONVERTED'
              : item.lead?.status
                ? item.lead.status.toUpperCase()
                : item.status
                  ? item.status.toUpperCase()
                  : 'NEW',
            source: item.channel || 'web',
            assignedTo: item.assigned_user_email || 'AI Assistant',
            createdAt: item.created_at || '',
            updatedAt: item.last_message_at || item.created_at || '',
            summary: item.summary || 'Lead inquiry details.',
            avatar: '',
            leadStatus: item.lead?.status || '',
            aboveThreshold: item.above_threshold,
            convertedAccountId: item.lead?.converted_account_id || null,
            convertedAt: item.lead?.converted_at || null,
          };
        });

        if (this.selectedProduct) {
          const target = this.selectedProduct.toLowerCase().trim();
          if (target === 'unknown') {
            mapped = mapped.filter(
              (l: any) => !l.product || l.product === 'N/A' || l.product.toLowerCase() === 'unknown'
            );
          } else {
            mapped = mapped.filter(
              (l: any) => l.product && l.product.toLowerCase().trim() === target
            );
          }
        }

        this.leads = mapped;
        this.loading = false;
      },
      error: () => {
        this.leads = [];
        this.totalRecords = 0;
        this.loading = false;
      },
    });
  }

  onFilterChange(): void {
    this.currentPage = 1;
    if (this.dt) {
      this.dt.first = 0;
    }
    this.loadLeads();
  }

  clearFilters(): void {
    this.selectedChannel = '';
    this.selectedStatus = '';
    this.selectedPriority = '';
    this.selectedLeadSource = '';
    this.selectedProduct = '';
    this.searchText = '';
    this.showAllLeads = true;
    this.currentPage = 1;
    if (this.dt) {
      this.dt.first = 0;
    }
    this.loadLeads();
  }

  hasActiveFilters(): boolean {
    return !!(
      this.selectedChannel ||
      this.selectedStatus ||
      this.selectedPriority ||
      this.selectedLeadSource ||
      this.selectedProduct ||
      this.searchText
    );
  }

  onLazyLoad(event: TableLazyLoadEvent): void {
    const page =
      Math.floor((event.first || 0) / (event.rows || this.pageSize)) + 1;
    this.currentPage = page;
    this.pageSize = event.rows || this.pageSize;
    this.loadLeads();
  }

  activeLeadMenuItems: any[] = [];

  openLeadMenu(event: Event, lead: any): void {
    event.stopPropagation();
    this.activeLeadMenuItems = this.getLeadMenuItems(lead);
    this.leadActionMenu.toggle(event);
  }

  getLeadMenuItems(lead: any): any[] {
    const items: any[] = [
      {
        label: 'View Details',
        icon: 'pi pi-eye',
        command: () => this.viewLead(lead),
      },
    ];

    const canConvert =
      this.authService.hasPermission('customer.manage') ||
      ['admin', 'company_admin', 'companyadmin', 'manager', 'platform_admin', 'superadmin'].includes(
        (this.authService.getUserRole() || '').toLowerCase()
      );
    const isUnconverted = !lead.convertedAccountId;

    if (canConvert && isUnconverted) {
      items.push({
        label: 'Convert to Customer',
        icon: 'pi pi-user-plus',
        command: () => this.openConvertDialog(lead),
      });
    }

    if (lead.convertedAccountId) {
      items.push({
        label: 'View Customer Account',
        icon: 'pi pi-user',
        command: () => this.router.navigate(['/client/customers', lead.convertedAccountId]),
      });
    }

    return items;
  }

  showConvertDialog = false;
  converting = false;
  convertPayload: {
    conversation_id: string;
    lead_id: string;
    owner_email: string;
    stage: string;
    value: number | null;
    notes: string;
    leadName?: string;
  } = {
    conversation_id: '',
    lead_id: '',
    owner_email: '',
    stage: 'customer',
    value: null,
    notes: '',
  };

  stageOptions = [
    { label: 'Customer', value: 'customer' },
    { label: 'Opportunity', value: 'opportunity' },
    { label: 'Lead', value: 'lead' },
    { label: 'Won', value: 'won' },
  ];

  openConvertDialog(lead: any): void {
    if (!lead) return;
    const convId = String(lead.id || lead.conversation_id || '');

    let numericValue: number | null = null;
    if (lead.budget) {
      const match = String(lead.budget).match(/[\d,.]+/);
      if (match) {
        const val = parseFloat(match[0].replace(/,/g, ''));
        if (!isNaN(val)) numericValue = val;
      }
    }

    const initialEmail =
      lead.assigned_user_email ||
      (lead.assignedTo && lead.assignedTo.includes('@') ? lead.assignedTo : '');

    this.convertPayload = {
      conversation_id: convId,
      lead_id: convId,
      owner_email: initialEmail,
      stage: 'customer',
      value: numericValue,
      notes: lead.summary ? `Summary: ${lead.summary.slice(0, 150)}...` : '',
      leadName: lead.name || 'Lead',
    };
    this.showConvertDialog = true;
  }

  submitConvert(): void {
    if (!this.convertPayload.conversation_id) return;
    this.converting = true;

    const emailVal = this.convertPayload.owner_email?.trim();
    const validEmail = emailVal && emailVal.includes('@') ? emailVal : null;

    const payload = {
      conversation_id: this.convertPayload.conversation_id,
      lead_id: this.convertPayload.lead_id,
      owner_email: validEmail,
      stage: this.convertPayload.stage || 'customer',
      value:
        this.convertPayload.value != null
          ? Number(this.convertPayload.value)
          : null,
      notes: this.convertPayload.notes
        ? this.convertPayload.notes.trim()
        : null,
    };

    this.customerService.convertLead(payload).subscribe({
      next: () => {
        this.converting = false;
        this.showConvertDialog = false;
        this.toastService.success(
          `${this.convertPayload.leadName || 'Lead'} has been successfully promoted to a Customer.`,
          'Lead Converted',
        );
        this.loadLeads();
      },
      error: (err) => {
        this.converting = false;
        this.toastService.error(
          err?.error?.detail ||
            err?.message ||
            'Failed to convert lead to customer.',
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

  getStatusSeverity(
    status: string,
  ):
    | 'success'
    | 'secondary'
    | 'info'
    | 'warn'
    | 'danger'
    | 'contrast'
    | undefined {
    const map: Record<
      string,
      'success' | 'secondary' | 'info' | 'warn' | 'danger' | 'contrast'
    > = {
      CONVERTED: 'success',
      Converted: 'success',
      QUALIFIED: 'info',
      Qualified: 'info',
      HOT: 'danger',
      Hot: 'danger',
      WARM: 'warn',
      Warm: 'warn',
      COLD: 'secondary',
      Cold: 'secondary',
      New: 'info',
      Assigned: 'secondary',
      'Follow-up': 'warn',
      Interested: 'success',
      Negotiation: 'contrast',
      Won: 'success',
      Lost: 'danger',
      Closed: 'secondary',
      Completed: 'success',
      'In Progress': 'info',
    };
    return map[status] || 'info';
  }

  getPrioritySeverity(
    priority: string,
  ): 'danger' | 'warn' | 'info' | 'secondary' {
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
      linkedin: 'pi pi-linkedin',
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
      linkedin: '#0A66C2',
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
    const numId =
      typeof id === 'number' ? id : id?.toString().charCodeAt(0) || 0;
    return colors[numId % colors.length];
  }

  exportCSV(): void {
    this.dt.exportCSV();
  }

  goToImport(): void {
    this.router.navigate(['/client/leads/import']);
  }
}
