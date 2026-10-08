import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import {
  LinkedInStatus,
  LinkedInAccount,
  LinkedInCredentialsPayload,
  GenerateKeywordsRequest,
  GenerateKeywordsResponse,
  SearchProfilesRequest,
  SearchProfilesResponse,
  SendInvitationsRequest,
  SendInvitationsResponse,
  GetInvitationsResponse,
  LinkedInReplyInvitationPayload,
  BatchAcceptResponse,
  LinkedInSettingsPayload,
  GetConversationsResponse,
  GetConversationMessagesResponse,
  SendMessageResponse,
  SyncMessagesResponse,
  LinkedInCommentSettings,
  GetCommentsResponse,
  SyncCommentsResponse,
  LinkedInAutoConnectSettings,
  GetAutoConnectSettingsResponse,
  SaveAutoConnectSettingsResponse,
  TriggerAutoConnectResponse,
} from '../models/linkedin.models';

@Injectable({
  providedIn: 'root',
})
export class LinkedinService {
  constructor(private apiService: ApiService) {}

  /**
   * Check connection status of company LinkedIn account(s)
   */
  public getStatus(): Observable<LinkedInStatus> {
    return this.apiService.get<LinkedInStatus>('linkedin/status', {
      companyScoped: true,
    });
  }

  /**
   * List all connected LinkedIn accounts for the active company
   */
  public getAccounts(): Observable<{ accounts: LinkedInAccount[]; total: number }> {
    return this.apiService.get<{ accounts: LinkedInAccount[]; total: number }>(
      'linkedin/accounts',
      { companyScoped: true }
    );
  }

  /**
   * Retrieve LinkedIn OAuth 2.0 authorization URL
   */
  public getConnectUrl(prompt?: string): Observable<{ authorize_url: string }> {
    const params: any = {};
    if (prompt) {
      params['prompt'] = prompt;
    }
    return this.apiService.get<{ authorize_url: string }>('linkedin/connect', {
      companyScoped: true,
      params,
    });
  }

  /**
   * Disconnect a specific LinkedIn account profile by ID
   */
  public disconnectAccount(accountId: string): Observable<{ ok: boolean; message?: string }> {
    return this.apiService.delete<{ ok: boolean; message?: string }>(
      `linkedin/accounts/${encodeURIComponent(accountId)}`,
      { companyScoped: true }
    );
  }

  /**
   * Disconnect LinkedIn profile (legacy / all)
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
   * Disconnect / remove personal session cookie and credentials
   */
  public disconnectCredentials(): Observable<{ ok: boolean; message?: string }> {
    return this.apiService.post<{ ok: boolean; message?: string }>(
      'linkedin/credentials/disconnect',
      {},
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

  /**
   * Fetch received pending LinkedIn invitations
   */
  public getInvitations(limit: number = 50): Observable<GetInvitationsResponse> {
    return this.apiService.get<GetInvitationsResponse>('linkedin/invitations', {
      params: { limit: limit.toString() },
      companyScoped: true,
    });
  }

  /**
   * Accept or reject a received LinkedIn connection request
   */
  public replyInvitation(
    payload: LinkedInReplyInvitationPayload
  ): Observable<{ success: boolean; action: string; message?: string }> {
    return this.apiService.post<{ success: boolean; action: string; message?: string }>(
      'linkedin/invitations/reply',
      payload,
      { companyScoped: true }
    );
  }

  /**
   * Accept all pending received invitations in batch
   */
  public acceptAllInvitations(): Observable<BatchAcceptResponse> {
    return this.apiService.post<BatchAcceptResponse>(
      'linkedin/invitations/accept-all',
      {},
      { companyScoped: true }
    );
  }

  /**
   * Save auto-accept and welcome message automation settings
   */
  public saveSettings(
    payload: LinkedInSettingsPayload
  ): Observable<{ ok: boolean }> {
    return this.apiService.post<{ ok: boolean }>(
      'linkedin/settings',
      payload,
      { companyScoped: true }
    );
  }

  /**
   * Trigger immediate background synchronization of connection requests
   */
  public syncInvitations(): Observable<{ ok: boolean; message: string }> {
    return this.apiService.post<{ ok: boolean; message: string }>(
      'linkedin/sync-invitations',
      {},
      { companyScoped: true }
    );
  }

  /**
   * Fetch recent LinkedIn conversation threads
   */
  public getConversations(limit: number = 25): Observable<GetConversationsResponse> {
    return this.apiService.get<GetConversationsResponse>('linkedin/conversations', {
      params: { limit: limit.toString() },
      companyScoped: true,
    });
  }

  /**
   * Fetch message history for a specific LinkedIn thread
   */
  public getConversationMessages(
    conversationUrnId: string,
    loadEarlier: boolean = false
  ): Observable<GetConversationMessagesResponse> {
    return this.apiService.get<GetConversationMessagesResponse>(
      `linkedin/conversations/${encodeURIComponent(conversationUrnId)}/messages`,
      {
        params: loadEarlier ? { load_earlier: 'true' } : {},
        companyScoped: true,
      }
    );
  }

  /**
   * Send a direct message to a LinkedIn conversation thread
   */
  public sendMessage(
    conversationUrnId: string,
    message: string
  ): Observable<SendMessageResponse> {
    return this.apiService.post<SendMessageResponse>(
      `linkedin/conversations/${encodeURIComponent(conversationUrnId)}/send`,
      { message },
      { companyScoped: true }
    );
  }

  /**
   * Sync LinkedIn conversations and messages to database
   */
  public syncMessages(): Observable<SyncMessagesResponse> {
    return this.apiService.post<SyncMessagesResponse>(
      'linkedin/sync-messages',
      {},
      { companyScoped: true }
    );
  }

  // =========================================================================
  // Comments & AI Replies Automation
  // =========================================================================

  /**
   * Fetch company's comment automation settings
   */
  public getCommentSettings(): Observable<LinkedInCommentSettings> {
    return this.apiService.get<LinkedInCommentSettings>('linkedin/comments/settings', {
      companyScoped: true,
    });
  }

  /**
   * Update comment automation settings
   */
  public updateCommentSettings(
    settings: LinkedInCommentSettings
  ): Observable<{ ok: boolean; message: string }> {
    return this.apiService.post<{ ok: boolean; message: string }>(
      'linkedin/comments/settings',
      settings,
      { companyScoped: true }
    );
  }

  /**
   * List comments with optional status or intent filtering
   */
  public getComments(filters?: {
    status_filter?: string;
    sentiment?: string;
    is_lead_only?: boolean;
    limit?: number;
  }): Observable<GetCommentsResponse> {
    const params: Record<string, string> = {};
    if (filters?.status_filter) params['status_filter'] = filters.status_filter;
    if (filters?.sentiment) params['sentiment'] = filters.sentiment;
    if (filters?.is_lead_only) params['is_lead_only'] = 'true';
    if (filters?.limit) params['limit'] = filters.limit.toString();

    return this.apiService.get<GetCommentsResponse>('linkedin/comments', {
      params,
      companyScoped: true,
    });
  }

  /**
   * Generate or regenerate AI reply for a comment
   */
  public generateCommentReply(
    commentId: string,
    customInstruction?: string
  ): Observable<{
    ok: boolean;
    suggested_reply: string;
    rationale: string;
    sentiment: string;
    intent_score: number;
    is_lead_candidate: boolean;
  }> {
    return this.apiService.post<{
      ok: boolean;
      suggested_reply: string;
      rationale: string;
      sentiment: string;
      intent_score: number;
      is_lead_candidate: boolean;
    }>(
      `linkedin/comments/${encodeURIComponent(commentId)}/generate-reply`,
      { custom_instruction: customInstruction || null },
      { companyScoped: true }
    );
  }

  /**
   * Approve and post reply to LinkedIn
   */
  public postCommentReply(
    commentId: string,
    replyText: string
  ): Observable<{ ok: boolean; message: string; reply_urn?: string }> {
    return this.apiService.post<{ ok: boolean; message: string; reply_urn?: string }>(
      `linkedin/comments/${encodeURIComponent(commentId)}/reply`,
      { reply_text: replyText },
      { companyScoped: true }
    );
  }

  /**
   * Ignore a comment
   */
  public ignoreComment(commentId: string): Observable<{ ok: boolean; message: string }> {
    return this.apiService.post<{ ok: boolean; message: string }>(
      `linkedin/comments/${encodeURIComponent(commentId)}/ignore`,
      {},
      { companyScoped: true }
    );
  }

  /**
   * Convert commenter into CRM Lead
   */
  public captureCommentLead(
    commentId: string
  ): Observable<{ ok: boolean; message: string; customer_id?: string; display_name?: string; linkedin_profile_url?: string | null }> {
    return this.apiService.post<{ ok: boolean; message: string; customer_id?: string; display_name?: string; linkedin_profile_url?: string | null }>(
      `linkedin/comments/${encodeURIComponent(commentId)}/capture-lead`,
      {},
      { companyScoped: true }
    );
  }



  /**
   * Poll LinkedIn for latest post comments and trigger AI reply generation
   */
  public syncComments(): Observable<SyncCommentsResponse> {
    return this.apiService.post<SyncCommentsResponse>(
      'linkedin/comments/sync',
      {},
      { companyScoped: true }
    );
  }

  /**
   * Fetch automated candidate search & connection scheduler settings
   */
  public getAutoConnectSettings(): Observable<GetAutoConnectSettingsResponse> {
    return this.apiService.get<GetAutoConnectSettingsResponse>(
      'linkedin/auto-connect/settings',
      { companyScoped: true }
    );
  }

  /**
   * Update automated candidate search & connection scheduler settings
   */
  public saveAutoConnectSettings(
    payload: LinkedInAutoConnectSettings
  ): Observable<SaveAutoConnectSettingsResponse> {
    return this.apiService.post<SaveAutoConnectSettingsResponse>(
      'linkedin/auto-connect/settings',
      payload,
      { companyScoped: true }
    );
  }

  /**
   * Trigger immediate test execution of auto-connect job
   */
  public triggerAutoConnectNow(): Observable<TriggerAutoConnectResponse> {
    return this.apiService.post<TriggerAutoConnectResponse>(
      'linkedin/auto-connect/run-now',
      {},
      { companyScoped: true }
    );
  }
}



