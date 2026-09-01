import { Injectable, Injector } from '@angular/core';
import {
  HttpInterceptor,
  HttpRequest,
  HttpHandler,
  HttpEvent,
  HttpErrorResponse,
  HttpHeaders,
} from '@angular/common/http';
import { Observable, BehaviorSubject, throwError } from 'rxjs';
import { catchError, filter, take, switchMap } from 'rxjs/operators';
import { AuthService } from '../services/auth.service';
import { environment } from '../../environments/environment';
import { ToastService } from '../shared/services/toast.service';
import { JsonPipe } from '@angular/common';

@Injectable()
export class AuthInterceptor implements HttpInterceptor {
  private refreshTokenInProgress = false;
  private refreshTokenSubject: BehaviorSubject<any> = new BehaviorSubject<any>(
    null,
  );

  constructor(
    private authService: AuthService,
    private toast: ToastService,
  ) {}

  intercept(
    req: HttpRequest<any>,
    next: HttpHandler,
  ): Observable<HttpEvent<any>> {
    const url = req.url;
    const authConfig = environment.authConfig;

    // Issuer authorization headers handling (oauth endpoints)
    if (
      authConfig &&
      authConfig.issuer &&
      url.includes(authConfig.issuer) &&
      !url.includes('api/v') &&
      !url.includes('check-session/status') &&
      !req.headers.has('Content-Type')
    ) {
      let headers = req.headers.set(
        'Content-Type',
        'application/x-www-form-urlencoded; charset=utf-8',
      );

      if (!authConfig.pkce) {
        headers = headers.set(
          'Authorization',
          'Basic ' +
            btoa(`${authConfig.clientId}:${authConfig.clientSecret || ''}`),
        );
      }
      req = req.clone({ headers });
    }

    // Check public paths that do not require any authentication
    // e.g. GET /health, POST /voice/exotel/status, /api/leadai/public/*
    const isPublicPath =
      url.includes('/api/leadai/health') ||
      url.endsWith('/health') ||
      url.includes('/voice/exotel/status') ||
      (url.includes('/api/leadai/public/') && !url.includes('/public/chat/')) ||
      (url.includes('/public/') && !url.includes('/public/chat/'));

    if (isPublicPath) {
      return next.handle(req);
    }

    // Check customer widget session paths
    // e.g. /api/leadai/public/chat/* or /public/chat/*
    const isCustomerWidgetPath = url.includes('/public/chat/');
    if (isCustomerWidgetPath) {
      const widgetToken = this.authService.getWidgetToken();
      if (widgetToken) {
        req = req.clone({
          headers: req.headers.set('X-Chat-Session', widgetToken),
        });
      }
      return next.handle(req);
    }

    // Staff auth path for everything else
    const accessToken =
      this.authService.getValue('accessToken') ||
      this.authService.getStaffToken();
    const setHeaders: Record<string, string> = {
      'ngrok-skip-browser-warning': 'true',
    };
    if (accessToken) {
      setHeaders['Authorization'] = `Bearer ${accessToken}`;
    }
    req = req.clone({ setHeaders });


    return next.handle(req).pipe(
      catchError((err: HttpErrorResponse) => {
        if (
          err.status !== 401 ||
          url.includes('oauth/token') ||
          url.includes('connect/token')
        ) {
          console.log(err as any | JsonPipe);

          this.toast.error(err.error.detail, 'Error');
          return throwError(() => err);
        }

        if (this.refreshTokenInProgress) {
          return this.refreshTokenSubject.pipe(
            filter((token) => token != null),
            take(1),
            switchMap((token: any) => {
              return next.handle(
                req.clone({
                  setHeaders: {
                    Authorization: `Bearer ${token.access_token}`,
                  },
                }),
              );
            }),
          );
        }

        const accessTokenLastUpdatedAt = this.authService.getValue(
          'accessTokenLastUpdatedAt',
        );
        const expiresIn = this.authService.getValue('expiresIn');

        if (
          accessTokenLastUpdatedAt != null &&
          expiresIn != null &&
          new Date().getTime() - Date.parse(accessTokenLastUpdatedAt) <
            Number(expiresIn) * 1000
        ) {
          console.log('token not expired');
          return throwError(() => err);
        }

        this.refreshTokenInProgress = true;
        this.refreshTokenSubject.next(null);

        const refreshToken$ = this.authService.refreshTokens();

        return refreshToken$.pipe(
          switchMap((data: any) => {
            this.authService.setValue('accessToken', data.access_token);
            if (data.refresh_token) {
              this.authService.setValue('refreshToken', data.refresh_token);
            }
            this.authService.setValue(
              'accessTokenLastUpdatedAt',
              String(new Date()),
            );
            this.authService.setValue('expiresIn', data.expires_in);

            this.authService.setStaffToken(data.access_token);

            this.refreshTokenInProgress = false;
            this.refreshTokenSubject.next(data);

            return next.handle(
              req.clone({
                setHeaders: {
                  Authorization: `Bearer ${data.access_token}`,
                },
              }),
            );
          }),
          catchError((error) => {
            this.refreshTokenInProgress = false;
            this.authService.logout();
            return throwError(() => error);
          }),
        );
      }),
    );
  }
}
