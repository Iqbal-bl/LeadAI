import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import {
  DataPoint,
  CreateDataPointPayload,
  UpdateDataPointPayload,
} from '../models/data-point.models';

@Injectable({
  providedIn: 'root',
})
export class DataPointService {
  constructor(private apiService: ApiService) {}

  /**
   * GET /data-points?include_inactive=false
   */
  public getDataPoints(includeInactive: boolean = false): Observable<DataPoint[]> {
    return this.apiService.get<DataPoint[]>('data-points', {
      params: { include_inactive: includeInactive },
      companyScoped: true,
    });
  }

  /**
   * POST /data-points
   */
  public createDataPoint(payload: CreateDataPointPayload): Observable<DataPoint> {
    return this.apiService.post<DataPoint>('data-points', payload, {
      companyScoped: true,
    });
  }

  /**
   * PATCH /data-points/{id}
   */
  public updateDataPoint(
    id: string,
    payload: UpdateDataPointPayload,
  ): Observable<DataPoint> {
    return this.apiService.patch<DataPoint>(`data-points/${id}`, payload, {
      companyScoped: true,
    });
  }

  /**
   * DELETE /data-points/{id}
   */
  public deleteDataPoint(id: string): Observable<{ success: boolean; message?: string }> {
    return this.apiService.delete<{ success: boolean; message?: string }>(
      `data-points/${id}`,
      {
        companyScoped: true,
      },
    );
  }
}
