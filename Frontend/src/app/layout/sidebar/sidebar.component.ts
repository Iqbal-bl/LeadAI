import {
  Component,
  EventEmitter,
  Input,
  Output,
  OnInit,
  OnDestroy,
  OnChanges,
  SimpleChanges,
  inject,
} from '@angular/core';
import { Router, NavigationEnd } from '@angular/router';
import { filter } from 'rxjs/operators';
import { Subscription } from 'rxjs';
import { LayoutService, SidebarSection } from '../../services/layout.service';

import { SharedModule } from '../../shared/shared.module';

@Component({
  selector: 'app-sidebar',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './sidebar.component.html',
  styleUrl: './sidebar.component.scss',
})
export class SidebarComponent implements OnInit, OnChanges, OnDestroy {
  @Input() collapsed = false;
  @Output() toggle = new EventEmitter<boolean>();
  @Input() navigationalMenu!: SidebarSection[];

  private layoutService = inject(LayoutService);
  private router = inject(Router);
  private sub = new Subscription();

  menuSections: SidebarSection[] = [];
  activeRoute = '/dashboard';
  currentRole = '';

  constructor() {
    this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe((event) => {
        this.activeRoute = event.urlAfterRedirects;
      });
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['navigationalMenu']) {
      if (this.navigationalMenu && this.navigationalMenu.length > 0) {
        this.menuSections = this.navigationalMenu;
      } else if (!this.navigationalMenu && this.currentRole) {
        this.menuSections = this.layoutService.getSidebarMenu(this.currentRole);
      }
    }
  }

  ngOnInit(): void {
    if (this.navigationalMenu && this.navigationalMenu.length > 0) {
      this.menuSections = this.navigationalMenu;
    }
    this.sub.add(
      this.layoutService.currentRole$.subscribe((role) => {
        this.currentRole = role;
        if (!this.navigationalMenu || this.navigationalMenu.length === 0) {
          this.menuSections = this.layoutService.getSidebarMenu(role);
        }
      }),
    );

    this.sub.add(
      this.layoutService.sidebarCollapsed$.subscribe((collapsed) => {
        this.collapsed = collapsed;
      }),
    );

    this.activeRoute = this.router.url;
  }

  ngOnDestroy(): void {
    this.sub.unsubscribe();
  }

  toggleSidebar(): void {
    this.layoutService.toggleSidebar();
    this.toggle.emit(this.collapsed);
  }

  navigateTo(route: string): void {
    this.router.navigate([route]);
  }

  isActive(route: string): boolean {
    if (route === '/dashboard') {
      return this.activeRoute === '/dashboard';
    }
    return this.activeRoute.startsWith(route);
  }

  getInitial(label: string): string {
    return label.charAt(0).toUpperCase();
  }
}
