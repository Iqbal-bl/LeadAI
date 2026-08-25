import { Injectable, inject } from '@angular/core';
import { Observable, of } from 'rxjs';
import { ApiService } from './api.service';
import { RoleGrant, PermissionCatalogue, AssignableUser } from '../models/auth.models';

@Injectable({
  providedIn: 'root',
})
export class RoleManagementService {
  private apiService = inject(ApiService);

  // GET /access/permissions
  public getPermissions(): Observable<PermissionCatalogue> {
    return this.apiService.get<PermissionCatalogue>('access/permissions');
  }

  // GET /access/roles
  public getRoles(forCompany?: string): Observable<RoleGrant[]> {
    const options: any = {};
    if (forCompany) {
      options.params = { for_company: forCompany };
    }
    return this.apiService.get<RoleGrant[]>('access/roles', options);
  }

  // POST /access/roles
  public grantRole(payload: RoleGrant): Observable<RoleGrant> {
    return this.apiService.post<RoleGrant>('access/roles', payload);
  }

  // PATCH /access/roles/{grant_id}
  public updateRole(
    grantId: string,
    payload: Partial<RoleGrant>,
  ): Observable<RoleGrant> {
    return this.apiService.patch<RoleGrant>(`access/roles/${grantId}`, payload);
  }

  // DELETE /access/roles/{grant_id}
  public revokeRole(grantId: string): Observable<void> {
    return this.apiService.delete<void>(`access/roles/${grantId}`);
  }

  // GET /access/assignable-users
  public getAssignableUsers(): Observable<AssignableUser[]> {
    return this.apiService.get<AssignableUser[]>('access/assignable-users');
  }

  // GET /access/role-permissions/all
  public getAllRolePermissions(): Observable<any[]> {
    return this.apiService.get<any[]>('access/role-permissions/all');
  }

  // GET /access/role-permissions/{role}
  public getRolePermissions(role: string): Observable<any> {
    return this.apiService.get<any>(`access/role-permissions/${role}`);
  }

  // PATCH /access/role-permissions/{role}
  public updateRolePermission(
    role: string,
    payload: { permission_key: string; is_granted: boolean }
  ): Observable<any> {
    return this.apiService.patch<any>(`access/role-permissions/${role}`, payload);
  }

  // PUT /access/role-permissions/{role}
  public saveAllRolePermissions(
    role: string,
    payload: { permissions: { permission_key: string; is_granted: boolean }[] }
  ): Observable<any> {
    return this.apiService.put<any>(`access/role-permissions/${role}`, payload);
  }

  // DELETE /access/role-permissions/{role}
  public resetRolePermissions(role: string): Observable<any> {
    return this.apiService.delete<any>(`access/role-permissions/${role}`);
  }
}
