import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import {
  LinkedInStatus,
  LinkedInCredentialsPayload,
  GenerateKeywordsRequest,
  GenerateKeywordsResponse,
  SearchProfilesRequest,
  SearchProfilesResponse,
  SendInvitationsRequest,
  SendInvitationsResponse,
} from '../models/linkedin.models';

@Injectable({
  providedIn: 'root',
})
export class LinkedinService {
  constructor(private apiService: ApiService) {}

  /**
   * Check connection status of company LinkedIn account
   */
  public getStatus(): Observable<LinkedInStatus> {
    return this.apiService.get<LinkedInStatus>('linkedin/status', {
      companyScoped: true,
    });
  }

  /**
   * Retrieve LinkedIn OAuth 2.0 authorization URL
   */
  public getConnectUrl(): Observable<{ authorize_url: string }> {
    return this.apiService.get<{ authorize_url: string }>('linkedin/connect', {
      companyScoped: true,
    });
  }

  /**
   * Disconnect LinkedIn profile
   */
  public disconnect(): Observable<{ ok: boolean }> {
    return this.apiService.post<{ ok: boolean }>(
      'linkedin/disconnect',
      {},
      { companyScoped: true }
    );
  }

  /**
   * Save bot session credentials (li_at cookie or username/password)
   */
  public saveCredentials(
    payload: LinkedInCredentialsPayload
  ): Observable<{ ok: boolean }> {
    return this.apiService.post<{ ok: boolean }>(
      'linkedin/credentials',
      payload,
      { companyScoped: true }
    );
  }

  /**
   * AI-powered Boolean keyword generator
   */
  public generateKeywords(prompt: string): Observable<GenerateKeywordsResponse> {
    const payload: GenerateKeywordsRequest = { prompt };
    return this.apiService.post<GenerateKeywordsResponse>(
      'linkedin/generate-keywords',
      payload,
      { companyScoped: true }
    );
  }

  /**
   * Search candidate profiles on LinkedIn
   */
  public searchProfiles(
    keywords: string,
    limit: number = 10
  ): Observable<SearchProfilesResponse> {
    const payload: SearchProfilesRequest = { keywords, limit };
    return this.apiService.post<SearchProfilesResponse>(
      'linkedin/search-profiles',
      payload,
      { companyScoped: true }
    );
  }

  /**
   * Batch send invitations to selected profiles
   */
  public sendInvitations(
    request: SendInvitationsRequest
  ): Observable<SendInvitationsResponse> {
    return this.apiService.post<SendInvitationsResponse>(
      'linkedin/send-invitations',
      request,
      { companyScoped: true }
    );
  }
}
