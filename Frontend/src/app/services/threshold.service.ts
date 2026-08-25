import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import { ThresholdSettings, ThresholdUpdateRequest } from '../models/threshold.models';

@Injectable({
  providedIn: 'root',
})
export class ThresholdService {
  constructor(private apiService: ApiService) {}

  /** GET /threshold — current setting plus counts above/below */
  public getThreshold(): Observable<ThresholdSettings> {
    return this.apiService.get<ThresholdSettings>('threshold', {
      companyScoped: true,
    });
  }

  /** PUT /threshold — update and trigger immediate recomputation of all lead flags */
  public updateThreshold(req: ThresholdUpdateRequest): Observable<ThresholdSettings> {
    return this.apiService.put<ThresholdSettings>('threshold', req, {
      companyScoped: true,
    });
  }
}
