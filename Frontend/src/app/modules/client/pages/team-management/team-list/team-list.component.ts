import { Component, OnInit, ViewChild } from '@angular/core';
import { SharedModule } from '../../../../../shared/shared.module';
import { TeamManagementService } from '../../../../../services/team-management.service';
import { TeamMember } from '../../../../../models/auth.models';
import {
  ROLE_COMPANY_ADMIN,
  ROLE_EMPLOYEE,
  ROLE_MANAGER,
} from '../../../../../shared/constants/role.constants';
import { RoleManagementService } from '../../../../../services/role-management.service';
import { CLIENT_PERMISSIONS } from '../../../constants/permission.constants';

import { MenuItem } from 'primeng/api';
import { Menu } from 'primeng/menu';
import { ConfirmationService } from '../../../../../shared/services/confirmation.service';
import { ToastService } from '../../../../../shared/services/toast.service';

@Component({
  selector: 'client-team-list',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './team-list.component.html',
  styleUrl: './team-list.component.scss',
})
export class TeamListComponent implements OnInit {
  @ViewChild('teamActionMenu') teamActionMenu!: Menu;

  team: (TeamMember & { grantId?: string })[] = [];
  showAddDialog = false;
  isEditMode = false;
  editingMemberId: string | null = null;
  PERMISSIONS = CLIENT_PERMISSIONS;

  activeTeamMenuItems: MenuItem[] = [];

  newMember: {
    name: string;
    email: string;
    password?: string;
    confirmPassword?: string;
    role: 'Admin' | 'Manager' | 'Employee';
    phone: string;
    status: 'Active' | 'Inactive' | 'On Leave';
  } = {
    name: '',
    email: '',
    password: '',
    confirmPassword: '',
    role: 'Manager',
    phone: '',
    status: 'Active',
  };

  roles = [
    { label: 'Admin', value: 'company_admin' },
    { label: 'Manager', value: 'manager' },
    { label: 'Employee', value: 'employee' },
  ];

  statuses = [
    { label: 'Active', value: 'Active' },
    { label: 'Inactive', value: 'Inactive' },
    { label: 'On Leave', value: 'On Leave' },
  ];

  constructor(
    private tmService: TeamManagementService,
    private roleManagementService: RoleManagementService,
    private confirmationService: ConfirmationService,
    private toastService: ToastService,
  ) {}

  openTeamMenu(event: Event, member: any): void {
    event.stopPropagation();
    this.activeTeamMenuItems = [
      {
        label: 'Edit Member',
        icon: 'pi pi-user-edit',
        command: () => this.editMember(member),
      },
      { label: 'Reassign Leads', icon: 'pi pi-arrow-right-left' },
      { separator: true },
      {
        label: 'Remove',
        icon: 'pi pi-trash',
        styleClass: 'text-red-500',
        command: () => this.deleteMember(member),
      },
    ];
    this.teamActionMenu.toggle(event);
  }

  deleteMember(member: any): void {
    this.confirmationService.confirmDelete(
      `Are you sure you want to remove ${member.name} from the team?`,
      () => {
        if (member.grantId) {
          this.roleManagementService.revokeRole(member.grantId).subscribe({
            next: () => {
              this.toastService.success(`${member.name} has been removed.`);
              this.loadTeamMembers();
            },
            error: () => {
              this.team = this.team.filter((t) => t.id !== member.id);
            },
          });
        } else {
          this.team = this.team.filter((t) => t.id !== member.id);
        }
      }
    );
  }

  ngOnInit(): void {
    this.loadTeamMembers();
  }

  loadTeamMembers(): void {
    this.tmService.getEmployees().subscribe({
      next: (data) => {
        const grants = data.items;
        const roleMap: Record<string, 'Admin' | 'Manager' | 'Employee'> = {
          [ROLE_COMPANY_ADMIN]: 'Admin',
          platform_admin: 'Admin',
          [ROLE_MANAGER]: 'Manager',
          [ROLE_EMPLOYEE]: 'Employee',
          Admin: 'Admin',
          Manager: 'Manager',
        };

        this.team = grants.map((g, idx) => ({
          id: idx + 1,
          name: (g as any).name || (g as any).full_name || '',
          email: (g as any).email || (g as any).user_email || '',
          role: roleMap[g.role] || (g.role as any) || 'Agent',
          status: g.is_active !== false ? 'Active' : 'Inactive',
          phone: '+1 (555) 000-0000',
          avatar: '',
          lastActive: 'Active',
          assignedLeads: 0,
          grantId: g.id,
        }));
      },
      error: () => {},
    });
  }

  openAddDialog(): void {
    this.isEditMode = false;
    this.editingMemberId = null;
    this.resetForm();
    this.showAddDialog = true;
  }

  editMember(member: any): void {
    this.isEditMode = true;
    this.editingMemberId = member.grantId || null;
    this.newMember = {
      name: member.name,
      email: member.email,
      password: '',
      confirmPassword: '',
      role: member.role,
      phone: member.phone || '',
      status: member.status,
    };
    this.showAddDialog = true;
  }

  saveMember(): void {
    if (this.newMember.name && this.newMember.email) {
      if (this.isEditMode && this.editingMemberId) {
        // Edit Mode: update employee
        const payload = {
          full_name: this.newMember.name,
          role: this.newMember.role,
          is_active: this.newMember.status !== 'Inactive',
        };

        this.tmService.updateEmployee(this.editingMemberId, payload).subscribe({
          next: () => {
            this.loadTeamMembers();
            this.showAddDialog = false;
            this.isEditMode = false;
            this.editingMemberId = null;
            this.resetForm();
          },
          error: () => {
            // Fallback: update locally
            const index = this.team.findIndex(
              (t) => t.grantId === this.editingMemberId,
            );
            if (index !== -1) {
              this.team[index] = {
                ...this.team[index],
                name: this.newMember.name,
                role: this.newMember.role,
                status: this.newMember.status,
                phone: this.newMember.phone || this.team[index].phone,
              };
            }
            this.showAddDialog = false;
            this.isEditMode = false;
            this.editingMemberId = null;
            this.resetForm();
          },
        });
      } else {
        // Create Mode: create member
        const payload = {
          email: this.newMember.email,
          password: this.newMember.password,
          name: this.newMember.name,
          role: this.newMember.role,
          send_email_confirmation: false,
        };

        this.tmService.createMember(payload).subscribe({
          next: () => {
            this.loadTeamMembers();
            this.showAddDialog = false;
            this.resetForm();
          },
          error: () => {
            // Fallback to local push on error
            this.team.unshift({
              id: this.team.length + 1,
              name: this.newMember.name,
              email: this.newMember.email,
              role: this.newMember.role,
              status: this.newMember.status,
              phone: this.newMember.phone || '+1 (555) 000-0000',
              avatar: '',
              lastActive: 'Just now',
              assignedLeads: 0,
            });
            this.showAddDialog = false;
            this.resetForm();
          },
        });
      }
    }
  }

  resetForm(): void {
    this.newMember = {
      name: '',
      email: '',
      password: '',
      confirmPassword: '',
      role: 'Admin',
      phone: '',
      status: 'Active',
    };
  }

  getInitials(name: string): string {
    return name
      .split(' ')
      .map((n) => n[0])
      .join('')
      .toUpperCase()
      .slice(0, 2);
  }

  getAvatarColor(id: number): string {
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
    return colors[id % colors.length];
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
      Active: 'success',
      Inactive: 'danger',
      'On Leave': 'warn',
    };
    return map[status] || 'info';
  }

  getRoleSeverity(
    role: string,
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
      Admin: 'contrast',
      Manager: 'warn',
      Agent: 'info',
      'AI Operator': 'secondary',
    };
    return map[role] || 'info';
  }
}
