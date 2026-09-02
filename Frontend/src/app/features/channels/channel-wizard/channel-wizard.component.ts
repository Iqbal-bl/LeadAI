import { Component, EventEmitter, Input, Output, OnInit, OnDestroy } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { ChannelService } from '../../../services/channel.service';
import { AuthService } from '../../../services/auth.service';
import { ChannelType, ChannelCreateResponse } from '../../../models/channel.models';
import { MessageService } from 'primeng/api';
import { StepperModule } from 'primeng/stepper';
import { ScriptService } from '../../../services/script.service';
import { Script } from '../../../models/script.models';

export interface ChannelPlatformOption {
  key: string;
  id: string;
  name: string;
  badge: string;
  badgeClass: string;
  description: string;
  icon: string;
  gradient: string;
  actionLabel: string;
  loading: boolean;
}

const ALL_PLATFORMS: readonly Omit<ChannelPlatformOption, 'loading'>[] = [
  {
    key: 'social.facebook',
    id: 'facebook',
    name: 'Facebook Messenger',
    badge: 'OAuth 2.0',
    badgeClass:
      'bg-blue-50 text-blue-700 border border-blue-200 dark:bg-blue-950/60 dark:text-blue-300 dark:border-blue-800',
    description:
      'Connect your Facebook Business Page for automated Messenger replies, lead capturing, and page inbox sync.',
    icon: 'pi pi-facebook',
    gradient: 'linear-gradient(135deg, #1877f2 0%, #0d5cb6 100%)',
    actionLabel: 'Connect Facebook',
  },
  {
    key: 'social.instagram',
    id: 'instagram',
    name: 'Instagram Direct',
    badge: 'Meta Direct',
    badgeClass:
      'bg-pink-50 text-pink-700 border border-pink-200 dark:bg-pink-950/60 dark:text-pink-300 dark:border-pink-800',
    description:
      'Link your Instagram Professional account to manage DMs, story mentions, and customer inquiries with AI auto-replies.',
    icon: 'pi pi-instagram',
    gradient: 'linear-gradient(45deg, #f09433, #dc2743, #bc1888)',
    actionLabel: 'Connect Instagram',
  },
  {
    key: 'social.linkedin',
    id: 'linkedin',
    name: 'LinkedIn',
    badge: 'OAuth 2.0',
    badgeClass:
      'bg-sky-50 text-sky-700 border border-sky-200 dark:bg-sky-950/60 dark:text-sky-300 dark:border-sky-800',
    description:
      'Link your company or personal LinkedIn profile to schedule and publish updates and engage with professional prospects.',
    icon: 'pi pi-linkedin',
    gradient: 'linear-gradient(135deg, #0A66C2 0%, #004182 100%)',
    actionLabel: 'Connect LinkedIn',
  },
  {
    key: 'social.whatsapp',
    id: 'whatsapp',
    name: 'WhatsApp Business',
    badge: 'Meta Cloud',
    badgeClass:
      'bg-emerald-50 text-emerald-700 border border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800',
    description:
      'Connect your WhatsApp Business number via Meta to send automated template messages and interact with customers.',
    icon: 'pi pi-whatsapp',
    gradient: 'linear-gradient(135deg, #25D366 0%, #128C7E 100%)',
    actionLabel: 'Connect WhatsApp',
  },
];

@Component({
  selector: 'app-channel-wizard',
  standalone: true,
  imports: [SharedModule, StepperModule],
  templateUrl: './channel-wizard.component.html',
  styleUrl: './channel-wizard.component.scss',
})
export class ChannelWizardComponent implements OnInit, OnDestroy {
  private _visible = false;
  @Input()
  get visible(): boolean {
    return this._visible;
  }
  set visible(val: boolean) {
    this._visible = val;
    if (val) {
      this.loadAvailablePlatforms();
    }
  }

  @Output() complete = new EventEmitter<void>();
  @Output() close = new EventEmitter<void>();

  activeStep = 0;

  // Step 1 form fields
  channelType: ChannelType = 'whatsapp';
  displayName = '';
  externalId = '';
  accessToken = '';
  appSecret = '';
  businessAccountId = '';
  displayNumber = '';
  apiVersion = 'v21.0';
  defaultLanguage = 'en';
  scriptId = '';
  verifyTokenInput = '';
  autoReply = true;

  private _oauthLoading = false;
  get oauthLoading(): boolean {
    return this._oauthLoading;
  }
  set oauthLoading(val: boolean) {
    this._oauthLoading = val;
    this.updatePlatformStates();
  }

  private _fbOauthLoading = false;
  get fbOauthLoading(): boolean {
    return this._fbOauthLoading;
  }
  set fbOauthLoading(val: boolean) {
    this._fbOauthLoading = val;
    this.updatePlatformStates();
  }

  private activeOAuthPopup: Window | null = null;
  private messageEventListener!: (event: MessageEvent) => void;

  // Scripts library for dropdown
  scripts: Script[] = [];

  // Step 1 result
  createdChannelId = '';
  webhookUrl = '';
  verifyToken = '';
  step1Loading = false;
  step1Error = '';

  // Step 3 (Test Send)
  testTo = '';
  testMessage = 'Hello! This is a test message from LeadAI.';
  testTemplateName = '';
  testTemplateLanguage = 'en';
  testLoading = false;
  testSuccess = false;
  testError = '';

  channelTypes: { label: string; value: ChannelType; icon: string; color: string }[] = [
    { label: 'WhatsApp', value: 'whatsapp', icon: 'pi pi-whatsapp', color: '#25D366' },
    { label: 'Messenger', value: 'messenger', icon: 'pi pi-facebook', color: '#0084FF' },
    { label: 'Instagram', value: 'instagram', icon: 'pi pi-instagram', color: '#E4405F' },
    { label: 'LinkedIn', value: 'linkedin', icon: 'pi pi-linkedin', color: '#0A66C2' },
  ];

  private _linkedinConnecting = false;
  get linkedinConnecting(): boolean {
    return this._linkedinConnecting;
  }
  set linkedinConnecting(val: boolean) {
    this._linkedinConnecting = val;
    this.updatePlatformStates();
  }
  private linkedinPollingInterval: any;

  constructor(
    private channelService: ChannelService,
    private authService: AuthService,
    private messageService: MessageService,
    private scriptService: ScriptService,
  ) {}

  availablePlatforms: ChannelPlatformOption[] = [];

  loadAvailablePlatforms(): void {
    const user = this.authService.getCurrentUser();
    const perms = user?.permissions || [];
    const isPlatformAdmin = this.authService.isPlatformAdmin();

    const hasPerm = (p: string) => isPlatformAdmin || perms.includes(p.toLowerCase());

    this.availablePlatforms = ALL_PLATFORMS
      .filter((p) => hasPerm(p.key))
      .map((p) => ({
        ...p,
        loading: false,
      }));

    this.updatePlatformStates();
  }

  private updatePlatformStates(): void {
    for (const platform of this.availablePlatforms) {
      if (platform.id === 'facebook' || platform.id === 'whatsapp') {
        platform.loading = this._fbOauthLoading;
      } else if (platform.id === 'instagram') {
        platform.loading = this._oauthLoading;
      } else if (platform.id === 'linkedin') {
        platform.loading = this._linkedinConnecting;
        platform.actionLabel = this._linkedinConnecting ? 'Waiting for Consent...' : 'Connect LinkedIn';
      }
    }
  }

  trackByPlatformId(_index: number, platform: ChannelPlatformOption): string {
    return platform.id;
  }

  onConnectPlatform(id: string): void {
    if (id === 'facebook' || id === 'whatsapp') {
      this.connectWithFacebook();
    } else if (id === 'instagram') {
      this.connectWithInstagram();
    } else if (id === 'linkedin') {
      this.connectWithLinkedIn();
    }
  }


  ngOnInit(): void {
    this.loadAvailablePlatforms();
    this.loadScripts();
    this.messageEventListener = (event: MessageEvent) => this.handleOAuthMessage(event);
    window.addEventListener('message', this.messageEventListener);
  }

  ngOnDestroy(): void {
    window.removeEventListener('message', this.messageEventListener);
    if (this.linkedinPollingInterval) {
      clearInterval(this.linkedinPollingInterval);
    }
  }

  private handleOAuthMessage(event: MessageEvent): void {
    if (!event.data || typeof event.data !== 'object') return;
    const { type, channel, error } = event.data;

    if (type === 'FACEBOOK_AUTH_SUCCESS' || type === 'INSTAGRAM_AUTH_SUCCESS') {
      this.oauthLoading = false;
      this.fbOauthLoading = false;
      if (this.activeOAuthPopup && !this.activeOAuthPopup.closed) {
        this.activeOAuthPopup.close();
      }
      this.messageService.add({
        severity: 'success',
        summary: 'Connected!',
        detail: `Successfully linked ${type === 'FACEBOOK_AUTH_SUCCESS' ? 'Facebook Page' : 'Instagram'} via OAuth.`,
      });
      this.finishWizard();
    } else if (type === 'FACEBOOK_AUTH_ERROR' || type === 'INSTAGRAM_AUTH_ERROR') {
      this.oauthLoading = false;
      this.fbOauthLoading = false;
      this.messageService.add({
        severity: 'error',
        summary: 'OAuth Error',
        detail: error || 'Failed to complete OAuth authorization.',
      });
    }
  }

  private openOAuthPopup(url: string, title: string): Window | null {
    const width = 600;
    const height = 700;
    const left = window.screen.width / 2 - width / 2;
    const top = window.screen.height / 2 - height / 2;
    this.activeOAuthPopup = window.open(
      url,
      title,
      `width=${width},height=${height},top=${top},left=${left},scrollbars=yes,status=yes`
    );
    return this.activeOAuthPopup;
  }

  loadScripts(): void {
    this.scriptService.getScripts(undefined, true).subscribe({
      next: (res) => {
        this.scripts = res;
      },
      error: () => {
        this.scripts = [];
      },
    });
  }

  getExternalIdLabel(): string {
    switch (this.channelType) {
      case 'whatsapp': return 'WhatsApp Phone Number ID';
      case 'messenger': return 'Facebook Page ID';
      case 'instagram': return 'Instagram Account ID';
      case 'linkedin': return 'LinkedIn Person URN';
      default: return 'External ID';
    }
  }

  getExternalIdPlaceholder(): string {
    switch (this.channelType) {
      case 'whatsapp': return 'e.g. 109876543210';
      case 'messenger': return 'e.g. 123456789012345';
      case 'instagram': return 'e.g. 17841401234567';
      case 'linkedin': return 'e.g. urn:li:person:CsjnS7Uz7f';
      default: return 'Enter ID';
    }
  }

  get step1Valid(): boolean {
    return !!this.displayName.trim() && !!this.externalId.trim() && !!this.accessToken.trim();
  }

  submitStep1(): void {
    this.step1Loading = true;
    this.step1Error = '';

    this.channelService.createChannel({
      channel: this.channelType,
      name: this.displayName.trim(),
      external_id: this.externalId.trim(),
      access_token: this.accessToken.trim(),
      app_secret: this.appSecret.trim() || undefined,
      business_account_id: this.businessAccountId.trim() || undefined,
      display_number: this.displayNumber.trim() || undefined,
      api_version: this.apiVersion.trim() || undefined,
      default_language: this.defaultLanguage.trim() || undefined,
      script_id: this.scriptId || undefined,
      verify_token: this.verifyTokenInput.trim() || undefined,
      auto_reply: this.autoReply,
    }).subscribe({
      next: (res: ChannelCreateResponse) => {
        this.createdChannelId = res.id;
        this.webhookUrl = res.webhook_url || '';
        this.verifyToken = res.verify_token || '';
        this.step1Loading = false;
        this.activeStep = 1;
      },
      error: (err) => {
        this.step1Loading = false;
        if (err.status === 409) {
          this.step1Error = 'This external ID is already connected to a channel, possibly under a different company. Each number/page/account can only be connected once.';
        } else {
          this.step1Error = err.error?.detail || err.message || 'Failed to create channel. Please check your inputs and try again.';
        }
      },
    });
  }

  copyToClipboard(value: string, label: string): void {
    if (!value) return;
    navigator.clipboard.writeText(value).then(() => {
      this.messageService.add({
        severity: 'success',
        summary: 'Copied',
        detail: `${label} copied to clipboard`,
        life: 2000,
      });
    });
  }

  goToStep3(): void {
    this.activeStep = 2;
  }

  submitTest(): void {
    if (!this.testTo.trim()) return;

    this.testLoading = true;
    this.testError = '';
    this.testSuccess = false;

    this.channelService.testChannel(this.createdChannelId, {
      to: this.testTo.trim(),
      message: this.testMessage.trim() || undefined,
      template_name: this.testTemplateName.trim() || undefined,
      template_language: this.testTemplateLanguage.trim() || 'en',
    }).subscribe({
      next: () => {
        this.testLoading = false;
        this.testSuccess = true;
        this.messageService.add({
          severity: 'success',
          summary: 'Test Sent',
          detail: 'Test message sent successfully!',
        });
      },
      error: (err) => {
        this.testLoading = false;
        this.testError = err.error?.detail || err.message || 'Test message failed. Please verify your Meta credentials and 24h messaging session.';
      },
    });
  }

  finishWizard(): void {
    if (this.linkedinPollingInterval) {
      clearInterval(this.linkedinPollingInterval);
    }
    if (this.activeOAuthPopup && !this.activeOAuthPopup.closed) {
      this.activeOAuthPopup.close();
    }
    this.complete.emit();
  }

  connectWithInstagram(): void {
    const popup = this.openOAuthPopup('about:blank', 'Instagram Connect');
    this.oauthLoading = true;
    this.authService.getInstagramAuthorizeUrl().subscribe({
      next: (res) => {
        this.oauthLoading = false;
        if (res && res.authorize_url && popup) {
          popup.location.href = res.authorize_url;
        } else if (popup) {
          popup.close();
        }
      },
      error: (err) => {
        this.oauthLoading = false;
        if (popup && !popup.closed) popup.close();
        this.messageService.add({
          severity: 'error',
          summary: 'OAuth Error',
          detail:
            err?.error?.detail ||
            err?.message ||
            'Failed to initiate Instagram OAuth authorization.',
        });
      },
    });
  }

  connectWithFacebook(): void {
    const popup = this.openOAuthPopup('about:blank', 'Facebook Connect');
    this.fbOauthLoading = true;
    this.authService.getFacebookAuthorizeUrl().subscribe({
      next: (res) => {
        this.fbOauthLoading = false;
        if (res && res.authorize_url && popup) {
          popup.location.href = res.authorize_url;
        } else if (popup) {
          popup.close();
        }
      },
      error: (err) => {
        this.fbOauthLoading = false;
        if (popup && !popup.closed) popup.close();
        this.messageService.add({
          severity: 'error',
          summary: 'OAuth Error',
          detail:
            err?.error?.detail ||
            err?.message ||
            'Failed to initiate Facebook OAuth authorization.',
        });
      },
    });
  }

  connectWithLinkedIn(): void {
    const popup = this.openOAuthPopup('about:blank', 'LinkedIn Connect');
    this.linkedinConnecting = true;
    this.channelService.getLinkedInConnectUrl().subscribe({
      next: (res) => {
        if (res && res.authorize_url && popup) {
          popup.location.href = res.authorize_url;

          if (this.linkedinPollingInterval) {
            clearInterval(this.linkedinPollingInterval);
          }

          this.linkedinPollingInterval = setInterval(() => {
            this.channelService.getLinkedInStatus().subscribe({
              next: (status) => {
                if (status && status.connected) {
                  clearInterval(this.linkedinPollingInterval);
                  this.linkedinConnecting = false;
                  if (popup && !popup.closed) {
                    popup.close();
                  }
                  this.messageService.add({
                    severity: 'success',
                    summary: 'LinkedIn Connected',
                    detail: `LinkedIn profile connected successfully${status.person_urn ? ' (' + status.person_urn + ')' : ''}.`,
                  });
                  this.finishWizard();
                }
              },
            });
          }, 3000);
        } else {
          this.linkedinConnecting = false;
          if (popup && !popup.closed) popup.close();
        }
      },
      error: (err) => {
        this.linkedinConnecting = false;
        if (popup && !popup.closed) popup.close();
        this.messageService.add({
          severity: 'error',
          summary: 'OAuth Error',
          detail:
            err?.error?.detail ||
            err?.message ||
            'Failed to initiate LinkedIn OAuth authorization.',
        });
      },
    });
  }



  onClose(): void {

    if (this.linkedinPollingInterval) {
      clearInterval(this.linkedinPollingInterval);
    }
    this.close.emit();
  }
}

