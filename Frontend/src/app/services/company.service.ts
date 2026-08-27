import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import {
  Company,
  CompanyCreatePayload,
  CompanyPermissionsOut,
  CompanyPermissionsPatchIn,
  CompanySettings,
} from '../models/company.models';

@Injectable({
  providedIn: 'root',
})
export class CompanyService {
  constructor(private apiService: ApiService) {}

  // GET /companies
  public getCompanies(includeInactive: boolean = false): Observable<Company[]> {
    return this.apiService.get<Company[]>('companies', {
      params: { include_inactive: includeInactive },
    });
  }

  // POST /companies
  public createCompany(payload: CompanyCreatePayload): Observable<Company> {
    return this.apiService.post<Company>('companies', payload);
  }

  // GET /companies/{id}
  public getCompany(id: string, options?: any): Observable<Company> {
    return this.apiService.get<Company>(`companies/${id}`, options);
  }

  // PATCH /companies/{id}
  public updateCompany(
    id: string,
    payload: Partial<
      Pick<
        Company,
        'name' | 'email' | 'phone_number' | 'description' | 'is_active'
      >
    >,
    options?: any
  ): Observable<Company> {
    return this.apiService.patch<Company>(`companies/${id}`, payload, options);
  }

  // DELETE /companies/{id}
  public deleteCompany(
    id: string,
    clientId?: string,
  ): Observable<{ success: boolean; message: string }> {
    const options: any = {};
    if (clientId) {
      options.params = { client_id: clientId };
    } else {
      options.companyScoped = true;
    }
    return this.apiService.delete<{ success: boolean; message: string }>(
      `companies/${id}`,
      options,
    );
  }

  // GET /companies/{id}/settings
  public getCompanySettings(id: string): Observable<CompanySettings> {
    return this.apiService.get<CompanySettings>(`companies/${id}/settings`);
  }

  // PUT /companies/{id}/settings
  public updateCompanySettings(
    id: string,
    settings: CompanySettings,
  ): Observable<CompanySettings> {
    return this.apiService.put<CompanySettings>(
      `companies/${id}/settings`,
      settings,
    );
  }

  // GET /companies/{company_id}/permissions
  public getCompanyPermissions(
    companyId: string,
    options?: any,
  ): Observable<CompanyPermissionsOut> {
    return this.apiService.get<CompanyPermissionsOut>(
      `companies/${companyId}/permissions`,
      options,
    );
  }

  // PATCH /companies/{company_id}/permissions
  public patchCompanyPermissions(
    companyId: string,
    payload: CompanyPermissionsPatchIn,
    options?: any,
  ): Observable<CompanyPermissionsOut> {
    return this.apiService.patch<CompanyPermissionsOut>(
      `companies/${companyId}/permissions`,
      payload,
      options,
    );
  }

  // Backward compatibility aliases
  public getCompanyServices(companyId: string, options?: any): Observable<any> {
    return this.getCompanyPermissions(companyId, options);
  }

  public patchCompanyServices(
    companyId: string,
    payload: any,
    options?: any,
  ): Observable<any> {
    return this.patchCompanyPermissions(companyId, payload, options);
  }
}

