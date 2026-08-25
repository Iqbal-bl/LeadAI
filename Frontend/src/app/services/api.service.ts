import { Injectable } from '@angular/core';
import { HttpClient, HttpParams, HttpHeaders } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AuthService } from './auth.service';

export interface ApiOptions {
  headers?: HttpHeaders | Record<string, string | string[]>;
  params?:
    | HttpParams
    | Record<
        string,
        string | number | boolean | readonly (string | number | boolean)[]
      >;
  reportProgress?: boolean;
  responseType?: 'json' | 'text' | 'blob' | 'arraybuffer';
  withCredentials?: boolean;
  companyScoped?: boolean;
  observe?: 'body' | 'response' | 'events';
}

import { environment } from '../../environments/environment';

@Injectable({
  providedIn: 'root',
})
export class ApiService {
  private apiPrefix = environment.apiPrefix;

  constructor(
    private http: HttpClient,
    private authService: AuthService,
  ) {}

  private buildParams(options?: ApiOptions): HttpParams {
    let httpParams = new HttpParams();

    if (options?.params) {
      if (options.params instanceof HttpParams) {
        httpParams = options.params;
      } else {
        Object.keys(options.params).forEach((key) => {
          const val = (options.params as any)[key];
          if (val !== undefined && val !== null) {
            httpParams = httpParams.set(key, String(val));
          }
        });
      }
    }

    // Apply company scoping logic:
    // Platform admins must pass ?client_id={id} on company-scoped endpoints.
    // Company-scoped users should omit client_id.
    if (options?.companyScoped) {
      const isPlatformAdmin = this.authService.isPlatformAdmin();
      const currentCompanyId = this.authService.getSelectedCompanyId();

      if (isPlatformAdmin && currentCompanyId && !httpParams.has('client_id')) {
        httpParams = httpParams.set('client_id', currentCompanyId);
      }
    }

    return httpParams;
  }

  private buildHeaders(options?: ApiOptions): HttpHeaders {
    let headers = new HttpHeaders();
    if (options?.headers) {
      if (options.headers instanceof HttpHeaders) {
        headers = options.headers;
      } else {
        Object.keys(options.headers).forEach((key) => {
          const val = (options.headers as any)[key];
          if (val !== undefined && val !== null) {
            headers = headers.set(key, String(val));
          }
        });
      }
    }
    headers = headers.set('ngrok-skip-browser-warning', 'sadfsd');
    return headers;
  }

  private buildOptions(options?: ApiOptions) {
    return {
      headers: this.buildHeaders(options),
      params: this.buildParams(options),
      reportProgress: options?.reportProgress,
      responseType: (options?.responseType || 'json') as any,
      withCredentials: options?.withCredentials,
      observe: options?.observe as any,
    };
  }

  public get<T>(url: string, options?: ApiOptions): Observable<T> {
    const fullUrl =
      url.startsWith('http') || url.startsWith('/')
        ? url
        : `${this.apiPrefix}/${url}`;
    return this.http.get<T>(fullUrl, this.buildOptions(options) as any) as any as Observable<T>;
  }

  public post<T>(url: string, body: any, options?: ApiOptions): Observable<T> {
    const fullUrl =
      url.startsWith('http') || url.startsWith('/')
        ? url
        : `${this.apiPrefix}/${url}`;
    return this.http.post<T>(fullUrl, body, this.buildOptions(options) as any) as any as Observable<T>;
  }

  public put<T>(url: string, body: any, options?: ApiOptions): Observable<T> {
    const fullUrl =
      url.startsWith('http') || url.startsWith('/')
        ? url
        : `${this.apiPrefix}/${url}`;
    return this.http.put<T>(fullUrl, body, this.buildOptions(options) as any) as any as Observable<T>;
  }

  public patch<T>(url: string, body: any, options?: ApiOptions): Observable<T> {
    const fullUrl =
      url.startsWith('http') || url.startsWith('/')
        ? url
        : `${this.apiPrefix}/${url}`;
    return this.http.patch<T>(fullUrl, body, this.buildOptions(options) as any) as any as Observable<T>;
  }

  public delete<T>(url: string, options?: ApiOptions): Observable<T> {
    const fullUrl =
      url.startsWith('http') || url.startsWith('/')
        ? url
        : `${this.apiPrefix}/${url}`;
    return this.http.delete<T>(fullUrl, this.buildOptions(options) as any) as any as Observable<T>;
  }
}
