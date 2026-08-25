import { Component, EventEmitter, Input, Output, OnInit, OnDestroy } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { ChannelService } from '../../../services/channel.service';
import { AuthService } from '../../../services/auth.service';
import { ChannelType, ChannelCreateResponse } from '../../../models/channel.models';
import { MessageService } from 'primeng/api';
import { StepperModule } from 'primeng/stepper';
import { ScriptService } from '../../../services/script.service';
import { Script } from '../../../models/script.models';

@Component({
  selector: 'app-channel-wizard',
  standalone: true,
  imports: [SharedModule, StepperModule],
  templateUrl: './channel-wizard.component.html',
  styleUrl: './channel-wizard.component.scss',
})
export class ChannelWizardComponent implements OnInit, OnDestroy {
  @Input() visible = false;
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
  oauthLoading = false;
  fbOauthLoading = false;

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

  linkedinConnecting = false;
  private linkedinPollingInterval: any;

  constructor(
    private channelService: ChannelService,
    private authService: AuthService,
    private messageService: MessageService,
    private scriptService: ScriptService,
  ) {}

  ngOnInit(): void {
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
    this.oauthLoading = true;
    this.authService.getInstagramAuthorizeUrl().subscribe({
      next: (res) => {
        this.oauthLoading = false;
        if (res && res.authorize_url) {
          this.openOAuthPopup(res.authorize_url, 'Instagram Connect');
        }
      },
      error: (err) => {
        this.oauthLoading = false;
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
    this.fbOauthLoading = true;
    this.authService.getFacebookAuthorizeUrl().subscribe({
      next: (res) => {
        this.fbOauthLoading = false;
        if (res && res.authorize_url) {
          window.location.href = res.authorize_url;
        }
      },
      error: (err) => {
        this.fbOauthLoading = false;
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
    this.linkedinConnecting = true;
    this.channelService.getLinkedInConnectUrl().subscribe({
      next: (res) => {
        if (res && res.authorize_url) {
          const width = 600;
          const height = 650;
          const left = window.screen.width / 2 - width / 2;
          const top = window.screen.height / 2 - height / 2;
          const popup = window.open(
            res.authorize_url,
            'LinkedIn Connect',
            `width=${width},height=${height},top=${top},left=${left},scrollbars=yes,status=yes`,
          );

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
        }
      },
      error: (err) => {
        this.linkedinConnecting = false;
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

