import { Injectable } from '@angular/core';
import { SidebarSection } from '../../../services/layout.service';

@Injectable({
  providedIn: 'root',
})
export class ClientPermissionService {
  /**
   * Check if user permissions contains the required permission
   */
  hasPermission(userPermissions: string[], permission: string): boolean {
    return userPermissions.includes(permission);
  }

  /**
   * Filters the client sidebar menu sections and items according to the user's granted permissions
   */
  filterMenuByPermissions(
    menu: SidebarSection[],
    userPermissions: string[],
  ): SidebarSection[] {
    return menu
      .map((section) => {
        const filteredItems = section.items.filter((item) => {
          const reqPermission = item.permission;
          if (!reqPermission) {
            return true; // No permission required, visible to all
          }
          // console.log(reqPermission);

          return userPermissions.includes(reqPermission);
        });

        return {
          ...section,
          items: filteredItems,
        };
      })
      .filter((section) => section.items.length > 0);
  }
}
