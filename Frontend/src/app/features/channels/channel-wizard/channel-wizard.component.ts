import { Component, EventEmitter, Input, Output, OnInit } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { ChannelService } from '../../../services/channel.service';
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
export class ChannelWizardComponent implements OnInit {
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
  ];

  constructor(
    private channelService: ChannelService,
    private messageService: MessageService,
    private scriptService: ScriptService,
  ) {}

  ngOnInit(): void {
    this.loadScripts();
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
      default: return 'External ID';
    }
  }

  getExternalIdPlaceholder(): string {
    switch (this.channelType) {
      case 'whatsapp': return 'e.g. 109876543210';
      case 'messenger': return 'e.g. 123456789012345';
      case 'instagram': return 'e.g. 17841401234567';
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
    this.complete.emit();
  }

  onClose(): void {
    this.close.emit();
  }
}

