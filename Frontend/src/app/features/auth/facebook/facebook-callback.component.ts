import { Component, OnInit, inject } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { AuthService } from '../../../services/auth.service';
import { SharedModule } from '../../../shared/shared.module';

interface FacebookPageItem {
  page_id: string;
  name: string;
  category?: string;
  instagram_username?: string;
  instagram_id?: string;
}

@Component({
  selector: 'app-facebook-callback',
  standalone: true,
  imports: [SharedModule],
  template: `
    <div class="callback-container">
      <div class="callback-card">
        <!-- Brand / Facebook Icon Badge -->
        <div class="facebook-icon-badge">
          <i class="pi pi-facebook"></i>
        </div>

        <!-- Processing State -->
        <div *ngIf="status === 'processing'" class="state-content">
          <div class="spinner"></div>
          <h2 class="title">Connecting Facebook</h2>
          <p class="subtitle">
            Verifying Facebook permissions and retrieving authorized Pages...
          </p>
        </div>

        <!-- Page Selection State -->
        <div *ngIf="status === 'select_page'" class="state-content">
          <h2 class="title text-indigo-400">Select Facebook Page</h2>
          <p class="subtitle mb-4">
            Choose which Facebook Page you want to connect for Messenger & automated replies:
          </p>

          <div class="pages-list max-h-64 overflow-y-auto space-y-2.5 w-full text-left my-4">
            <div
              *ngFor="let page of pages"
              (click)="selectedPageId = page.page_id"
              class="page-item p-3 rounded-xl border flex items-center justify-between cursor-pointer transition-all duration-200"
              [class.selected-page]="selectedPageId === page.page_id"
            >
              <div class="flex items-center gap-3">
                <div class="page-icon">
                  <i class="pi pi-flag text-indigo-400"></i>
                </div>
                <div>
                  <div class="font-bold text-sm text-slate-100">{{ page.name }}</div>
                  <div class="text-xs text-slate-400">
                    {{ page.category || 'Facebook Page' }}
                    <span *ngIf="page.instagram_username" class="text-pink-400 ml-1.5">
                      • Linked &#64;{{ page.instagram_username }}
                    </span>
                  </div>
                </div>
              </div>
              <div
                class="w-5 h-5 rounded-full border flex items-center justify-center"
                [style.border-color]="selectedPageId === page.page_id ? '#1877f2' : '#64748b'"
                [style.background]="selectedPageId === page.page_id ? 'rgba(24, 119, 242, 0.2)' : 'transparent'"
              >
                <div
                  *ngIf="selectedPageId === page.page_id"
                  class="w-2.5 h-2.5 rounded-full"
                  style="background-color: #1877f2"
                ></div>
              </div>
            </div>
          </div>

          <!-- Connect Linked Instagram Toggle -->
          <div *ngIf="hasLinkedInstagram" class="flex items-center justify-between p-3 rounded-xl bg-slate-800/60 border border-slate-700/50 w-full mb-4 text-left">
            <div class="flex flex-col">
              <span class="text-xs font-semibold text-slate-200">Connect Linked Instagram</span>
              <span class="text-[11px] text-slate-400">Also link the associated Instagram business account</span>
            </div>
            <p-inputSwitch [(ngModel)]="connectInstagram" [ngModelOptions]="{ standalone: true }"></p-inputSwitch>
          </div>

          <div class="flex items-center justify-center gap-3 w-full mt-2">
            <p-button
              label="Cancel"
              severity="secondary"
              [rounded]="true"
              size="small"
              (onClick)="closeOrRedirect()"
            ></p-button>
            <p-button
              label="Confirm Connection"
              icon="pi pi-check"
              [rounded]="true"
              size="small"
              [loading]="selectingPage"
              [disabled]="!selectedPageId"
              (onClick)="confirmPageSelection()"
            ></p-button>
          </div>
        </div>

        <!-- Success State -->
        <div *ngIf="status === 'success'" class="state-content">
          <div class="success-icon-wrapper">
            <i class="pi pi-check"></i>
          </div>
          <h2 class="title text-emerald-400">Connected Successfully!</h2>
          <p class="subtitle">
            Your Facebook Page is now connected and subscribed to webhooks.
          </p>
          <div class="mt-4 flex flex-col items-center gap-2">
            <span class="text-xs text-slate-400"
              >Redirecting to Channels in {{ countdown }}s...</span
            >
            <p-button
              label="Return to Channels"
              icon="pi pi-arrow-right"
              iconPos="right"
              [rounded]="true"
              size="small"
              (onClick)="closeOrRedirect()"
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
                'An unexpected error occurred during Facebook authorization.'
            }}
          </p>

          <div class="mt-6 flex items-center justify-center gap-3">
            <p-button
              label="Back to Channels"
              icon="pi pi-arrow-left"
              severity="secondary"
              [rounded]="true"
              (onClick)="closeOrRedirect()"
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
        background: rgba(30, 41, 59, 0.7);
        backdrop-filter: blur(16px);
        -webkit-backdrop-filter: blur(16px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 1.25rem;
        padding: 2.25rem 2rem;
        width: 100%;
        max-width: 480px;
        box-shadow:
          0 20px 25px -5px rgba(0, 0, 0, 0.5),
          0 8px 10px -6px rgba(0, 0, 0, 0.5);
        display: flex;
        flex-col: column;
        align-items: center;
        text-align: center;
        position: relative;
      }

      .facebook-icon-badge {
        width: 52px;
        height: 52px;
        border-radius: 1rem;
        background: linear-gradient(135deg, #1877f2 0%, #0d5cb6 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.6rem;
        color: white;
        margin: 0 auto 1.5rem auto;
        box-shadow: 0 10px 15px -3px rgba(24, 119, 242, 0.4);
      }

      .state-content {
        display: flex;
        flex-direction: column;
        align-items: center;
        width: 100%;
      }

      .spinner {
        width: 44px;
        height: 44px;
        border: 3px solid rgba(255, 255, 255, 0.1);
        border-top-color: #1877f2;
        border-radius: 50%;
        animation: spin 0.9s cubic-bezier(0.6, 0.2, 0.4, 0.8) infinite;
        margin-bottom: 1.5rem;
      }

      @keyframes spin {
        to {
          transform: rotate(360deg);
        }
      }

      .success-icon-wrapper {
        width: 56px;
        height: 56px;
        border-radius: 50%;
        background: rgba(16, 185, 129, 0.15);
        border: 1px solid rgba(16, 185, 129, 0.3);
        display: flex;
        align-items: center;
        justify-content: center;
        color: #34d399;
        font-size: 1.75rem;
        margin-bottom: 1.25rem;
      }

      .error-icon-wrapper {
        width: 52px;
        height: 52px;
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
        max-width: 380px;
      }

      .page-item {
        background: rgba(15, 23, 42, 0.6);
        border-color: rgba(51, 65, 85, 0.7);
      }

      .page-item:hover {
        border-color: #6366f1;
        background: rgba(99, 102, 241, 0.08);
      }

      .selected-page {
        border-color: #1877f2 !important;
        background: rgba(24, 119, 242, 0.12) !important;
      }

      .page-icon {
        width: 34px;
        height: 34px;
        border-radius: 8px;
        background: rgba(99, 102, 241, 0.15);
        display: flex;
        align-items: center;
        justify-content: center;
      }
    `,
  ],
})
export class FacebookCallbackComponent implements OnInit {
  private activatedRoute = inject(ActivatedRoute);
  private authService = inject(AuthService);
  private router = inject(Router);

  status: 'processing' | 'select_page' | 'success' | 'error' = 'processing';
  errorMessage = '';
  countdown = 3;
  private timerInterval: any;

  selectionToken = '';
  pages: FacebookPageItem[] = [];
  selectedPageId: string = '';
  connectInstagram = true;
  selectingPage = false;

  get hasLinkedInstagram(): boolean {
    const chosen = this.pages.find((p) => p.page_id === this.selectedPageId);
    return !!(chosen && (chosen.instagram_id || chosen.instagram_username));
  }

  ngOnInit(): void {
    this.handleCallback();
  }

  handleCallback(): void {
    this.activatedRoute.queryParams.subscribe({
      next: (params) => {
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
            'Facebook authorization was cancelled or denied.';
          this.notifyOpener('FACEBOOK_AUTH_ERROR', { error: this.errorMessage });
          return;
        }

        const code = params['code'];
        const state = params['state'];

        if (!code || !state) {
          this.status = 'error';
          this.errorMessage =
            'Authorization code or state parameter missing from callback URL.';
          this.notifyOpener('FACEBOOK_AUTH_ERROR', { error: this.errorMessage });
          return;
        }

        this.status = 'processing';
        this.authService.handleFacebookCallback({ code, state }).subscribe({
          next: (res) => {
            this.selectionToken = res.selection;
            this.pages = res.pages || [];

            if (this.pages.length === 1) {
              // Auto-select single page directly for rapid flow
              this.selectedPageId = this.pages[0].page_id;
              this.confirmPageSelection();
            } else if (this.pages.length > 1) {
              this.selectedPageId = this.pages[0].page_id;
              this.status = 'select_page';
            } else {
              this.status = 'error';
              this.errorMessage =
                'No administerable Facebook Pages found. Ensure your Facebook account has admin access to at least one Page.';
              this.notifyOpener('FACEBOOK_AUTH_ERROR', { error: this.errorMessage });
            }
          },
          error: (err) => {
            this.status = 'error';
            this.errorMessage =
              err?.error?.detail ||
              err?.error?.message ||
              err?.message ||
              'Failed to verify Facebook authorization. Please try again.';
            this.notifyOpener('FACEBOOK_AUTH_ERROR', { error: this.errorMessage });
          },
        });
      },
      error: () => {
        this.status = 'error';
        this.errorMessage = 'Failed to parse callback parameters.';
      },
    });
  }

  confirmPageSelection(): void {
    if (!this.selectedPageId || !this.selectionToken) return;

    this.selectingPage = true;
    this.authService
      .selectFacebookPage({
        selection: this.selectionToken,
        page_id: this.selectedPageId,
        connect_instagram: this.connectInstagram,
      })
      .subscribe({
        next: (account) => {
          this.selectingPage = false;
          this.status = 'success';
          this.notifyOpener('FACEBOOK_AUTH_SUCCESS', { channel: account });
          this.startCountdown();
        },
        error: (err) => {
          this.selectingPage = false;
          this.status = 'error';
          this.errorMessage =
            err?.error?.detail ||
            err?.error?.message ||
            err?.message ||
            'Failed to connect Facebook Page.';
          this.notifyOpener('FACEBOOK_AUTH_ERROR', { error: this.errorMessage });
        },
      });
  }

  private notifyOpener(type: string, payload: any): void {
    try {
      if (window.opener && !window.opener.closed) {
        window.opener.postMessage(
          {
            type,
            ...payload,
          },
          window.location.origin
        );
      }
    } catch (e) {
      console.warn('Could not postMessage to opener:', e);
    }
  }

  startCountdown(): void {
    this.countdown = 2;
    this.timerInterval = setInterval(() => {
      this.countdown--;
      if (this.countdown <= 0) {
        clearInterval(this.timerInterval);
        this.closeOrRedirect();
      }
    }, 1000);
  }

  closeOrRedirect(): void {
    if (this.timerInterval) {
      clearInterval(this.timerInterval);
    }

    try {
      if (window.opener && !window.opener.closed) {
        window.close();
        return;
      }
    } catch (e) {}

    // Fallback if not inside a popup
    this.router.navigate(['/client/channels']);
  }

  retryAuth(): void {
    this.authService.getFacebookAuthorizeUrl().subscribe({
      next: (res) => {
        if (res && res.authorize_url) {
          window.location.href = res.authorize_url;
        }
      },
      error: () => {
        this.closeOrRedirect();
      },
    });
  }
}
