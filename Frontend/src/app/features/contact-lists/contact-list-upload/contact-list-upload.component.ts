import { Component, EventEmitter, Input, Output } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { ContactListService } from '../../../services/contact-list.service';
import { ContactListPreview } from '../../../models/contact-list.models';
import { MessageService } from 'primeng/api';

@Component({
  selector: 'app-contact-list-upload',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './contact-list-upload.component.html',
  styleUrl: './contact-list-upload.component.scss',
})
export class ContactListUploadComponent {
  @Input() visible = false;
  @Input() mode: 'upload' | 'from-leads' = 'upload';
  @Output() complete = new EventEmitter<void>();
  @Output() close = new EventEmitter<void>();

  // Upload mode
  selectedFile: File | null = null;
  listName = '';
  previewData: ContactListPreview | null = null;
  previewLoading = false;
  saveLoading = false;
  previewError = '';

  // Column mapping editable state
  editableColumnMap: Record<string, string> = {};
  availableColumns: string[] = [];

  // From-leads mode
  leadsFilterStatus = '';
  leadsFilterMinScore: number | null = null;
  leadsFilterChannel = '';
  fromLeadsLoading = false;

  acceptedFormats = '.xlsx,.xls,.csv,.tsv,.docx';

  constructor(
    private contactListService: ContactListService,
    private messageService: MessageService,
  ) {}

  onFileSelect(event: any): void {
    const files = event.files || event.target?.files;
    if (files && files.length > 0) {
      this.selectedFile = files[0];
      this.previewData = null;
      this.previewError = '';
    }
  }

  previewFile(): void {
    if (!this.selectedFile) return;

    this.previewLoading = true;
    this.previewError = '';

    this.contactListService.previewList(this.selectedFile).subscribe({
      next: (preview) => {
        this.previewData = preview;
        this.editableColumnMap = { ...preview.column_map };
        this.availableColumns = Object.keys(preview.column_map);
        this.previewLoading = false;
      },
      error: (err) => {
        this.previewLoading = false;
        this.previewError = err.error?.detail || 'Failed to parse file. Please check the format.';
      },
    });
  }

  updateColumnMapping(originalCol: string, mappedTo: string): void {
    this.editableColumnMap[originalCol] = mappedTo;
  }

  get mappingOptions(): string[] {
    return ['phone', 'name', 'email', 'company', 'skip', ...this.availableColumns.filter(c => !['phone', 'name', 'email', 'company', 'skip'].includes(c))];
  }

  saveList(): void {
    if (!this.selectedFile || !this.listName.trim()) return;

    this.saveLoading = true;
    this.contactListService.createList(this.listName, this.selectedFile, this.editableColumnMap).subscribe({
      next: () => {
        this.saveLoading = false;
        this.complete.emit();
      },
      error: (err) => {
        this.saveLoading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: err.error?.detail || 'Failed to save contact list.',
        });
      },
    });
  }

  createFromLeads(): void {
    if (!this.listName.trim()) return;

    this.fromLeadsLoading = true;
    this.contactListService.createFromLeads({
      name: this.listName,
      filters: {
        lead_status: this.leadsFilterStatus || undefined,
        min_score: this.leadsFilterMinScore ?? undefined,
        channel: this.leadsFilterChannel || undefined,
      },
    }).subscribe({
      next: () => {
        this.fromLeadsLoading = false;
        this.complete.emit();
      },
      error: (err) => {
        this.fromLeadsLoading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: err.error?.detail || 'Failed to create list from leads.',
        });
      },
    });
  }

  onClose(): void {
    this.close.emit();
  }

  get canSave(): boolean {
    return !!this.listName.trim() && !!this.previewData && !!this.selectedFile;
  }
}
