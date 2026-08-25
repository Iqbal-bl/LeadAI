import {
  Component,
  EventEmitter,
  Input,
  Output,
  OnInit,
  OnDestroy,
  inject,
} from '@angular/core';
import { Router, NavigationEnd } from '@angular/router';
import { filter } from 'rxjs/operators';
import { Subscription } from 'rxjs';
import { MenuItem } from 'primeng/api';
import { ThemeService } from '../../shared/services/theme.service';
import { AuthService } from '../../services/auth.service';
import { LayoutService } from '../../services/layout.service';

import { SharedModule } from '../../shared/shared.module';

@Component({
  selector: 'app-toolbar',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './toolbar.component.html',
  styleUrl: './toolbar.component.scss',
})
export class ToolbarComponent implements OnInit, OnDestroy {
  @Input() sidebarCollapsed = false;
  @Output() toggleSidebar = new EventEmitter<void>();

  auth = inject(AuthService);
  layoutService = inject(LayoutService);

  isDarkMode = false;
  showSearch = false;
  searchQuery = '';
  searchResults: any[] = [];
  // allSearchItems = GLOBAL_SEARCH_ITEMS;
  unreadNotifications = 0;
  breadcrumbItems: MenuItem[] = [];
  breadcrumbHome: MenuItem = { icon: 'pi pi-home', routerLink: '/dashboard' };
  userNameAvatar: string = '';

  userMenuItems: MenuItem[] = [
    { label: 'Profile', icon: 'pi pi-user', command: () => {} },
    { label: 'Settings', icon: 'pi pi-cog', routerLink: '/settings' },
    { separator: true },
    {
      label: 'Logout',
      icon: 'pi pi-sign-out',
      command: () => {
        this.auth.logout();
      },
    },
  ];

  // Developer Role Switcher configuration
  roleOptions = [
    { label: 'Platform Admin', value: 'admin' },
    { label: 'Manager', value: 'manager' },
    { label: 'Agent', value: 'agent' },
    { label: 'AI Operator', value: 'ai_operator' },
  ];
  currentRole = 'agent';

  // Company switching for Admins/Managers
  companies: any[] = [];
  selectedCompanyId = '';

  // Agent Specific Data
  agentStatuses = [
    {
      label: 'Available',
      value: 'available',
      icon: 'pi pi-circle-fill text-green-500',
    },
    { label: 'Away', value: 'away', icon: 'pi pi-circle-fill text-yellow-500' },
    { label: 'Busy', value: 'busy', icon: 'pi pi-circle-fill text-red-500' },
  ];
  selectedAgentStatus = 'available';
  callsCompleted = 12;
  callsTarget = 20;

  // AI Operator Specific Data
  activeAiCalls = 24;
  aiAccuracy = 94.2;

  private subs = new Subscription();

  constructor(
    private themeService: ThemeService,
    private router: Router,
  ) {}

  ngOnInit(): void {
    // Theme monitor
    this.subs.add(
      this.themeService.darkMode$.subscribe((dark) => (this.isDarkMode = dark)),
    );

    this.auth.currentUser$.subscribe((user) => {
      this.userNameAvatar = user?.full_name || user?.email?.split('@')[0] || 'User';
    });

    // Dynamic Notifications count monitor
    this.subs.add(
      this.layoutService.notifications$.subscribe((notes) => {
        this.unreadNotifications = notes.filter((n) => !n.read).length;
      }),
    );

    // Current active role monitor
    this.subs.add(
      this.layoutService.currentRole$.subscribe((role) => {
        this.currentRole = this.layoutService.normalizeRole(role);
      }),
    );

    // Company switcher initialization
    this.subs.add(
      this.auth.currentUser$.subscribe((user) => {
        this.companies = user?.accessible_companies || [];
      }),
    );
    this.subs.add(
      this.auth.selectedCompanyId$.subscribe((id) => {
        this.selectedCompanyId = id || '';
      }),
    );

    // Update breadcrumbs on navigation
    this.subs.add(
      this.router.events
        .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
        .subscribe((event) => {
          this.updateBreadcrumb(event.urlAfterRedirects);
        }),
    );

    this.updateBreadcrumb(this.router.url);
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  toggleTheme(): void {
    this.themeService.toggleTheme();
  }

  openSearch(): void {
    this.showSearch = true;
    this.searchQuery = '';
    this.searchResults = [];
  }

  closeSearch(): void {
    this.showSearch = false;
  }

  onSearchInput(event: any): void {
    const query = (event.query || '').toLowerCase();
    if (query.length < 2) {
      this.searchResults = [];
      return;
    }
    // this.searchResults = this.allSearchItems.filter(item =>
    //   item.label.toLowerCase().includes(query) ||
    //   item.detail.toLowerCase().includes(query) ||
    //   item.type.toLowerCase().includes(query)
    // ).slice(0, 10);
  }

  onSearchSelect(event: any): void {
    if (event?.route) {
      this.router.navigate([event.route]);
    }
    this.closeSearch();
  }

  toggleNotifications(): void {
    this.layoutService.toggleNotificationPanel();
  }

  onRoleChange(event: any): void {
    this.layoutService.setRoleOverride(event.value);
  }

  onCompanyChange(companyId: string): void {
    this.auth.setSelectedCompanyId(companyId);
  }

  private updateBreadcrumb(url: string): void {
    // const match = Object.keys(BREADCRUMB_MAP).find(key => url.startsWith(key));
    // if (match) {
    // this.breadcrumbItems = BREADCRUMB_MAP[match].map(item => ({
    //   label: item.label,
    //   icon: item.icon,
    // }));
    // } else {
    // this.breadcrumbItems = [{ label: 'Dashboard', icon: 'pi pi-th-large' }];
    // }
  }

  getTypeSeverity(
    type: string,
  ):
    | 'success'
    | 'secondary'
    | 'info'
    | 'warn'
    | 'danger'
    | 'contrast'
    | undefined {
    const map: {
      [key: string]:
        | 'success'
        | 'secondary'
        | 'info'
        | 'warn'
        | 'danger'
        | 'contrast';
    } = {
      Lead: 'info',
      Team: 'success',
      Document: 'warn',
      Conversation: 'secondary',
      Prompt: 'contrast',
    };
    return map[type] || 'info';
  }

  onKeydown(event: KeyboardEvent): void {
    if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
      event.preventDefault();
      this.openSearch();
    }
    if (event.key === 'Escape') {
      this.closeSearch();
    }
  }
}
