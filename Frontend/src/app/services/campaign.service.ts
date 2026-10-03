import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import { AuthService } from './auth.service';
import {
  Campaign,
  CampaignCreateRequest,
  CampaignHistoryResponse,
  CampaignPreview,
  CampaignRecipient,
  CampaignExecution,
  CampaignExecutionListResponse,
  RestartMode,
} from '../models/campaign.models';
import { environment } from '../../environments/environment';

@Injectable({
  providedIn: 'root',
})
export class CampaignService {
  constructor(
    private apiService: ApiService,
    private authService: AuthService,
  ) {}

  /** GET /campaigns/{id}/export — URL for direct browser CSV report download */
  public getExportUrl(id: string, executionId?: string): string {
    const isPlatformAdmin = this.authService.isPlatformAdmin();
    const currentCompanyId = this.authService.getSelectedCompanyId();
    const token = this.authService.getStaffToken();
    const params = new URLSearchParams();
    if (token) {
      params.append('token', token);
    }
    if (isPlatformAdmin && currentCompanyId) {
      params.append('client_id', currentCompanyId);
    }
    if (executionId) {
      params.append('execution_id', executionId);
    }
    const query = params.toString();
    const base = `${environment.apiPrefix}/campaigns/${id}/export`;
    return query ? `${base}?${query}` : base;
  }

  /** Trigger CSV export download in new browser tab / prompt */
  public downloadExport(id: string, executionId?: string): void {
    const url = this.getExportUrl(id, executionId);
    window.open(url, '_blank');
  }

  /** GET /campaigns — list all campaigns */
  public getCampaigns(): Observable<Campaign[]> {
    return this.apiService.get<Campaign[]>('campaigns', {
      companyScoped: true,
    });
  }

  /** GET /campaigns/{id} — get campaign with counters */
  public getCampaign(id: string): Observable<Campaign> {
    return this.apiService.get<Campaign>(`campaigns/${id}`, {
      companyScoped: true,
    });
  }

  /** POST /campaigns — create a new campaign (draft) */
  public createCampaign(req: CampaignCreateRequest): Observable<Campaign> {
    return this.apiService.post<Campaign>('campaigns', req, {
      companyScoped: true,
    });
  }

  /** POST /campaigns/{id}/build — materialise recipient rows */
  public buildCampaign(id: string): Observable<any> {
    return this.apiService.post<any>(`campaigns/${id}/build`, null, {
      companyScoped: true,
    });
  }

  /** POST /campaigns/{id}/preview — preview with rendered samples & warnings */
  public previewCampaign(id: string): Observable<CampaignPreview> {
    return this.apiService.get<CampaignPreview>(`campaigns/${id}/preview`, {
      companyScoped: true,
    });
  }

  /** POST /campaigns/{id}/start — queue or restart campaign with optional restart_mode */
  public startCampaign(id: string, restartMode?: RestartMode): Observable<any> {
    const path = restartMode
      ? `campaigns/${id}/start?restart_mode=${restartMode}`
      : `campaigns/${id}/start`;
    return this.apiService.post<any>(path, null, {
      companyScoped: true,
    });
  }

  /** POST /campaigns/{id}/pause */
  public pauseCampaign(id: string): Observable<any> {
    return this.apiService.post<any>(`campaigns/${id}/pause`, null, {
      companyScoped: true,
    });
  }

  /** POST /campaigns/{id}/resume */
  public resumeCampaign(id: string): Observable<any> {
    return this.apiService.post<any>(`campaigns/${id}/resume`, null, {
      companyScoped: true,
    });
  }

  /** POST /campaigns/{id}/cancel */
  public cancelCampaign(id: string): Observable<any> {
    return this.apiService.post<any>(`campaigns/${id}/cancel`, null, {
      companyScoped: true,
    });
  }

  /** POST /campaigns/{id}/retry-failed — resets only permanently-failed recipients */
  public retryFailed(id: string): Observable<any> {
    return this.apiService.post<any>(`campaigns/${id}/retry-failed`, null, {
      companyScoped: true,
    });
  }

  /** GET /campaigns/{id}/recipients — optionally filtered by status */
  public getRecipients(
    id: string,
    status?: string,
  ): Observable<CampaignRecipient[]> {
    const params: Record<string, string> = {};
    if (status) {
      params['status'] = status;
    }
    return this.apiService.get<CampaignRecipient[]>(
      `campaigns/${id}/recipients`,
      {
        params,
        companyScoped: true,
      },
    );
  }

  /** GET /campaigns/{id}/history — paginated, newest-first list of run events */
  public getHistory(
    id: string,
    page: number = 1,
    pageSize: number = 50,
  ): Observable<CampaignHistoryResponse> {
    return this.apiService.get<CampaignHistoryResponse>(
      `campaigns/${id}/history`,
      {
        params: {
          page: page.toString(),
          page_size: pageSize.toString(),
        },
        companyScoped: true,
      },
    );
  }

  /** GET /campaigns/{id}/executions — paginated, newest-first list of runs */
  public getExecutions(
    id: string,
    page: number = 1,
    pageSize: number = 50,
  ): Observable<CampaignExecutionListResponse> {
    return this.apiService.get<CampaignExecutionListResponse>(
      `campaigns/${id}/executions`,
      {
        params: {
          page: page.toString(),
          page_size: pageSize.toString(),
        },
        companyScoped: true,
      },
    );
  }
}

