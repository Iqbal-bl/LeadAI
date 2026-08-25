import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { BehaviorSubject, Observable, tap, throwError } from 'rxjs';
import { UserMe } from '../models/auth.models';
import {
  ROLE_COMPANY_ADMIN,
  ROLE_EMPLOYEE,
  ROLE_MANAGER,
} from '../shared/constants/role.constants';

import { environment } from '../../environments/environment';
import { CommonLibService } from './common-lib.service';

const STORAGE_KEYS = {
  accessToken: 'accessToken',
  refreshToken: 'refreshToken',
  codeVerifier: 'codeVerifier',
  accessTokenLastUpdatedAt: 'accessTokenLastUpdatedAt',
  sessionId: 'sessionId',
  expiresIn: 'expiresIn',
};

@Injectable({
  providedIn: 'root',
})
export class AuthService {
  private readonly TOKEN_KEY = 'leadai_staff_token';
  private readonly COMPANY_KEY = 'leadai_selected_client_id';
  private readonly WIDGET_TOKEN_KEY = 'leadai_widget_token';

  private currentUserSubject = new BehaviorSubject<UserMe | null>(null);
  public currentUser$ = this.currentUserSubject.asObservable();

  private selectedCompanyIdSubject = new BehaviorSubject<string | null>(null);
  public selectedCompanyId$ = this.selectedCompanyIdSubject.asObservable();

  public decodedToken: any;
  public userName: any;
  public loggedInStatus: any;

  constructor(
    private http: HttpClient,
    private commonLibService: CommonLibService,
  ) {
    // Rehydrate token and company on startup
    const storedCompany = localStorage.getItem(this.COMPANY_KEY);
    if (storedCompany) {
      this.selectedCompanyIdSubject.next(storedCompany);
    }

    // Rehydrate active session if available
    const storedToken =
      this.getValue(STORAGE_KEYS.accessToken) || this.getStaffToken();
    if (storedToken) {
      this.setStaffToken(storedToken);
      this.setAuthLibAttributes();
    }
  }

  // Get staff token from storage
  public getStaffToken(): string | null {
    return localStorage.getItem(this.TOKEN_KEY);
  }

  // Get customer widget session token
  public getWidgetToken(): string | null {
    return localStorage.getItem(this.WIDGET_TOKEN_KEY);
  }

  // Set staff token in storage
  public setStaffToken(token: string): void {
    localStorage.setItem(this.TOKEN_KEY, token);
  }

  // Set customer widget session token
  public setWidgetToken(token: string): void {
    localStorage.setItem(this.WIDGET_TOKEN_KEY, token);
  }

  // Get current selected company ID
  public getSelectedCompanyId(): string | null {
    return this.selectedCompanyIdSubject.value;
  }

  // Switch selected company
  public setSelectedCompanyId(companyId: string | null): void {
    if (companyId) {
      localStorage.setItem(this.COMPANY_KEY, companyId);
    } else {
      localStorage.removeItem(this.COMPANY_KEY);
    }
    this.selectedCompanyIdSubject.next(companyId);
  }

  // Get current user role
  public getUserRole(): string | null {
    return this.currentUserSubject.value?.role || null;
  }

  // Helper to check if current user is platform admin
  public isPlatformAdmin(): boolean {
    return this.getUserRole() === 'platform_admin';
  }

  // Session state getter/setter
  public getLoggedInStatus(): any {
    return this.loggedInStatus;
  }

  public setLoggedInStatus(status: any): void {
    this.loggedInStatus = status;
  }

  // Check if admin user
  public isAdmin(): boolean {
    const role = this.getUserRole();
    return (
      role?.toLowerCase() === 'admin' ||
      role?.toLowerCase() === 'platform_admin'
    );
  }

  // Initiate OIDC login flow using redirect
  public async initiateOidcLogin(): Promise<void> {
    const url = await this.buildAuthorizeUrl(environment.authConfig);
    window.location.assign(url);
  }

  // Initiate authorization code PKCE flow authorization URL builder
  public async buildAuthorizeUrl(authConfig: any): Promise<string> {
    const stateIn = this.commonLibService.generateRandomString(10);
    // generateCodeChallenge automatically generates, saves, and returns the challenge
    const code_challenge = await this.commonLibService.generateCodeChallenge();

    const path = environment.production
      ? 'oauth/authorize'
      : 'connect/authorize';
    const baseUrl = `${authConfig.issuer}/${path}?client_id=${authConfig.clientId}&redirect_uri=${encodeURIComponent(authConfig.loginRedirectUri)}&response_type=code&state=${stateIn}&identityToken=&version=v2.0`;

    return !authConfig.pkce
      ? baseUrl
      : baseUrl +
          '&code_challenge=' +
          code_challenge +
          '&code_challenge_method=S256&scope=openid profile api1 offline_access roles';
  }

  // Generate the oauth token using code
  public async getTokenwithAuthCodeGrant(code: string): Promise<any> {
    let headers = new HttpHeaders().set(
      'Content-Type',
      'application/x-www-form-urlencoded; charset=utf-8',
    );

    const authConfig = environment.authConfig;

    if (!authConfig.pkce) {
      headers = headers.set(
        'Authorization',
        'Basic ' + btoa(authConfig.clientId + ':' + authConfig.clientSecret),
      );
    }

    const codeVerifier = this.getValue(STORAGE_KEYS.codeVerifier) ?? '';
    const params = new URLSearchParams();
    params.set('grant_type', 'authorization_code');
    params.set('code', code);
    params.set('redirect_uri', authConfig.loginRedirectUri);
    if (authConfig.pkce) {
      params.set('client_id', authConfig.clientId);
      params.set('code_verifier', codeVerifier);
    }

    const path = environment.production ? 'oauth/token' : 'connect/token';

    return await this.http
      .post<any>(`${authConfig.issuer}/${path}`, params.toString(), {
        headers: headers,
      })
      .toPromise()
      .then(
        (data: {
          access_token: string;
          refresh_token: string;
          sId: string;
          expires_in: number;
        }) => {
          this.setValue(STORAGE_KEYS.accessToken, data.access_token);
          this.setValue(STORAGE_KEYS.refreshToken, data.refresh_token);
          this.setValue(
            STORAGE_KEYS.accessTokenLastUpdatedAt,
            String(new Date()),
          );
          this.setValue(STORAGE_KEYS.sessionId, data.sId);
          this.setValue(STORAGE_KEYS.expiresIn, data.expires_in);

          this.setStaffToken(data.access_token);
          this.setAuthLibAttributes();
          return data;
        },
      )
      .catch((error) => {
        return error;
      });
  }

  // revoke token
  public revokeTokens(accessToken: any) {
    const options = {
      headers: new HttpHeaders().set('Content-Type', 'text/plain'),
      responseType: 'text' as const,
    };
    if (accessToken) {
      this.http
        .post(
          environment.authConfig.issuer +
            '/tokens/revoke/access_token' +
            '?ngsw-bypass=true',
          accessToken,
          options,
        )
        .subscribe(
          (data: any) => {},
          (error: any) => console.log(error),
        );
    }
  }

  // refresh the token
  public refreshTokens(): Observable<any> {
    const refreshToken = this.getValue(STORAGE_KEYS.refreshToken);
    if (refreshToken) {
      const params = new URLSearchParams();
      params.append('grant_type', 'refresh_token');
      params.append('refresh_token', refreshToken);

      const path = environment.production ? 'oauth/token' : 'connect/token';

      return this.http
        .post(
          environment.authConfig.issuer + `/${path}` + '?ngsw-bypass=true',
          params.toString(),
        )
        .pipe(
          tap((data: any) => {
            if (data && data.access_token) {
              this.setValue(STORAGE_KEYS.accessToken, data.access_token);
              this.setStaffToken(data.access_token);
              if (data.refresh_token) {
                this.setValue(STORAGE_KEYS.refreshToken, data.refresh_token);
              }
              this.setAuthLibAttributes();
            }
          }),
        );
    } else {
      return throwError(() => new Error('No refresh token found!'));
    }
  }

  // check the session state
  public checkSession(): Observable<any> {
    const options = {
      params: new HttpParams()
        .set('sessionId', this.getValue(STORAGE_KEYS.sessionId)!)
        .set('ngsw-bypass', 'true'),
      responseType: 'text' as const,
    };
    return this.http.get(
      environment.authConfig.checkSessionApi + '/status',
      options,
    );
  }

  // get the session status using cookie
  public validateSessionExist(): Observable<any> {
    const httpOptions = {
      headers: new HttpHeaders().set('Content-Type', 'text/plain'),
      responseType: 'text' as const,
      withCredentials: true,
      observe: 'response' as 'response',
    };
    const urlPrefix =
      environment.authConfig.checkSessionApi || environment.apiPrefix;
    return this.http.get(
      `${urlPrefix}/auth/${environment.authConfig.clientId}`,
      httpOptions,
    );
  }

  // refresh the session
  public refreshSession(): Observable<any> {
    const options = {
      params: new HttpParams()
        .set('sessionId', this.getValue(STORAGE_KEYS.sessionId)!)
        .set('ngsw-bypass', 'true'),
    };
    return this.http.get(
      environment.authConfig.issuer + '/connect/revocation',
      options,
    );
  }

  // mock login fallback
  public login(email: string, password?: string): Observable<any> {
    const role = email.includes('admin')
      ? ROLE_COMPANY_ADMIN
      : email.includes('manager')
        ? ROLE_MANAGER
        : email.includes('operator')
          ? 'ai_operator'
          : ROLE_EMPLOYEE;
    return this.loginWithRole(role);
  }

  public loginWithRole(role: string): Observable<any> {
    const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
    const payload = btoa(
      JSON.stringify({
        user_name: `${role}_demo`,
        email: `${role}@leadai.com`,
        role: role,
        client_id: 'company-1',
        client_name: 'LeadAI Demo Tenant',
        permissions: ['read', 'write'],
        accessible_companies: [
          { id: 'company-1', name: 'TechCorp Solutions', is_active: true },
          { id: 'company-2', name: 'GlobalFin Partners', is_active: true },
        ],
      }),
    );
    const mockToken = `${header}.${payload}.signature`;

    this.setValue('accessToken', mockToken);
    this.setStaffToken(mockToken);
    this.setAuthLibAttributes();

    return this.currentUser$;
  }

  // local clear
  public logout(): void {
    localStorage.removeItem(this.TOKEN_KEY);
    localStorage.removeItem(this.COMPANY_KEY);
    this.removeValue(STORAGE_KEYS.accessToken);
    this.removeValue(STORAGE_KEYS.refreshToken);
    this.removeValue(STORAGE_KEYS.sessionId);
    this.currentUserSubject.next(null);
    this.selectedCompanyIdSubject.next(null);
    this.logoutRedirect();
  }

  // OIDC Redirect logout
  public logoutRedirect() {
    const idToken = this.getValue('id_token') || '';
    const endsessionPath = environment.production
      ? 'oauth/endsession'
      : 'connect/endsession';
    // this.logout();
    window.location.href = `${
      environment.authConfig.issuer
    }/${endsessionPath}?id_token_hint=${idToken}&post_logout_redirect_uri=${encodeURIComponent(environment.authConfig.postLogoutRedirectUri)}`;
  }

  // set auth attributes by decoding token
  public async setAuthLibAttributes() {
    const token = this.getValue(STORAGE_KEYS.accessToken);
    if (token) {
      this.decodedToken = this.jwtDecode(token);
      if (this.decodedToken) {
        this.userName = this.decodedToken.user_name;

        const user: UserMe = {
          email: this.decodedToken.email || this.decodedToken.user_name || '',
          full_name:
            this.decodedToken.full_name || this.decodedToken.user_name || '',
          role: this.decodedToken.role || 'agent',
          client_id: this.decodedToken.client_id || '',
          client_name: this.decodedToken.client_name || '',
          permissions: this.decodedToken.permissions || [],
          accessible_companies: this.decodedToken.accessible_companies || [],
        };
        this.currentUserSubject.next(user);
        this.checkSessionPeriodically();
      }
    }
  }

  public setCurrentUser(user: UserMe): void {
    this.currentUserSubject.next(user);
  }

  public getCurrentUser(): UserMe | null {
    return this.currentUserSubject.value;
  }

  private checkSessionPeriodically() {
    // Periodical check if needed
  }

  // Session storage storage helpers
  public setValue(key: string, value: any): void {
    sessionStorage.setItem(key, value);
  }

  public getValue(key: string): any {
    return sessionStorage.getItem(key);
  }

  public removeValue(key: string): void {
    sessionStorage.removeItem(key);
  }

  // Decodes client-side JWT token natively
  public jwtDecode(token: string): any {
    try {
      const parts = token.split('.');
      if (parts.length !== 3) return null;
      const payload = parts[1];
      const decoded = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
      return JSON.parse(decoded);
    } catch (e) {
      return null;
    }
  }

  // GET /access/me
  public getAccessMe(): Observable<UserMe> {
    return this.http.get<UserMe>('access/me').pipe(
      tap((user) => {
        this.currentUserSubject.next(user);

        const currentCompanyId = this.getSelectedCompanyId();
        const hasAccess = user.accessible_companies.some(
          (c: any) => c.id === currentCompanyId,
        );

        if (!currentCompanyId || !hasAccess) {
          if (user.accessible_companies.length > 0) {
            this.setSelectedCompanyId(user.accessible_companies[0].id);
          } else {
            this.setSelectedCompanyId(user.client_id || null);
          }
        }
      }),
    );
  }

  // Instagram OAuth: Get authorization URL & state
  public getInstagramAuthorizeUrl(): Observable<{
    authorize_url: string;
    state: string;
  }> {
    return this.http.get<{ authorize_url: string; state: string }>(
      `${environment.apiPrefix}/channels/instagram/connect`,
      {
        headers: {
          'ngrok-skip-browser-warning': 'sdf',
        },
      },
    );
  }

  // Instagram OAuth: Initiate login redirect
  public initiateInstagramAuth(): void {
    this.getInstagramAuthorizeUrl().subscribe({
      next: (res) => {
        if (res && res.authorize_url) {
          window.location.assign(res.authorize_url);
        }
      },
      error: (err) => {
        console.error('Failed to get Instagram authorize URL', err);
      },
    });
  }

  // Instagram OAuth: Handle callback with code & state
  public handleInstagramCallback(payload: {
    code: string;
    state: string;
  }): Observable<any> {
    return this.http.post<any>(
      `${environment.apiPrefix}/channels/instagram/callback`,
      payload,
    );
  }

  // LinkedIn OAuth: Get authorization URL
  public getLinkedInConnectUrl(): Observable<{ authorize_url: string }> {
    return this.http.get<{ authorize_url: string }>(
      `${environment.apiPrefix}/channels/linkedin/connect`,
      {
        headers: {
          'ngrok-skip-browser-warning': 'sdf',
        },
      },
    );
  }

  // LinkedIn OAuth: Get connection status
  public getLinkedInStatus(): Observable<{
    connected: boolean;
    person_urn?: string;
    access_token_valid?: boolean;
    has_refresh_token?: boolean;
  }> {
    return this.http.get<{
      connected: boolean;
      person_urn?: string;
      access_token_valid?: boolean;
      has_refresh_token?: boolean;
    }>(`${environment.apiPrefix}/channels/linkedin/status`, {
      headers: {
        'ngrok-skip-browser-warning': 'sdf',
      },
    });
  }

  // LinkedIn OAuth: Disconnect
  public disconnectLinkedIn(): Observable<{ ok: boolean }> {
    return this.http.post<{ ok: boolean }>(
      `${environment.apiPrefix}/channels/linkedin/disconnect`,
      {},
    );
  }
}
