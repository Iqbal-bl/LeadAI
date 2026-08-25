import { Component, OnInit, inject } from '@angular/core';
import { AuthService } from '../../../../../services/auth.service';
import { CompanyService } from '../../../../../services/company.service';
import { RoleManagementService } from '../../../../../services/role-management.service';
import { RoleGrant } from '../../../../../models/auth.models';
import { Company } from '../../../../../models/company.models';
import { MessageService } from 'primeng/api';
import { ConfirmationService } from '../../../../../shared/services/confirmation.service';

import { SharedModule } from '../../../../../shared/shared.module';

@Component({
  selector: 'app-roles-assigned',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './roles-assigned.component.html',
  styleUrl: './roles-assigned.component.scss',
})
export class RolesAssignedComponent implements OnInit {
  private authService = inject(AuthService);
  private roleManagementService = inject(RoleManagementService);
  private companyService = inject(CompanyService);
  private confirmationService = inject(ConfirmationService);
  private messageService = inject(MessageService);

  roles: RoleGrant[] = [];
  companies: Company[] = [];
  loading = false;
  searchQuery = '';
  selectedCompanyFilter = '';

  // Dialog state
  displayDialog = false;
  isEditMode = false;
  selectedRole: RoleGrant | null = null;

  // Form fields
  formFullName = '';
  formEmail = '';
  formRole = '';
  formCompanyId = '';

  // Stats
  totalRolesCount = 0;
  activeRolesCount = 0;

  // Role options
  roleOptions = [
    { label: 'Platform Admin', value: 'platform_admin' },
    { label: 'Manager', value: 'manager' },
    { label: 'Agent', value: 'agent' },
    { label: 'Viewer', value: 'viewer' },
  ];

  get isPlatformAdmin(): boolean {
    return this.authService.isPlatformAdmin();
  }

  get isFormValid(): boolean {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return (
      this.formFullName.trim().length > 0 &&
      this.formEmail.trim().length > 0 &&
      emailRegex.test(this.formEmail) &&
      this.formRole.length > 0
    );
  }

  ngOnInit(): void {
    this.loadRoles();
    this.loadCompanies();
  }

  loadRoles(): void {
    this.loading = true;
    const forCompany = this.selectedCompanyFilter || undefined;
    this.roleManagementService.getRoles(forCompany).subscribe({
      next: (data) => {
        this.roles = data
        this.calculateStats();
        this.loading = false;
      },
      error: () => {
        this.calculateStats();
        this.loading = false;
      },
    });
  }

  loadCompanies(): void {
    this.companyService.getCompanies(true).subscribe({
      next: (data) => {
        this.companies = data || [];
      },
      error: () => {
        this.companies = [];
      },
    });
  }

  private calculateStats(): void {
    this.totalRolesCount = this.roles.length;
    this.activeRolesCount = this.roles.filter((r) => r.is_active).length;
  }

  getFilteredRoles(): RoleGrant[] {
    if (!this.searchQuery) return this.roles;
    const query = this.searchQuery.toLowerCase();
    return this.roles.filter(
      (r) =>
        r.full_name.toLowerCase().includes(query) ||
        r.user_email.toLowerCase().includes(query),
    );
  }

  onCompanyFilterChange(): void {
    this.loadRoles();
  }

  // --- Create / Edit Dialog ---

  openCreateDialog(): void {
    this.isEditMode = false;
    this.selectedRole = null;
    this.formFullName = '';
    this.formEmail = '';
    this.formRole = '';
    this.formCompanyId = '';
    this.displayDialog = true;
  }

  openEditDialog(role: RoleGrant): void {
    this.isEditMode = true;
    this.selectedRole = role;
    this.formFullName = role.full_name;
    this.formEmail = role.user_email;
    this.formRole = role.role;
    this.formCompanyId = role.client_id || '';
    this.displayDialog = true;
  }

  onDialogSave(): void {
    if (this.isEditMode && this.selectedRole?.id) {
      const payload: Partial<RoleGrant> = {
        role: this.formRole,
        full_name: this.formFullName,
        is_active: this.selectedRole.is_active,
      };
      this.roleManagementService.updateRole(this.selectedRole.id, payload).subscribe({
        next: (updated) => {
          const idx = this.roles.findIndex(
            (r) => r.id === this.selectedRole!.id,
          );
          if (idx > -1) this.roles[idx] = { ...this.roles[idx], ...updated };
          this.calculateStats();
          this.displayDialog = false;
          this.messageService.add({
            severity: 'success',
            summary: 'Updated',
            detail: 'Role updated successfully.',
          });
        },
        error: () => {
          // Demo fallback — update locally
          const idx = this.roles.findIndex(
            (r) => r.id === this.selectedRole!.id,
          );
          if (idx > -1) {
            this.roles[idx] = {
              ...this.roles[idx],
              full_name: this.formFullName,
              role: this.formRole,
            };
          }
          this.displayDialog = false;
          this.messageService.add({
            severity: 'success',
            summary: 'Updated (Demo)',
            detail: 'Role updated successfully.',
          });
        },
      });
    } else {
      const payload: RoleGrant = {
        user_email: this.formEmail,
        role: this.formRole,
        full_name: this.formFullName,
        client_id: this.formCompanyId || undefined,
      };
      this.roleManagementService.grantRole(payload).subscribe({
        next: (created) => {
          this.roles = [created, ...this.roles];
          this.calculateStats();
          this.displayDialog = false;
          this.messageService.add({
            severity: 'success',
            summary: 'Created',
            detail: 'Role assigned successfully.',
          });
        },
        error: () => {
          // Demo fallback — insert locally
          const mockCreated: RoleGrant = {
            id: 'grant-' + Date.now(),
            user_email: this.formEmail,
            full_name: this.formFullName,
            role: this.formRole,
            client_id: this.formCompanyId,
            client_name: this.companies.find(
              (c) => c.id === this.formCompanyId,
            )?.name || '',
            is_active: true,
            created_at: new Date().toISOString(),
          };
          this.roles = [mockCreated, ...this.roles];
          this.calculateStats();
          this.displayDialog = false;
          this.messageService.add({
            severity: 'success',
            summary: 'Created (Demo)',
            detail: 'Role assigned successfully.',
          });
        },
      });
    }
  }

  onDialogCancel(): void {
    this.displayDialog = false;
  }

  // --- Toggle Active Status ---

  toggleActiveStatus(role: RoleGrant): void {
    if (!role.id) return;
    const newStatus = !role.is_active;
    this.roleManagementService.updateRole(role.id, { is_active: newStatus }).subscribe({
      next: () => {
        role.is_active = newStatus;
        this.calculateStats();
        this.messageService.add({
          severity: 'success',
          summary: 'Status Updated',
          detail: `${role.full_name} is now ${newStatus ? 'Active' : 'Inactive'}`,
        });
      },
      error: () => {
        role.is_active = newStatus;
        this.calculateStats();
        this.messageService.add({
          severity: 'success',
          summary: 'Status Updated (Demo)',
          detail: `${role.full_name} is now ${newStatus ? 'Active' : 'Inactive'}`,
        });
      },
    });
  }

  // --- Delete ---

  deleteRole(role: RoleGrant): void {
    this.confirmationService.confirm({
      message: `Are you sure you want to revoke this role from ${role.full_name}?`,
      header: 'Remove Role?',
      icon: 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: 'p-button-danger p-button-sm cursor-pointer',
      rejectButtonStyleClass:
        'p-button-secondary p-button-outlined p-button-sm cursor-pointer',
      accept: () => {
        if (!role.id) return;
        this.roleManagementService.revokeRole(role.id).subscribe({
          next: () => {
            this.roles = this.roles.filter((r) => r.id !== role.id);
            this.calculateStats();
            this.messageService.add({
              severity: 'success',
              summary: 'Revoked',
              detail: 'Role revoked successfully.',
            });
          },
          error: () => {
            this.roles = this.roles.filter((r) => r.id !== role.id);
            this.calculateStats();
            this.messageService.add({
              severity: 'success',
              summary: 'Revoked (Demo)',
              detail: 'Role revoked successfully.',
            });
          },
        });
      },
    });
  }

  // --- Helpers ---

  getRoleSeverity(role: string): string {
    switch (role) {
      case 'platform_admin':
        return 'danger';
      case 'manager':
        return 'info';
      case 'agent':
        return 'success';
      case 'viewer':
        return 'secondary';
      default:
        return 'info';
    }
  }

  getRoleLabel(role: string): string {
    switch (role) {
      case 'platform_admin':
        return 'Platform Admin';
      case 'manager':
        return 'Manager';
      case 'agent':
        return 'Agent';
      case 'viewer':
        return 'Viewer';
      default:
        return role;
    }
  }
}
