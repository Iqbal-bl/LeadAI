import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import {
  Channel,
  ChannelCreateRequest,
  ChannelCreateResponse,
  ChannelUpdateRequest,
  ChannelContact,
  ChannelStatus,
  ChannelTestRequest,
  ChannelTestResponse,
  ChannelDeleteResponse,
} from '../models/channel.models';

@Injectable({
  providedIn: 'root',
})
export class ChannelService {
  constructor(private apiService: ApiService) {}

  /** GET /channels — list all channels */
  public getChannels(): Observable<Channel[]> {
    return this.apiService.get<Channel[]>('channels', {
      companyScoped: true,
    });
  }

  /** GET /channels/status — dashboard summary / channel health */
  public getChannelStatus(): Observable<ChannelStatus> {
    return this.apiService.get<ChannelStatus>('channels/status', {
      companyScoped: true,
    });
  }

  /** POST /channels — create a new channel connection */
  public createChannel(req: ChannelCreateRequest): Observable<ChannelCreateResponse> {
    return this.apiService.post<ChannelCreateResponse>('channels', req, {
      companyScoped: true,
    });
  }

  /** PATCH /channels/{account_id} — update channel settings */
  public updateChannel(
    accountId: string,
    req: ChannelUpdateRequest,
  ): Observable<Channel> {
    return this.apiService.patch<Channel>(`channels/${accountId}`, req, {
      companyScoped: true,
    });
  }

  /** DELETE /channels/{account_id} — disconnect/delete a channel */
  public deleteChannel(accountId: string): Observable<ChannelDeleteResponse> {
    return this.apiService.delete<ChannelDeleteResponse>(`channels/${accountId}`, {
      companyScoped: true,
    });
  }

  /** POST /channels/{account_id}/test — send test message to verify channel */
  public testChannel(
    accountId: string,
    req: ChannelTestRequest,
  ): Observable<ChannelTestResponse> {
    return this.apiService.post<ChannelTestResponse>(
      `channels/${accountId}/test`,
      req,
      {
        companyScoped: true,
      },
    );
  }

  /** GET /channels/{account_id}/contacts — people who messaged this channel */
  public getChannelContacts(
    accountId: string,
    page: number = 1,
    pageSize: number = 50,
  ): Observable<ChannelContact[] | any> {
    return this.apiService.get<ChannelContact[] | any>(
      `channels/${accountId}/contacts`,
      {
        params: {
          page,
          page_size: pageSize,
        },
        companyScoped: true,
      },
    );
  }

  /** Helper to toggle auto_reply */
  public toggleAutoReply(accountId: string, autoReply: boolean): Observable<Channel> {
    return this.updateChannel(accountId, { auto_reply: autoReply });
  }

  /** Helper to toggle is_active */
  public toggleActiveStatus(accountId: string, isActive: boolean): Observable<Channel> {
    return this.updateChannel(accountId, { is_active: isActive });
  }
}

