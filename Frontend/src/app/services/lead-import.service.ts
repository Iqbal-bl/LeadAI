import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import {
  LeadImportSchemaResponse,
  LeadImportResponse,
} from '../models/lead-import.models';

@Injectable({
  providedIn: 'root',
})
export class LeadImportService {
  constructor(private apiService: ApiService) {}

  /**
   * Fetch lead import schema (fixed fields + company-defined custom data points)
   * GET /api/leadai/leads/import/schema
   */
  public getImportSchema(): Observable<LeadImportSchemaResponse> {
    return this.apiService.get<LeadImportSchemaResponse>('leads/import/schema', {
      companyScoped: true,
    });
  }

  /**
   * Upload leads file and auto-classify into product-tailored campaign batches
   * POST /api/leadai/leads/import (multipart/form-data)
   */
  public importLeads(formData: FormData): Observable<LeadImportResponse> {
    return this.apiService.post<LeadImportResponse>('leads/import', formData, {
      companyScoped: true,
    });
  }

  /**
   * Client-side helper to download the sample CSV template
   */
  public downloadSampleTemplate(csvHeader: string, filename = 'leads_import_template.csv'): void {
    const blob = new Blob([csvHeader + '\n'], { type: 'text/csv;charset=utf-8;' });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
  }
}
