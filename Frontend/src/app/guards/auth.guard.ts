import { Injectable, inject } from '@angular/core';
import {
  CanActivate,
  Router,
  ActivatedRouteSnapshot,
  RouterStateSnapshot,
} from '@angular/router';
import { Observable, of } from 'rxjs';
import { catchError, switchMap, map } from 'rxjs/operators';
import { AuthService } from '../services/auth.service';
import { PermissionService } from '../services/permission.service';
import { environment } from '../../environments/environment';

@Injectable({
  providedIn: 'root',
})
export class AuthGuard implements CanActivate {
  private authService = inject(AuthService);
  private router = inject(Router);
  private permissionService = inject(PermissionService);

  canActivate(
    route: ActivatedRouteSnapshot,
    state: RouterStateSnapshot,
  ): Observable<boolean> {
    const accessToken =
      this.authService.getValue('accessToken') ||
      this.authService.getStaffToken();
    if (accessToken) {
      this.authService.setAuthLibAttributes();
      return this.permissionService.fetchPermissions().pipe(
        map(() => true),
        catchError((err) => {
          console.error('Failed to fetch permissions on load:', err);
          return of(true);
        }),
      );
    }

    const authConfig = environment.authConfig;
    const loginPortal = authConfig.issuer || '';

    return this.authService.validateSessionExist().pipe(
      switchMap((response: any) => {
        const status =
          response.body || response.statusText || 'SESSION NOT EXIST';
        this.authService.setLoggedInStatus(status);

        if (status === 'MFA NOT VERIFIED') {
          window.location.href = loginPortal + '/mfa';
          return of(false);
        }

        if (status === 'AUTHORIZED_USER') {
          if (this.authService.getValue('accessToken')) {
            this.authService.setAuthLibAttributes();
            return this.permissionService.fetchPermissions().pipe(
              map(() => true),
              catchError((err) => {
                console.error('Failed to fetch permissions on auth user:', err);
                return of(true);
              }),
            );
          }

          // Trigger redirect login since local storage is empty
          this.authService.initiateOidcLogin();
          return of(false);
        }

        if (status === 'SESSION NOT EXIST') {
          this.authService.initiateOidcLogin();
          return of(false);
        }

        return of(true);
      }),
      catchError((error) => {
        console.error('Error in validateSessionExist', error);
        this.authService.initiateOidcLogin();
        return of(false);
      }),
    );
  }
}
