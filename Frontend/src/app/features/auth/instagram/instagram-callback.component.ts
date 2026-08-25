import { Component, OnInit, inject } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { AuthService } from '../../../services/auth.service';
import { SharedModule } from '../../../shared/shared.module';

@Component({
  selector: 'app-instagram-callback',
  standalone: true,
  imports: [SharedModule],
  template: `
    <div class="callback-container">
      <div class="callback-card">
        <!-- Brand / Instagram Icon Badge -->
        <div class="instagram-icon-badge">
          <i class="pi pi-instagram"></i>
        </div>

        <!-- Processing State -->
        <div *ngIf="status === 'processing'" class="state-content">
          <div class="spinner"></div>
          <h2 class="title">Connecting Instagram</h2>
          <p class="subtitle">
            Processing authorization code and verifying channel setup...
          </p>
        </div>

        <!-- Success State -->
        <div *ngIf="status === 'success'" class="state-content">
          <div class="success-icon-wrapper">
            <i class="pi pi-check"></i>
          </div>
          <h2 class="title text-emerald-400">Connected Successfully!</h2>
          <p class="subtitle">
            Your Instagram account has been linked to LeadAI.
          </p>
          <div class="mt-4 flex flex-col items-center gap-2">
            <span class="text-xs text-slate-400"
              >Redirecting to channels in {{ countdown }}s...</span
            >
            <p-button
              label="Go to Channels Now"
              icon="pi pi-arrow-right"
              iconPos="right"
              [rounded]="true"
              size="small"
              (onClick)="redirectToChannels()"
            ></p-button>
          </div>
        </div>

        <!-- Error State -->
        <div *ngIf="status === 'error'" class="state-content">
          <div class="error-icon-wrapper">
            <i class="pi pi-times"></i>
          </div>
          <h2 class="title text-red-400">Connection Failed</h2>
          <p class="subtitle">
            {{
              errorMessage ||
                'An unexpected error occurred during Instagram authorization.'
            }}
          </p>

          <div class="mt-6 flex items-center justify-center gap-3">
            <p-button
              label="Return to Channels"
              icon="pi pi-arrow-left"
              severity="secondary"
              [rounded]="true"
              (onClick)="redirectToChannels()"
            ></p-button>
            <p-button
              label="Try Again"
              icon="pi pi-refresh"
              [rounded]="true"
              (onClick)="retryAuth()"
            ></p-button>
          </div>
        </div>
      </div>
    </div>
  `,
  styles: [
    `
      .callback-container {
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 100vh;
        background: radial-gradient(
          circle at top right,
          #1e1b4b 0%,
          #0f172a 50%,
          #020617 100%
        );
        color: #f1f5f9;
        font-family:
          'Inter',
          system-ui,
          -apple-system,
          sans-serif;
        padding: 1.5rem;
      }

      .callback-card {
        display: flex;
        flex-direction: column;
        align-items: center;
        text-align: center;
        background: rgba(30, 41, 59, 0.7);
        backdrop-filter: blur(16px);
        -webkit-backdrop-filter: blur(16px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        box-shadow:
          0 25px 50px -12px rgba(0, 0, 0, 0.5),
          0 0 40px rgba(225, 48, 108, 0.15);
        border-radius: 1.5rem;
        padding: 2.5rem 2rem;
        max-width: 480px;
        width: 100%;
        animation: fadeIn 0.4s ease-out;
      }

      @keyframes fadeIn {
        from {
          opacity: 0;
          transform: translateY(12px) scale(0.98);
        }
        to {
          opacity: 1;
          transform: translateY(0) scale(1);
        }
      }

      .instagram-icon-badge {
        width: 56px;
        height: 56px;
        border-radius: 16px;
        background: linear-gradient(
          45deg,
          #f09433 0%,
          #e6683c 25%,
          #dc2743 50%,
          #cc2366 75%,
          #bc1888 100%
        );
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.75rem;
        color: #ffffff;
        margin-bottom: 1.5rem;
        box-shadow: 0 10px 20px -5px rgba(225, 48, 108, 0.4);
      }

      .state-content {
        display: flex;
        flex-direction: column;
        align-items: center;
        width: 100%;
      }

      .spinner {
        border: 3px solid rgba(255, 255, 255, 0.1);
        width: 42px;
        height: 42px;
        border-radius: 50%;
        border-left-color: #ec4899;
        border-top-color: #f43f5e;
        animation: spin 0.9s cubic-bezier(0.55, 0.15, 0.45, 0.85) infinite;
        margin-bottom: 1.25rem;
      }

      @keyframes spin {
        0% {
          transform: rotate(0deg);
        }
        100% {
          transform: rotate(360deg);
        }
      }

      .success-icon-wrapper {
        width: 48px;
        height: 48px;
        border-radius: 50%;
        background: rgba(16, 185, 129, 0.15);
        border: 1px solid rgba(16, 185, 129, 0.3);
        display: flex;
        align-items: center;
        justify-content: center;
        color: #34d399;
        font-size: 1.5rem;
        margin-bottom: 1.25rem;
      }

      .error-icon-wrapper {
        width: 48px;
        height: 48px;
        border-radius: 50%;
        background: rgba(239, 68, 68, 0.15);
        border: 1px solid rgba(239, 68, 68, 0.3);
        display: flex;
        align-items: center;
        justify-content: center;
        color: #f87171;
        font-size: 1.5rem;
        margin-bottom: 1.25rem;
      }

      .title {
        font-size: 1.35rem;
        font-weight: 700;
        margin: 0 0 0.5rem 0;
        letter-spacing: -0.01em;
      }

      .subtitle {
        font-size: 0.875rem;
        color: #94a3b8;
        line-height: 1.5;
        margin: 0;
        max-width: 360px;
      }
    `,
  ],
})
export class InstagramCallbackComponent implements OnInit {
  private activatedRoute = inject(ActivatedRoute);
  private authService = inject(AuthService);
  private router = inject(Router);

  status: 'processing' | 'success' | 'error' = 'processing';
  errorMessage = '';
  countdown = 3;
  private timerInterval: any;

  ngOnInit(): void {
    this.handleCallback();
  }

  handleCallback(): void {
    this.activatedRoute.queryParams.subscribe({
      next: (params) => {
        // Handle error parameters returned by Instagram / Meta OAuth
        if (
          params['error'] ||
          params['error_reason'] ||
          params['error_description']
        ) {
          this.status = 'error';
          this.errorMessage =
            params['error_description'] ||
            params['error_reason'] ||
            params['error'] ||
            'Instagram authorization was cancelled or denied.';
          return;
        }

        const code = params['code'];
        const state = params['state'];

        if (!code || !state) {
          this.status = 'error';
          this.errorMessage =
            'Authorization code or state parameter missing from callback URL.';
          return;
        }

        this.status = 'processing';
        this.authService.handleInstagramCallback({ code, state }).subscribe({
          next: () => {
            this.status = 'success';
            this.startRedirectCountdown();
          },
          error: (err) => {
            this.status = 'error';
            this.errorMessage =
              err?.error?.detail ||
              err?.error?.message ||
              err?.message ||
              'Failed to verify Instagram authorization code. Please try again.';
          },
        });
      },
      error: (err) => {
        this.status = 'error';
        this.errorMessage = 'Failed to parse callback parameters.';
      },
    });
  }

  startRedirectCountdown(): void {
    this.countdown = 3;
    this.timerInterval = setInterval(() => {
      this.countdown--;
      if (this.countdown <= 0) {
        clearInterval(this.timerInterval);
        this.redirectToChannels();
      }
    }, 1000);
  }

  redirectToChannels(): void {
    if (this.timerInterval) {
      clearInterval(this.timerInterval);
    }
    const path = '/client/channels';
    this.router.navigate([path]);
  }

  retryAuth(): void {
    this.authService.initiateInstagramAuth();
  }
}
