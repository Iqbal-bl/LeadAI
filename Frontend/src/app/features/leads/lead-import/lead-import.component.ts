import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';
import { SharedModule } from '../../../shared/shared.module';
import { LeadImportService } from '../../../services/lead-import.service';
import { ChannelService } from '../../../services/channel.service';
import { ScriptService } from '../../../services/script.service';
import { Channel } from '../../../models/channel.models';
import { Script } from '../../../models/script.models';
import {
  LeadImportSchemaField,
  LeadImportResponse,
  LeadImportBatch,
  LeadImportInvalidRow,
} from '../../../models/lead-import.models';
import { MessageService } from 'primeng/api';

@Component({
  selector: 'app-lead-import',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './lead-import.component.html',
  styleUrl: './lead-import.component.scss',
})
export class LeadImportComponent implements OnInit {
  // Schema State
  schemaFields: LeadImportSchemaField[] = [];
  sampleCsvHeader = '';
  isLoadingSchema = true;
  showSchemaReference = false;

  // File Upload State
  selectedFile: File | null = null;
  isDragging = false;

  // Config State
  channel: 'chat' | 'call' | 'both' = 'chat';
  chatChannel: 'whatsapp' | 'instagram' | 'messenger' = 'whatsapp';
  chatChannelAccountId = '';
  instagramAccountId = '';
  facebookAccountId = '';
  voiceScriptId = '';
  callEscalation = false;

  // Available options
  channels: Channel[] = [];
  scripts: Script[] = [];
  isLoadingOptions = true;

  // Upload & Receipt State
  isImporting = false;
  receipt: LeadImportResponse | null = null;
  showInvalidRows = false;

  constructor(
    private leadImportService: LeadImportService,
    private channelService: ChannelService,
    private scriptService: ScriptService,
    private messageService: MessageService,
    private router: Router,
  ) {}

  ngOnInit(): void {
    this.loadSchema();
    this.loadChannelsAndScripts();
  }

  loadSchema(): void {
    this.isLoadingSchema = true;
    this.leadImportService.getImportSchema().subscribe({
      next: (res) => {
        this.schemaFields = res.fields || [];
        this.sampleCsvHeader = res.sample_csv_header || '';
        this.isLoadingSchema = false;
      },
      error: () => {
        // Fallback default schema for resilient display
        this.schemaFields = [
          { key: 'name', label: 'Name', data_type: 'text', required: false, source: 'fixed' },
          { key: 'phone', label: 'Phone Number', data_type: 'text', required: false, source: 'fixed' },
          { key: 'email', label: 'Email', data_type: 'email', required: false, source: 'fixed' },
          { key: 'whatsapp', label: 'WhatsApp Number', data_type: 'text', required: false, source: 'fixed' },
          { key: 'instagram_id', label: 'Instagram ID (IGSID)', data_type: 'text', required: false, source: 'fixed' },
          { key: 'facebook_id', label: 'Facebook/Messenger ID (PSID)', data_type: 'text', required: false, source: 'fixed' },
          { key: 'product', label: 'Product', data_type: 'text', required: false, source: 'fixed' },
        ];
        this.sampleCsvHeader = 'Name,Phone Number,Email,WhatsApp Number,Instagram ID (IGSID),Facebook/Messenger ID (PSID),Product';
        this.isLoadingSchema = false;
      },
    });
  }

  loadChannelsAndScripts(): void {
    this.isLoadingOptions = true;
    this.channelService.getChannels().subscribe({
      next: (chList) => {
        this.channels = chList || [];
        this.autoSelectChatAccount();
      },
      error: () => {
        this.channels = [];
      },
    });

    this.scriptService.getScripts().subscribe({
      next: (scList) => {
        this.scripts = scList || [];
        this.isLoadingOptions = false;
      },
      error: () => {
        this.scripts = [];
        this.isLoadingOptions = false;
      },
    });
  }

  get fixedFields(): LeadImportSchemaField[] {
    return this.schemaFields.filter((f) => f.source === 'fixed');
  }

  get customFields(): LeadImportSchemaField[] {
    return this.schemaFields.filter((f) => f.source === 'data_point');
  }

  get accountsForChatChannel(): Channel[] {
    return this.channels.filter(
      (c) => c.channel === this.chatChannel && c.is_active !== false,
    );
  }

  get instagramAccounts(): Channel[] {
    return this.channels.filter(
      (c) => c.channel === 'instagram' && c.is_active !== false,
    );
  }

  get facebookAccounts(): Channel[] {
    return this.channels.filter(
      (c) => c.channel === 'messenger' && c.is_active !== false,
    );
  }

  onChatChannelChange(): void {
    this.autoSelectChatAccount();
  }

  autoSelectChatAccount(): void {
    const accs = this.accountsForChatChannel;
    if (accs.length > 0) {
      this.chatChannelAccountId = accs[0].id;
    } else {
      this.chatChannelAccountId = '';
    }
  }

  // Drag and Drop handlers
  onDragOver(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.isDragging = true;
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.isDragging = false;
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.isDragging = false;
    const files = event.dataTransfer?.files;
    if (files && files.length > 0) {
      this.validateAndSetFile(files[0]);
    }
  }

  onFileSelected(event: any): void {
    const files = event.target?.files;
    if (files && files.length > 0) {
      this.validateAndSetFile(files[0]);
    }
  }

  validateAndSetFile(file: File): void {
    const validExtensions = ['.csv', '.xlsx', '.xls', '.docx'];
    const lowerName = file.name.toLowerCase();
    const hasValidExt = validExtensions.some((ext) => lowerName.endsWith(ext));
    if (!hasValidExt) {
      this.messageService.add({
        severity: 'error',
        summary: 'Invalid File Type',
        detail: 'Please upload a CSV, XLSX, XLS, or DOCX document.',
      });
      return;
    }

    this.selectedFile = file;
  }

  removeFile(): void {
    this.selectedFile = null;
  }

  downloadSampleCsv(): void {
    if (!this.sampleCsvHeader) {
      this.sampleCsvHeader = 'Name,Phone Number,Email,WhatsApp Number,Instagram ID (IGSID),Facebook/Messenger ID (PSID),Product,Budget';
    }
    this.leadImportService.downloadSampleTemplate(this.sampleCsvHeader);
    this.messageService.add({
      severity: 'info',
      summary: 'Template Downloaded',
      detail: 'Sample CSV template saved to your device.',
    });
  }

  canSubmit(): boolean {
    if (!this.selectedFile) return false;
    if (this.channel === 'chat' || this.channel === 'both') {
      if (!this.chatChannelAccountId) return false;
    }
    return true;
  }

  submitImport(): void {
    if (!this.selectedFile) return;

    if ((this.channel === 'chat' || this.channel === 'both') && !this.chatChannelAccountId) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Connected Account Required',
        detail: `Please select a connected ${this.chatChannel} account for messaging outreach.`,
      });
      return;
    }

    this.isImporting = true;
    const formData = new FormData();
    formData.append('file', this.selectedFile);
    formData.append('channel', this.channel);

    if (this.channel === 'chat' || this.channel === 'both') {
      formData.append('chat_channel', this.chatChannel);
      formData.append('chat_channel_account_id', this.chatChannelAccountId);
    }

    if (this.instagramAccountId) {
      formData.append('instagram_account_id', this.instagramAccountId);
    }
    if (this.facebookAccountId) {
      formData.append('facebook_account_id', this.facebookAccountId);
    }

    if (this.channel === 'call' || this.channel === 'both') {
      if (this.voiceScriptId) {
        formData.append('voice_script_id', this.voiceScriptId);
      }
    }

    if (this.channel === 'both') {
      formData.append('call_escalation', this.callEscalation ? 'true' : 'false');
    }

    this.leadImportService.importLeads(formData).subscribe({
      next: (res) => {
        this.receipt = res;
        this.isImporting = false;
        this.messageService.add({
          severity: 'success',
          summary: 'Import Complete',
          detail: `Processed ${res.total} rows (${res.batches?.length || 0} product batches created).`,
        });
      },
      error: (err) => {
        this.isImporting = false;
        const msg = err?.error?.detail || err?.message || 'Failed to process lead import file';
        this.messageService.add({
          severity: 'error',
          summary: 'Import Failed',
          detail: msg,
        });
      },
    });
  }

  resetImport(): void {
    this.selectedFile = null;
    this.receipt = null;
    this.showInvalidRows = false;
  }

  goBackToLeads(): void {
    this.router.navigate(['/client/leads']);
  }

  goToCampaign(campaignId: string): void {
    this.router.navigate(['/client/campaigns', campaignId]);
  }

  goToCampaignList(): void {
    this.router.navigate(['/client/campaigns']);
  }

  getFileExt(filename?: string): string {
    if (!filename) return 'FILE';
    const parts = filename.split('.');
    return parts.length > 1 ? parts.pop()!.toUpperCase() : 'FILE';
  }

  getFileTypeLabel(filename?: string): string {
    const ext = this.getFileExt(filename);
    switch (ext) {
      case 'CSV':
        return 'CSV Spreadsheet';
      case 'XLSX':
      case 'XLS':
        return 'Excel Workbook';
      case 'DOCX':
        return 'Word Document';
      default:
        return 'Document';
    }
  }

  formatFileSize(bytes?: number): string {
    if (!bytes) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }
}

