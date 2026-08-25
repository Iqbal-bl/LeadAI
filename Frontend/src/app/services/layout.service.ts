import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, combineLatest, map, Observable } from 'rxjs';
import { AuthService } from './auth.service';
import { Notification } from '../models/notification.models';
import { ROLE_COMPANY_ADMIN, ROLE_EMPLOYEE, ROLE_MANAGER } from '../shared/constants/role.constants';

export interface MenuItem {
  label: string;
  icon: string;
  routerLink: string;
  badge?: string;
  badgeSeverity?: 'success' | 'info' | 'warn' | 'danger' | 'secondary' | 'contrast';
  permission?: string;
}

export interface SidebarSection {
  title: string;
  items: MenuItem[];
}

@Injectable({
  providedIn: 'root'
})
export class LayoutService {
  private authService = inject(AuthService);

  // Layout states
  private sidebarCollapsedSubject = new BehaviorSubject<boolean>(false);
  public sidebarCollapsed$ = this.sidebarCollapsedSubject.asObservable();

  private mobileSidebarOpenSubject = new BehaviorSubject<boolean>(false);
  public mobileSidebarOpen$ = this.mobileSidebarOpenSubject.asObservable();

  private notificationPanelOpenSubject = new BehaviorSubject<boolean>(false);
  public notificationPanelOpen$ = this.notificationPanelOpenSubject.asObservable();

  // Role override for testing
  private roleOverrideSubject = new BehaviorSubject<string | null>(null);
  public roleOverride$ = this.roleOverrideSubject.asObservable();

  // Selected role stream (combines auth role with manual override)
  public currentRole$: Observable<string> = combineLatest([
    this.authService.currentUser$,
    this.roleOverride$
  ]).pipe(
    map(([user, override]) => {
      if (override) return override;
      const role = user?.role || 'agent';
      return role.toLowerCase();
    })
  );

  // Dynamic notifications state
  private notificationsSubject = new BehaviorSubject<Notification[]>([]);
  public notifications$ = this.notificationsSubject.asObservable();

  // Seed mock notifications per role
  private roleNotifications: Record<string, Notification[]> = {
    admin: [
      { id: 101, type: 'kb-update', title: 'System Security Audit', message: 'SOC2 compliance audit logs for July exported successfully.', time: '5 min ago', read: false, icon: 'pi pi-shield', severity: 'info' },
      { id: 102, type: 'ai-alert', title: 'High API Latency Alert', message: 'Auth Service latency spiked to 350ms (threshold 200ms).', time: '12 min ago', read: false, icon: 'pi pi-exclamation-triangle', severity: 'danger' },
      { id: 103, type: 'worker-activity', title: 'Database Backup Completed', message: 'Automated database snapshot backup-2026-08-03 completed.', time: '1 hour ago', read: true, icon: 'pi pi-database', severity: 'success' },
      { id: 104, type: 'new-lead', title: 'Enterprise Signup', message: 'Vertex Industries registered a new workspace (250 licenses).', time: '3 hours ago', read: true, icon: 'pi pi-arrow-circle-up', severity: 'success' }
    ],
    manager: [
      { id: 201, type: 'ai-alert', title: 'Manager Attention: Escalation', message: 'Agent Chris Johnson requested supervisor assistance on lead Robert Anderson call.', time: '3 min ago', read: false, icon: 'pi pi-users', severity: 'danger' },
      { id: 202, type: 'worker-activity', title: 'Agent Performance Target', message: 'Maria Santos completed 35 qualified calls, reaching 120% of daily quota.', time: '20 min ago', read: false, icon: 'pi pi-chart-line', severity: 'success' },
      { id: 203, type: 'worker-activity', title: 'Shift Handoff Complete', message: 'Evening shift team has clocked in. 8 agents active.', time: '2 hours ago', read: true, icon: 'pi pi-clock', severity: 'info' },
      { id: 204, type: 'kb-update', title: 'Pending Approval', message: 'Jordan Blake requested approval for custom pricing terms on Lead #832.', time: '4 hours ago', read: true, icon: 'pi pi-check-square', severity: 'warn' }
    ],
    agent: [
      { id: 301, type: 'new-lead', title: 'New Lead Assigned', message: 'Sarah Mitchell from TechCorp Solutions has been assigned to your queue.', time: 'Just now', read: false, icon: 'pi pi-user-plus', severity: 'info' },
      { id: 302, type: 'missed-call', title: 'Callback Scheduled', message: 'Scheduled callback for James Chen is due in 15 minutes.', time: '15 min ago', read: false, icon: 'pi pi-calendar', severity: 'warn' },
      { id: 303, type: 'ai-alert', title: 'Customer Replied', message: 'Emily Parker sent an email response: "Send the pricing options details ASAP."', time: '1 hour ago', read: true, icon: 'pi pi-envelope', severity: 'success' },
      { id: 304, type: 'missed-call', title: 'Missed Call Alert', message: 'You missed an inbound call from lead David Kumar.', time: '3 hours ago', read: true, icon: 'pi pi-phone-missed', severity: 'danger' }
    ],
    ai_operator: [
      { id: 401, type: 'ai-alert', title: 'AI Confidence Drop', message: 'AI Agent confidence fell to 71% on lead Sarah Mitchell call. Sentiment: Frustrated.', time: '2 min ago', read: false, icon: 'pi pi-exclamation-triangle', severity: 'warn' },
      { id: 402, type: 'kb-update', title: 'KB Indexing Completed', message: 'Product_Features_v3.2.pdf has been chunked and vectorized (142 nodes).', time: '10 min ago', read: false, icon: 'pi pi-book', severity: 'success' },
      { id: 403, type: 'kb-update', title: 'Prompt Deployed', message: 'Prompt version "Lead Qualification v2.4.1" promoted to production.', time: '1 hour ago', read: true, icon: 'pi pi-code', severity: 'info' },
      { id: 404, type: 'ai-alert', title: 'LLM Node Warning', message: 'Azure OpenAI endpoint EastUS reported 8.4% token throttling.', time: '5 hours ago', read: true, icon: 'pi pi-server', severity: 'danger' }
    ]
  };

  constructor() {
    // Populate notifications based on current role changes
    this.currentRole$.subscribe(role => {
      const normalizedRole = this.normalizeRole(role);
      const seed = this.roleNotifications[normalizedRole] || this.roleNotifications['agent'];
      // Deep copy to prevent mutating static seeds across role swaps
      this.notificationsSubject.next(seed.map(n => ({ ...n })));
    });
  }

  // Sidebar helpers
  public setSidebarCollapsed(collapsed: boolean): void {
    this.sidebarCollapsedSubject.next(collapsed);
  }

  public toggleSidebar(): void {
    this.sidebarCollapsedSubject.next(!this.sidebarCollapsedSubject.value);
  }

  // Mobile sidebar helpers
  public setMobileSidebarOpen(open: boolean): void {
    this.mobileSidebarOpenSubject.next(open);
  }

  // Notification Drawer helpers
  public setNotificationPanelOpen(open: boolean): void {
    this.notificationPanelOpenSubject.next(open);
  }

  public toggleNotificationPanel(): void {
    this.notificationPanelOpenSubject.next(!this.notificationPanelOpenSubject.value);
  }

  // Role override helpers
  public setRoleOverride(role: string | null): void {
    this.roleOverrideSubject.next(role);
  }

  public normalizeRole(role: string): string {
    const r = role.toLowerCase();
    if (r === 'admin' || r === 'platform_admin' || r === 'platform-admin' || r === ROLE_COMPANY_ADMIN || r === 'company-admin') {
      return 'admin';
    }
    if (r === ROLE_MANAGER) return 'manager';
    if (r === 'ai_operator' || r === 'ai operator' || r === 'ai-operator' || r === 'aioperator') {
      return 'ai_operator';
    }
    if (r === ROLE_EMPLOYEE || r === 'agent') {
      return 'agent';
    }
    return 'agent'; // default
  }

  // Notification manipulations
  public markAsRead(id: number): void {
    const list = this.notificationsSubject.value.map(n => {
      if (n.id === id) {
        return { ...n, read: true };
      }
      return n;
    });
    this.notificationsSubject.next(list);
  }

  public markAllAsRead(): void {
    const list = this.notificationsSubject.value.map(n => ({ ...n, read: true }));
    this.notificationsSubject.next(list);
  }

  public dismissNotification(id: number): void {
    const list = this.notificationsSubject.value.filter(n => n.id !== id);
    this.notificationsSubject.next(list);
  }

  public addNotification(note: Notification): void {
    const list = [note, ...this.notificationsSubject.value];
    this.notificationsSubject.next(list);
  }

  // Dynamic Navigation definitions based on active role
  public getSidebarMenu(role: string): SidebarSection[] {
    const normalized = this.normalizeRole(role);
    const sections: SidebarSection[] = [];

    // Core Section
    const coreItems: MenuItem[] = [
      { label: 'Dashboard', icon: 'pi pi-th-large', routerLink: '/client/dashboard' }
    ];

    if (normalized === 'admin' || normalized === 'manager' || normalized === 'agent') {
      coreItems.push({ label: 'Leads', icon: 'pi pi-users', routerLink: '/client/leads' });
      coreItems.push({ label: 'Conversations', icon: 'pi pi-comments', routerLink: '/client/conversations' });
    }

    sections.push({
      title: 'CORE SERVICES',
      items: coreItems
    });

    // AI & KB Section
    const aiItems: MenuItem[] = [];
    if (normalized === 'admin' || normalized === 'manager' || normalized === 'agent' || normalized === 'ai_operator') {
      aiItems.push({ label: 'AI Assistant', icon: 'pi pi-microchip-ai', routerLink: '/client/ai-assistant' });
      aiItems.push({ label: 'Knowledge Base', icon: 'pi pi-book', routerLink: '/client/knowledge-base' });
    }
    if (normalized === 'admin' || normalized === 'ai_operator') {
      aiItems.push({ label: 'Prompts', icon: 'pi pi-code', routerLink: '/client/prompts' });
    }

    if (aiItems.length > 0) {
      sections.push({
        title: 'AI WORKFLOWS',
        items: aiItems
      });
    }

    // Management & Admin Section
    const adminItems: MenuItem[] = [];
    if (normalized === 'admin' || normalized === 'manager') {
      adminItems.push({ label: 'Analytics', icon: 'pi pi-chart-bar', routerLink: '/client/analytics' });
      adminItems.push({ label: 'Team', icon: 'pi pi-user-edit', routerLink: '/client/team' });
      adminItems.push({ label: 'Lead Threshold', icon: 'pi pi-sliders-h', routerLink: '/client/lead-threshold', permission: 'settings.manage' });
    }

    // Settings is visible for all, but routes or values can change
    adminItems.push({ label: 'Settings', icon: 'pi pi-cog', routerLink: '/client/settings' });

    // Social Media Section
    sections.push({
      title: 'SOCIAL MEDIA',
      items: [
        { label: 'Social Media Analytics', icon: 'pi pi-chart-pie', routerLink: '/client/social-analytics' },
        { label: 'Create a Post', icon: 'pi pi-send', routerLink: '/client/create-post' }
      ]
    });

    // Outreach Section (Channels, Contact Lists, Campaigns)
    if (normalized === 'admin' || normalized === 'manager') {
      sections.push({
        title: 'OUTREACH',
        items: [
          { label: 'Channels', icon: 'pi pi-link', routerLink: '/client/channels', permission: 'channel.read' },
          { label: 'Contact Lists', icon: 'pi pi-list', routerLink: '/client/contact-lists', permission: 'campaign.manage' },
          { label: 'Campaigns', icon: 'pi pi-megaphone', routerLink: '/client/campaigns', permission: 'campaign.read' },
        ]
      });
    }

    // CRM Section (Customers)
    if (normalized === 'admin' || normalized === 'manager' || normalized === 'agent') {
      sections.push({
        title: 'CRM',
        items: [
          { label: 'Customers', icon: 'pi pi-id-card', routerLink: '/client/customers', permission: 'customer.read' },
        ]
      });
    }

    // Files Section (Documents)
    if (normalized === 'admin' || normalized === 'manager' || normalized === 'agent') {
      sections.push({
        title: 'FILES',
        items: [
          { label: 'Documents', icon: 'pi pi-folder-open', routerLink: '/client/documents', permission: 'file.read' },
        ]
      });
    }

    if (normalized === 'admin') {
      sections.push({
        title: 'MANAGEMENT',
        items: adminItems
      });

      // Special administrative shell links
      sections.push({
        title: 'ADMIN CENTER',
        items: [
          { label: 'Admin Dashboard', icon: 'pi pi-sliders-h', routerLink: '/admin/dashboard' },
          { label: 'Companies', icon: 'pi pi-building', routerLink: '/client/companies' }
        ]
      });
    } else {
      sections.push({
        title: 'MANAGEMENT',
        items: adminItems
      });
    }

    return sections;
  }
}
