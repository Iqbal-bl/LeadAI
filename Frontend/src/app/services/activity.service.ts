import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import { ActivityResponse } from '../models/activity.models';

export interface ActivityQueryParams {
  action?: string;
  action_prefix?: string;
  log_type?: 'Security' | 'Warning' | 'Error' | string;
  entity_type?: string;
  entity_id?: string;
  actor_email?: string;
  since?: string;
  until?: string;
  page?: number;
  page_size?: number;
}

@Injectable({
  providedIn: 'root',
})
export class ActivityService {
  constructor(private apiService: ApiService) {}

  // GET /activity
  public getActivity(
    params?: ActivityQueryParams,
  ): Observable<ActivityResponse> {
    return this.apiService.get<ActivityResponse>('activity', {
      params: params as any,
      companyScoped: true,
    });
  }

  // GET /activity/actions
  public getActions(): Observable<string[]> {
    return this.apiService.get<string[]>('activity/actions', {
      companyScoped: true,
    });
  }
}
