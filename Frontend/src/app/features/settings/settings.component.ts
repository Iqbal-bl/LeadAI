import { Component, OnInit } from '@angular/core';
import { forkJoin } from 'rxjs';
import { AuthService } from '../../services/auth.service';
import { RoleManagementService } from '../../services/role-management.service';
import { ToastService } from '../../shared/services/toast.service';

import { SharedModule } from '../../shared/shared.module';
import { LeadThresholdComponent } from './lead-threshold/lead-threshold.component';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [SharedModule, LeadThresholdComponent],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.scss',
})
export class SettingsComponent implements OnInit {
  activeTab: string = 'threshold';

  isCompanyAdmin = false;
  loadingPermissions = false;
  managerCanReveal: boolean | undefined = undefined;
  employeeCanReveal: boolean | undefined = undefined;
  savingManager = false;
  savingEmployee = false;

  constructor(
    private authService: AuthService,
    private roleManagementService: RoleManagementService,
    private toastService: ToastService,
  ) {}

  ngOnInit(): void {
    this.checkAdminRole();
  }

  private checkAdminRole(): void {
    this.authService.currentUser$.subscribe({
      next: (user) => {
        if (user) {
          this.isCompanyAdmin = this.authService.isCompanyAdmin();
          if (this.isCompanyAdmin) {
            this.loadCompanyRolePermissions();
          }
        }
      },
    });
  }

  public loadCompanyRolePermissions(): void {
    this.loadingPermissions = true;
    forkJoin({
      manager: this.roleManagementService.getCompanyRolePermissions('manager'),
      employee:
        this.roleManagementService.getCompanyRolePermissions('employee'),
    }).subscribe({
      next: ({ manager, employee }) => {
        this.loadingPermissions = false;
        this.managerCanReveal =
          manager?.find((r) => r.permission_key === 'lead.reveal_pii')
            ?.is_granted ?? false;
        this.employeeCanReveal =
          employee?.find((r) => r.permission_key === 'lead.reveal_pii')
            ?.is_granted ?? false;
      },
      error: (err) => {
        this.loadingPermissions = false;
        console.warn('Failed to load company role permissions:', err);
      },
    });
  }

  public setRolePermission(
    role: 'manager' | 'employee',
    isGranted: boolean,
  ): void {
    if (role === 'manager') this.savingManager = true;
    if (role === 'employee') this.savingEmployee = true;

    this.roleManagementService
      .updateCompanyRolePermission(role, {
        permission_key: 'lead.reveal_pii',
        is_granted: isGranted,
      })
      .subscribe({
        next: (res) => {
          if (role === 'manager') {
            this.savingManager = false;
            this.managerCanReveal = res.is_granted;
          } else {
            this.savingEmployee = false;
            this.employeeCanReveal = res.is_granted;
          }
          this.toastService.success(
            `${role === 'manager' ? 'Managers' : 'Employees'} ${
              res.is_granted ? 'can now' : 'can no longer'
            } reveal customer contact details.`,
            'Permission Updated',
          );
        },
        error: (err) => {
          if (role === 'manager') this.savingManager = false;
          if (role === 'employee') this.savingEmployee = false;
          this.toastService.error(
            err?.error?.detail ||
              err?.message ||
              'Failed to update permission.',
            'Update Failed',
          );
        },
      });
  }
}
