import { Component, OnInit, inject } from '@angular/core';
import { RoleManagementService } from '../../../../../services/role-management.service';

import { SharedModule } from '../../../../../shared/shared.module';

@Component({
  selector: 'app-permissions-matrix',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './permissions-matrix.component.html',
  styleUrl: './permissions-matrix.component.scss',
})
export class PermissionsMatrixComponent implements OnInit {
  private roleManagementService = inject(RoleManagementService);

  loading = false;
  searchQuery = '';

  // Parsed data
  permissions: Record<string, string> = {};
  rolePermissions: Record<string, string[]> = {};
  roles: string[] = [];
  permissionKeys: string[] = [];
  filteredPermissions: string[] = [];
  formattedRoleNames: Record<string, string> = {};

  // Stats
  totalRoles = 0;
  totalPermissions = 0;

  ngOnInit(): void {
    this.loadPermissions();
  }

  loadPermissions(): void {
    this.loading = true;
    this.roleManagementService.getPermissions().subscribe({
      next: (data) => {
        this.permissions = data.permissions;
        this.rolePermissions = data.role_permissions;

        this.processData();
        this.loading = false;
      },
      error: () => {
        this.processData();
        this.loading = false;
      },
    });
  }

  private processData(): void {
    this.roles = Object.keys(this.rolePermissions);
    this.permissionKeys = Object.keys(this.permissions);
    this.totalRoles = this.roles.length;
    this.totalPermissions = this.permissionKeys.length;
    this.formattedRoleNames = this.roles.reduce(
      (acc, r) => {
        acc[r] = r
          .split('_')
          .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
          .join(' ');
        return acc;
      },
      {} as Record<string, string>,
    );
    this.updateFilteredPermissions();
  }

  updateFilteredPermissions(): void {
    if (!this.searchQuery) {
      this.filteredPermissions = this.permissionKeys;
      return;
    }
    const query = this.searchQuery.toLowerCase();
    this.filteredPermissions = this.permissionKeys.filter(
      (key) =>
        key.toLowerCase().includes(query) ||
        this.permissions[key].toLowerCase().includes(query),
    );
  }

  getFilteredPermissions(): string[] {
    return this.filteredPermissions;
  }

  hasPermission(role: string, permission: string): boolean {
    return this.rolePermissions[role]?.includes(permission) ?? false;
  }

  formatRoleName(role: string): string {
    return role
      .split('_')
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ');
  }
}
