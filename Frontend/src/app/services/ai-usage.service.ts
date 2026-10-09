import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';

// ─── Response models ────────────────────────────────────────────────────────

export interface AiUsageSummary {
  total_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  has_unknown_price: boolean;
  is_partial: boolean;
  start_date: string;
  end_date: string;
  filter_client_id?: string | null;
}

export interface CompanyUsageRow {
  client_id: string | null;
  company_name: string;
  is_system: boolean;
  total_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  has_unknown_price: boolean;
}

export interface ProcessUsageRow {
  process: string;
  display_name: string;
  category: string;
  is_customer_facing: boolean;
  total_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  has_unknown_price?: boolean;
}

export interface ModelUsageRow {
  model: string;
  total_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  has_unknown_price?: boolean;
}

export interface DailyTrendPoint {
  date: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  tokens: number;
  cost_usd: number | null;
}

export interface AdminAiUsageResponse {
  summary: AiUsageSummary;
  companies: CompanyUsageRow[];
  by_process: ProcessUsageRow[];
  by_model: ModelUsageRow[];
  daily_trend: DailyTrendPoint[];
}

export interface ClientAiUsageResponse {
  client_id: string;
  company_name: string;
  show_cost: boolean;
  summary: {
    total_requests: number;
    total_input_tokens: number;
    total_output_tokens: number;
    total_tokens: number;
    total_cost_usd: number | null;
    start_date: string;
    end_date: string;
  };
  by_process: ProcessUsageRow[];
  daily_trend: DailyTrendPoint[];
}

export interface UsageCatalogItem {
  process: string;
  display_name: string;
  category: string;
  is_customer_facing: boolean;
}

// ─── Service ────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class AiUsageService {
  private readonly BASE = 'usage';

  constructor(private api: ApiService) {}

  /**
   * Super-Admin: Cross-company AI usage with full cost breakdown.
   */
  getAdminUsage(params: {
    start_date?: string;
    end_date?: string;
    client_id?: string;
  }): Observable<AdminAiUsageResponse> {
    const p: Record<string, string> = {};
    if (params.start_date) p['start_date'] = params.start_date;
    if (params.end_date) p['end_date'] = params.end_date;
    if (params.client_id) p['client_id'] = params.client_id;

    return this.api.get<AdminAiUsageResponse>(`${this.BASE}/admin`, {
      params: p,
    });
  }

  /**
   * Company Admin: Own company usage. Cost visibility controlled server-side.
   */
  getClientUsage(params: {
    start_date?: string;
    end_date?: string;
  }): Observable<ClientAiUsageResponse> {
    const p: Record<string, string> = {};
    if (params.start_date) p['start_date'] = params.start_date;
    if (params.end_date) p['end_date'] = params.end_date;

    return this.api.get<ClientAiUsageResponse>(`${this.BASE}/client`, {
      params: p,
      companyScoped: true,
    });
  }

  /** Process catalogue */
  getCatalog(): Observable<{ catalog: UsageCatalogItem[] }> {
    return this.api.get<{ catalog: UsageCatalogItem[] }>(`${this.BASE}/catalog`);
  }
}
