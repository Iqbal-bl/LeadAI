import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import {
  AnalyticsData,
  AnalyticsFunnelStage,
} from '../models/analytics.models';

@Injectable({
  providedIn: 'root',
})
export class AnalyticsService {
  constructor(private apiService: ApiService) {}

  // GET /analytics
  public getAnalytics(days: number = 7): Observable<AnalyticsData> {
    return this.apiService.get<AnalyticsData>('analytics', {
      params: { days },
      companyScoped: true,
    });
  }

  // GET /analytics/funnel
  public getFunnel(): Observable<AnalyticsFunnelStage[]> {
    return this.apiService.get<AnalyticsFunnelStage[]>('analytics/funnel', {
      companyScoped: true,
    });
  }
}
