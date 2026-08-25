import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';

export interface CreateMemberPayload {
  email: string;
  password?: string;
  name: string;
  role: string;
  send_email_confirmation?: boolean;
}

export interface UpdateEmployeePayload {
  full_name?: string;
  role?: string;
  is_active?: boolean;
}

export interface EmployeeResponse {
  id: string;
  email: string;
  name: string;
  role: string;
  client_id: string;
  is_active: boolean;
  created_at: string;
}

export interface ListEmployeesResponse {
  total: number;
  items: EmployeeResponse[];
}

@Injectable({ providedIn: 'root' })
export class TeamManagementService {
  constructor(private apiService: ApiService) {}

  /**
   * Create a member in your company (company admin)
   * POST /api/leadai/user-management/members
   */
  public createMember(payload: CreateMemberPayload, clientId?: string | null): Observable<EmployeeResponse> {
    const params: Record<string, string> = {};
    if (clientId) {
      params['client_id'] = clientId;
    }
    return this.apiService.post<EmployeeResponse>('user-management/members', payload, {
      params,
      companyScoped: true,
    });
  }

  /**
   * List employees in your company (company admin)
   * GET /api/leadai/user-management/employees
   */
  public getEmployees(clientId?: string | null): Observable<ListEmployeesResponse> {
    const params: Record<string, string> = {};
    if (clientId) {
      params['client_id'] = clientId;
    }
    return this.apiService.get<ListEmployeesResponse>('user-management/employees', {
      params,
      companyScoped: true,
    });
  }

  /**
   * Update an employee in your company (company admin)
   * PATCH /api/leadai/user-management/employees/{employee_id}
   */
  public updateEmployee(
    employeeId: string,
    payload: UpdateEmployeePayload,
    clientId?: string | null
  ): Observable<EmployeeResponse> {
    const params: Record<string, string> = {};
    if (clientId) {
      params['client_id'] = clientId;
    }
    return this.apiService.patch<EmployeeResponse>(`user-management/employees/${employeeId}`, payload, {
      params,
      companyScoped: true,
    });
  }
}
