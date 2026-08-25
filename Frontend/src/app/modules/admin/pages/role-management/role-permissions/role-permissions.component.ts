import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { RoleManagementService } from '../../../../../services/role-management.service';
import { MessageService, ConfirmationService } from 'primeng/api';
import { Subject, Subscription } from 'rxjs';
import { debounceTime, distinctUntilChanged } from 'rxjs/operators';

export interface RolePermission {
  role: string;
  permission_key: string;
  description: string;
  is_granted: boolean;
  is_default: boolean;
}

export interface RolePermissionsInfo {
  role: string;
  permissions: RolePermission[];
  effective_permissions: string[];
  grantedCount?: number;
  defaultCount?: number;
  customCount?: number;
  totalCount?: number;
}

import { SharedModule } from '../../../../../shared/shared.module';

@Component({
  selector: 'app-role-permissions',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './role-permissions.component.html',
  styleUrl: './role-permissions.component.scss',
})
export class RolePermissionsComponent implements OnInit, OnDestroy {
  private roleManagementService = inject(RoleManagementService);
  private messageService = inject(MessageService);
  private confirmationService = inject(ConfirmationService);

  rolesInfo: RolePermissionsInfo[] = [];
  loading = false;
  loadingDetails = false;
  saving = false;

  // Page View State
  selectedRole: string | null = null;
  rolePermissions: RolePermission[] = [];
  effectivePermissions: string[] = [];

  // Search Filter with Debounce
  searchQuery = '';
  private searchSubject = new Subject<string>();
  private searchSubscription!: Subscription;
  filteredQuery = '';

  ngOnInit(): void {
    this.loadAllRolePermissions();

    this.searchSubscription = this.searchSubject
      .pipe(
        debounceTime(300),
        distinctUntilChanged()
      )
      .subscribe((query) => {
        this.filteredQuery = query;
      });
  }

  ngOnDestroy(): void {
    if (this.searchSubscription) {
      this.searchSubscription.unsubscribe();
    }
  }

  loadAllRolePermissions(): void {
    this.loading = true;
    this.roleManagementService.getAllRolePermissions().subscribe({
      next: (data) => {
        this.rolesInfo = data.map((roleInfo) => {
          const permissions = roleInfo.permissions || [];
          return {
            ...roleInfo,
            grantedCount: permissions.filter((p: any) => p.is_granted).length,
            defaultCount: permissions.filter((p: any) => p.is_default).length,
            customCount: permissions.filter((p: any) => p.is_granted !== p.is_default).length,
            totalCount: permissions.length,
          };
        });
        this.loading = false;
      },
      error: (err) => {
        this.loading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Load Error',
          detail: err?.message || 'Failed to load role permissions.',
        });
      },
    });
  }

  managePermissions(role: string): void {
    this.selectedRole = role;
    this.loadRolePermissionsDetails(role);
  }

  loadRolePermissionsDetails(role: string): void {
    this.loadingDetails = true;
    this.roleManagementService.getRolePermissions(role).subscribe({
      next: (data) => {
        this.rolePermissions = JSON.parse(JSON.stringify(data.permissions || []));
        this.effectivePermissions = [...(data.effective_permissions || [])];
        this.loadingDetails = false;
      },
      error: (err) => {
        this.loadingDetails = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Load Error',
          detail: err?.message || 'Failed to load details for this role.',
        });
      },
    });
  }

  onSearchChange(event: any): void {
    this.searchSubject.next(event.target.value);
  }

  getFilteredPermissions(): RolePermission[] {
    if (!this.filteredQuery) {
      return this.rolePermissions;
    }
    const query = this.filteredQuery.toLowerCase();
    return this.rolePermissions.filter(
      (p) =>
        p.permission_key.toLowerCase().includes(query) ||
        p.description.toLowerCase().includes(query)
    );
  }

  togglePermission(permission: RolePermission): void {
    if (!this.selectedRole) return;

    this.roleManagementService
      .updateRolePermission(this.selectedRole, {
        permission_key: permission.permission_key,
        is_granted: permission.is_granted,
      })
      .subscribe({
        next: () => {
          this.messageService.add({
            severity: 'success',
            summary: 'Permission Updated',
            detail: `Successfully updated '${permission.description}'`,
          });
          this.updateEffectivePermissionsLocal();
        },
        error: (err) => {
          permission.is_granted = !permission.is_granted;
          this.messageService.add({
            severity: 'error',
            summary: 'Update Failed',
            detail: err?.message || 'Failed to toggle permission.',
          });
        },
      });
  }

  saveAllChanges(): void {
    if (!this.selectedRole) return;

    this.saving = true;
    const payload = {
      permissions: this.rolePermissions.map((p) => ({
        permission_key: p.permission_key,
        is_granted: p.is_granted,
      })),
    };

    this.roleManagementService.saveAllRolePermissions(this.selectedRole, payload).subscribe({
      next: () => {
        this.saving = false;
        this.messageService.add({
          severity: 'success',
          summary: 'Permissions Saved',
          detail: 'All role permissions have been successfully updated.',
        });
        this.loadAllRolePermissions();
        this.selectedRole = null;
      },
      error: (err) => {
        this.saving = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Save Failed',
          detail: err?.message || 'Failed to save permission overrides.',
        });
      },
    });
  }

  confirmReset(): void {
    if (!this.selectedRole) return;

    this.confirmationService.confirm({
      header: 'Reset Permissions?',
      message: `This will remove all custom permission overrides and restore ${this.formatRoleName(this.selectedRole)} back to its default permissions. Are you sure you want to proceed?`,
      icon: 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: 'p-button-danger p-button-sm cursor-pointer',
      rejectButtonStyleClass: 'p-button-secondary p-button-outlined p-button-sm cursor-pointer',
      accept: () => {
        this.resetPermissions();
      },
    });
  }

  private resetPermissions(): void {
    if (!this.selectedRole) return;

    this.roleManagementService.resetRolePermissions(this.selectedRole).subscribe({
      next: () => {
        this.messageService.add({
          severity: 'success',
          summary: 'Permissions Reset',
          detail: 'Role permissions have been reset back to default settings.',
        });
        this.loadAllRolePermissions();
        this.selectedRole = null;
      },
      error: (err) => {
        this.messageService.add({
          severity: 'error',
          summary: 'Reset Failed',
          detail: err?.message || 'Failed to reset role permissions.',
        });
      },
    });
  }

  private updateEffectivePermissionsLocal(): void {
    this.effectivePermissions = this.rolePermissions
      .filter((p) => p.is_granted)
      .map((p) => p.permission_key);

    const mainRoleInfo = this.rolesInfo.find(r => r.role === this.selectedRole);
    if (mainRoleInfo) {
      const targetInMain = mainRoleInfo.permissions;
      this.rolePermissions.forEach(childPerm => {
        const matchingMain = targetInMain.find(p => p.permission_key === childPerm.permission_key);
        if (matchingMain) {
          matchingMain.is_granted = childPerm.is_granted;
        }
      });
      mainRoleInfo.grantedCount = targetInMain.filter((p: any) => p.is_granted).length;
      mainRoleInfo.customCount = targetInMain.filter((p: any) => p.is_granted !== p.is_default).length;
    }
  }

  formatRoleName(role: string): string {
    if (!role) return '';
    return role
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (char) => char.toUpperCase());
  }
}
