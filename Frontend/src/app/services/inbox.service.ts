import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import {
  LeadInboxItem,
  LeadDetail,
  ContactInfo,
  InboxResponse,
} from '../models/inbox.models';

export interface InboxQueryParams {
  status?: string;
  lead_status?: string;
  channel?: string;
  assigned_to?: 'me' | 'unassigned' | string;
  search?: string;
  min_score?: number;
  sort?: 'recent' | 'score' | 'oldest';
  page?: number;
  page_size?: number;
  above_threshold?: boolean;
  campaign_id?: string;
}

@Injectable({
  providedIn: 'root',
})
export class InboxService {
  constructor(private apiService: ApiService) {}

  // GET /inbox
  public getInbox(params?: InboxQueryParams): Observable<InboxResponse> {
    return this.apiService.get<InboxResponse>('inbox', {
      params: params as any,
      companyScoped: true,
    });
  }

  // GET /inbox/queue
  public getWorkQueue(): Observable<LeadInboxItem[]> {
    return this.apiService.get<LeadInboxItem[]>('inbox/queue', {
      companyScoped: true,
    });
  }

  // GET /inbox/{id}
  public getLeadDetail(id: string): Observable<LeadDetail> {
    return this.apiService.get<LeadDetail>(`inbox/${id}`, {
      companyScoped: true,
    });
  }

  // POST /inbox/{id}/assign
  public assignLead(id: string, userEmail: string | null): Observable<void> {
    return this.apiService.post<void>(
      `inbox/${id}/assign`,
      { user_email: userEmail },
      {
        companyScoped: true,
      },
    );
  }

  // POST /inbox/{id}/claim
  public claimLead(id: string): Observable<void> {
    return this.apiService.post<void>(`inbox/${id}/claim`, null, {
      companyScoped: true,
    });
  }

  // POST /inbox/{id}/reply
  public replyLead(id: string, message: string): Observable<void> {
    return this.apiService.post<void>(
      `inbox/${id}/reply`,
      { message },
      {
        companyScoped: true,
      },
    );
  }

  // POST /inbox/{id}/status
  public setLeadStatus(
    id: string,
    status: 'open' | 'needs_human' | 'closed' | string,
  ): Observable<void> {
    return this.apiService.post<void>(
      `inbox/${id}/status`,
      { status },
      {
        companyScoped: true,
      },
    );
  }

  // GET /inbox/{id}/contact
  public getLeadContact(id: string): Observable<ContactInfo> {
    return this.apiService.get<ContactInfo>(`inbox/${id}/contact`, {
      companyScoped: true,
    });
  }

  // POST /inbox/{id}/requalify
  public requalifyLead(id: string): Observable<void> {
    return this.apiService.post<void>(`inbox/${id}/requalify`, null, {
      companyScoped: true,
    });
  }

  // GET /inbox/export/leads
  public exportLeads(leadStatus?: string, limit?: number): Observable<any[]> {
    const params: Record<string, string | number> = {};
    if (leadStatus) {
      params['lead_status'] = leadStatus;
    }
    if (limit) {
      params['limit'] = limit;
    }
    return this.apiService.get<any[]>('inbox/export/leads', {
      params,
      companyScoped: true,
    });
  }
}
