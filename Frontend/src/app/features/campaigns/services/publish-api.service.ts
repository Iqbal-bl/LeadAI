import { Injectable } from '@angular/core';

import { HttpClient, HttpHeaders } from '@angular/common/http';

import { Observable } from 'rxjs';

import { UnifiedPublishRequest } from './publish.services';

@Injectable({
  providedIn: 'root',
})
export class PublishApiService {
  private readonly apiUrl = 'https://sporting-zombie-kennel.ngrok-free.dev';

  private readonly apiKey = 'changeme123';

  constructor(private http: HttpClient) {}

  publish(data: UnifiedPublishRequest): Observable<any> {
    const headers = new HttpHeaders({
      'X-API-Key': this.apiKey,
      'Content-Type': 'application/json',
    });

    return this.http.post(`${this.apiUrl}/direct/posts`, data, {
      headers,
    });
  }
}
