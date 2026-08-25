import { Component, OnInit, inject } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { AuthService } from '../../../services/auth.service';
import { PermissionService } from '../../../services/permission.service';
import { CommonLibService } from '../../../services/common-lib.service';
import { environment } from '../../../../environments/environment';
import { first } from 'rxjs';

const STORAGE_KEYS = {
  accessToken: 'accessToken',
  refreshToken: 'refreshToken',
  codeValue: 'codeValue',
  stateValue: 'stateValue',
};

import { SharedModule } from '../../../shared/shared.module';

@Component({
  selector: 'app-callback',
  standalone: true,
  imports: [SharedModule],
  template: `
    <div class="callback-container">
      <div class="spinner"></div>
      <p class="status-text">Completing login redirect... {{ error || '' }}</p>
    </div>
  `,
  styles: [
    `
      .callback-container {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        height: 100vh;
        background: #0f172a;
        color: #f1f5f9;
        font-family: 'Inter', sans-serif;
      }
      .spinner {
        border: 4px solid rgba(255, 255, 255, 0.1);
        width: 40px;
        height: 40px;
        border-radius: 50%;
        border-left-color: #3b82f6;
        animation: spin 1s linear infinite;
        margin-bottom: 20px;
      }
      @keyframes spin {
        0% {
          transform: rotate(0deg);
        }
        100% {
          transform: rotate(360deg);
        }
      }
      .status-text {
        font-size: 1.1rem;
        font-weight: 500;
      }
    `,
  ],
})
export class CallbackComponent implements OnInit {
  private activatedRoute = inject(ActivatedRoute);
  private authService = inject(AuthService);
  private router = inject(Router);
  private permissionService = inject(PermissionService);
  private commonLibService = inject(CommonLibService);
  error: any;

  ngOnInit() {
    this.callBackFunc();
  }

  callBackFunc() {
    this.activatedRoute.queryParams.subscribe(async (params) => {
      if (params['code'] && params['state']) {
        if (params['code'].length != 0 && params['state'].length != 0) {
          this.authService.removeValue(STORAGE_KEYS.accessToken);
          this.authService.removeValue(STORAGE_KEYS.refreshToken);
          this.authService.setValue(STORAGE_KEYS.codeValue, params['code']);
          this.authService.setValue(STORAGE_KEYS.stateValue, params['state']);
          await this.handleAuthentication();
        } else {
          await this.handleAuthentication();
        }
      } else {
        window.location.assign(
          environment.authConfig.issuer + '/login?logout=true',
        );
        console.error('No authorization code found in query parameters.');
      }
    });
  }

  handleAuthentication() {
    const code = this.authService.getValue(STORAGE_KEYS.codeValue) ?? '';

    this.authService
      .getTokenwithAuthCodeGrant(code)
      .then((tokens: any) => {
        if (tokens.error) {
          if (tokens.error.error == 'mfa_required') {
            window.location.assign(`${environment.authConfig.issuer}/mfa`);
          } else if (
            tokens.error.error_description?.includes(
              'Invalid authorization code',
            )
          ) {
            this.authService.initiateOidcLogin();
          } else {
            this.error =
              tokens.error.error_description || 'Unknown authentication error';
            window.location.assign(
              `${environment.authConfig.issuer}/login?error=${this.error}`,
            );
          }
        } else {
          this.error = '';
          // this.permissionService.fetchPermissions();
          // this.commonLibService.isFeatureUpdated.pipe(first()).subscribe({
          //   next: () => {
          //     const path = this.authService.isAdmin()
          //       ? 'team'
          //       : 'dashboard';
          //     this.router.navigate([path]);
          //   },
          // });
          if (this.authService.isAdmin()) {
            this.router.navigate(['/admin/dashboard']);
          } else {
            this.router.navigate(['/client/dashboard']);
          }
        }
      })
      .catch(() => {
        window.location.assign(
          environment.authConfig.issuer + '/login?logout=true',
        );
      });
  }
}
