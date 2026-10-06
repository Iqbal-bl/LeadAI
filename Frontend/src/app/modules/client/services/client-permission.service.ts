import { Injectable, inject } from '@angular/core';
import { SidebarSection } from '../../../services/layout.service';
import { AuthService } from '../../../services/auth.service';

@Injectable({
  providedIn: 'root',
})
export class ClientPermissionService {
  private authService = inject(AuthService);

  /**
   * Check if user permissions contains the required permission
   */
  hasPermission(userPermissions: string[], permission: string, role?: string): boolean {
    if (!permission) return true;
    const r = (role || '').toLowerCase();
    if (r === 'admin' || r === 'company_admin' || r === 'platform_admin' || r === 'companyadmin') {
      return true;
    }
    const permissions = userPermissions || [];
    return permissions.includes(permission) || permissions.includes('*') || false;
  }

  /**
   * Filters the client sidebar menu sections and items according to the user's granted permissions
   * AND active subscription plan features (e.g. LinkedIn, AI Blog).
   */
  filterMenuByPermissions(
    menu: SidebarSection[],
    userPermissions: string[],
  ): SidebarSection[] {
    return menu
      .map((section) => {
        const filteredItems = section.items.filter((item) => {
          // 1. Subscription plan feature gating
          if (item.routerLink === '/client/linkedin' && !this.authService.hasFeature('linkedin')) {
            return false;
          }
          if (item.routerLink === '/client/blog' && !this.authService.hasFeature('blog')) {
            return false;
          }
          if (
            (item.routerLink === '/client/create-post' || item.routerLink === '/client/social-analytics') &&
            !this.authService.hasFeature('social')
          ) {
            return false;
          }

          // 2. Permission check
          const reqPermission = item.permission;
          if (!reqPermission) {
            return true; // No permission required, visible to all
          }

          const user = this.authService.getCurrentUser();
          const role = user?.role || '';
          return this.hasPermission(userPermissions, reqPermission, role);
        });

        return {
          ...section,
          items: filteredItems,
        };
      })
      .filter((section) => section.items.length > 0);
  }
}

