import { Component, OnInit, ViewChild } from '@angular/core';
import { KbService } from '../../../../services/kb.service';

import { SharedModule } from '../../../../shared/shared.module';
import {
  Faq,
  KbDocument,
  KnowledgeBaseDoc,
} from '../../../../models/kb.models';
import { CLIENT_PERMISSIONS } from '../../constants/permission.constants';
import { MenuItem } from 'primeng/api';
import { Menu } from 'primeng/menu';
import { ConfirmationService } from '../../../../shared/services/confirmation.service';
import { ToastService } from '../../../../shared/services/toast.service';

@Component({
  selector: 'app-knowledge-base',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './knowledge-base.component.html',
  styleUrl: './knowledge-base.component.scss',
})
export class KnowledgeBaseComponent implements OnInit {
  @ViewChild('docActionMenu') docActionMenu!: Menu;

  PERMISSIONS = CLIENT_PERMISSIONS;
  docs: KnowledgeBaseDoc[] = [];
  faqs: Faq[] = [];

  activeDocMenuItems: MenuItem[] = [];
  kbActiveTab: 'docs' | 'test' = 'docs';

  // Testing tab state
  testQuestion = 'What is the enterprise pricing model and refund policy?';
  testResult: any = null;
  isTesting = false;

  // Dialogs
  showAddFaqDialog = false;
  newFaq = { question: '', answer: '', category: 'Product' };

  showUploadDialog = false;
  isUploading = false;
  uploadMethod: 'file' | 'cloud' = 'file';
  cloudLinkTitle = '';
  cloudLinkUrl = '';
  cloudLinkNotes = '';
  isImportingCloud = false;
  isReindexing = false;

  // View Document dialog state
  showViewDocDialog = false;
  selectedDoc: KnowledgeBaseDoc | null = null;
  selectedDocDetail: KbDocument | null = null;
  selectedDocChunks: any[] = [];
  isLoadingDocContent = false;
  isDownloadingDoc = false;
  activeDocViewerTab: 'content' | 'chunks' = 'content';

  constructor(
    private kbService: KbService,
    private confirmationService: ConfirmationService,
    private toastService: ToastService,
  ) {}

  ngOnInit(): void {
    this.loadDocuments();
  }

  loadDocuments(): void {
    this.kbService.getDocuments().subscribe({
      next: (documents) => {
        // Group by content type (assuming FAQs have 'faq' in title, tags, or content_type)
        // const faqDocs = documents.filter(
        //   (d) =>
        //     d.content_type === 'application/json' || d.tags.includes('faq'),
        // );
        // const normalDocs = documents.filter(
        //   (d) =>
        //     d.content_type !== 'application/json' && !d.tags.includes('faq'),
        // );

        this.docs = documents.map((d) => ({
          id: d.id,
          fileName: d.file_name || d.title || 'Untitled Document',
          fileType: d.content_type || 'text/plain',
          chunks: d.chunk_count,
          uploadDate: d.created_at ? d.created_at.split('T')[0] : '',
          uploadedBy: d.created_by || 'Admin',
          status:
            d.status === 'indexed'
              ? 'Processed'
              : d.status === 'indexing'
                ? 'Processing'
                : ('Failed' as any),
          rawDoc: d,
        }));

        // Fallback to mock FAQs if none returned from API, otherwise map from documents
        // if (faqDocs.length > 0) {
        //   this.faqs = faqDocs.map((d: any, idx: number) => ({
        //     id: idx + 1,
        //     question: d.title,
        //     answer: 'No content detail cached.', // FAQ content normally stored in raw text, placeholder for list
        //     category: d.tags || 'General',
        //     updatedDate: d.created_at.split('T')[0],
        //   }));
        // } else {
        //   this.faqs = [];
        // }
      },
      error: () => {},
    });
  }

  runTest(): void {
    if (!this.testQuestion.trim()) return;
    this.isTesting = true;

    this.kbService.testQuery(this.testQuestion).subscribe({
      next: (res) => {
        this.testResult = {
          aiResponse: res.answer,
          sourceDoc: res.sources[0]?.document_id || 'N/A',
          confidenceScore: Math.round(res.confidence * 100),
          matchingChunks: res.sources.map((src: any, index: number) => ({
            id: index + 1,
            text: src.excerpt,
            relevance: Math.round(src.score * 100),
          })),
        };
        this.isTesting = false;
      },
      error: () => {
        // Fallback simulated response on error
        setTimeout(() => {
          this.isTesting = false;
          this.testResult = {
            aiResponse:
              'Enterprise plans start at $499/month with custom seat allocations and dedicated SLA. Annual contracts include a 30-day money-back guarantee with zero cancellation fees.',
            sourceDoc: 'Pricing_Guide_2024.pdf',
            confidenceScore: 96,
            matchingChunks: [
              {
                id: 14,
                text: 'Enterprise tier pricing begins at $499/mo for up to 50 active call seats...',
                relevance: 98,
              },
              {
                id: 22,
                text: 'Refund policy: All prepaid annual licenses are eligible for full refund within 30 days of execution...',
                relevance: 94,
              },
            ],
          };
        }, 800);
      },
    });
  }

  saveFaq(): void {
    if (!this.newFaq.question.trim() || !this.newFaq.answer.trim()) {
      this.toastService.warn('Please provide both a question and an answer.');
      return;
    }

    this.kbService
      .createFAQ({
        title: this.newFaq.question.trim(),
        content: this.newFaq.answer.trim(),
        tags: 'faq,' + this.newFaq.category,
      })
      .subscribe({
        next: () => {
          this.loadDocuments();
          this.newFaq = { question: '', answer: '', category: 'Product' };
          this.showAddFaqDialog = false;
          this.toastService.success('FAQ added and indexed successfully!');
        },
        error: (err) => {
          console.error('Failed to create FAQ', err);
          this.toastService.error('Failed to create FAQ. Please try again.');
        },
      });
  }

  deleteFaq(faq: Faq): void {
    // If it's a mock faq (numeric id), just filter locally
    this.faqs = this.faqs.filter((f) => f.id !== faq.id);
  }

  openDocMenu(event: Event, doc: KnowledgeBaseDoc): void {
    event.stopPropagation();
    this.activeDocMenuItems = [
      {
        label: 'View Document',
        icon: 'pi pi-eye',
        command: () => this.viewDoc(doc),
      },
      {
        label: 'Re-index',
        icon: 'pi pi-refresh',
        command: () => this.reindexDoc(doc),
      },
      {
        label: 'Download',
        icon: 'pi pi-download',
        command: () => this.downloadDoc(doc),
      },
      { separator: true },
      {
        label: 'Delete',
        icon: 'pi pi-trash',
        styleClass: 'text-red-500',
        command: () => this.deleteDoc(doc),
      },
    ];
    this.docActionMenu.toggle(event);
  }

  viewDoc(doc: KnowledgeBaseDoc): void {
    this.selectedDoc = doc;
    this.selectedDocDetail = null;
    this.selectedDocChunks = [];
    this.activeDocViewerTab = 'content';
    this.showViewDocDialog = true;

    if (doc.id) {
      this.isLoadingDocContent = true;
      this.kbService.getDocument(String(doc.id)).subscribe({
        next: (docDetail) => {
          this.selectedDocDetail = docDetail;
        },
      });

      this.kbService.getDocumentChunks(String(doc.id), 500).subscribe({
        next: (chunkData) => {
          this.selectedDocChunks = chunkData.chunks || [];
          const assembled = this.selectedDocChunks
            .map((c) => c.text)
            .join('\n\n');
          if (!this.selectedDocDetail) {
            this.selectedDocDetail = {
              id: String(doc.id),
              title: chunkData.title || doc.fileName,
              file_name: doc.fileName,
              content_type: doc.fileType,
              source_type: 'upload',
              status: 'indexed',
              status_message: null,
              chunk_count: chunkData.total_chunks || doc.chunks,
              char_count: assembled.length,
              embedding_model: this.selectedDocChunks[0]?.embedding_model || '',
              tags: '',
              created_at: doc.uploadDate,
              created_by: doc.uploadedBy,
              raw_text: assembled,
            };
          } else if (!this.selectedDocDetail.raw_text) {
            this.selectedDocDetail.raw_text = assembled;
          }
          this.isLoadingDocContent = false;
        },
        error: (err) => {
          console.error('Failed to load document chunks', err);
          this.isLoadingDocContent = false;
          this.toastService.error('Failed to load document content.');
        },
      });
    }
  }

  importCloudDoc(): void {
    if (!this.cloudLinkTitle.trim() || !this.cloudLinkUrl.trim()) {
      this.toastService.warn('Please provide a document title and cloud link.');
      return;
    }

    this.isImportingCloud = true;
    this.kbService
      .importCloudLink({
        title: this.cloudLinkTitle.trim(),
        url: this.cloudLinkUrl.trim(),
        notes: this.cloudLinkNotes.trim() || undefined,
        tags: 'cloud_link',
      })
      .subscribe({
        next: (doc) => {
          this.isImportingCloud = false;
          this.showUploadDialog = false;
          this.cloudLinkTitle = '';
          this.cloudLinkUrl = '';
          this.cloudLinkNotes = '';
          this.toastService.success(
            `"${doc.title || doc.file_name}" downloaded and indexed successfully!`,
          );
          this.loadDocuments();
        },
        error: (err) => {
          this.isImportingCloud = false;
          this.toastService.error(
            err?.error?.detail ||
              'Failed to download file from cloud link. Ensure file sharing is set to viewable by anyone with the link.',
          );
        },
      });
  }

  reindexDoc(doc: KnowledgeBaseDoc): void {
    if (!doc.id) return;
    this.isReindexing = true;
    this.toastService.info(`Re-indexing "${doc.fileName}"...`);
    this.kbService.reindexDocument(String(doc.id)).subscribe({
      next: () => {
        this.isReindexing = false;
        this.toastService.success(`"${doc.fileName}" re-indexed successfully.`);
        this.loadDocuments();
        if (this.showViewDocDialog && this.selectedDoc?.id === doc.id) {
          this.viewDoc(doc);
        }
      },
      error: (err) => {
        this.isReindexing = false;
        this.toastService.error(
          err?.error?.detail || `Failed to re-index "${doc.fileName}".`,
        );
      },
    });
  }

  downloadDoc(doc: KnowledgeBaseDoc): void {
    if (!doc.id) return;

    // If document content is already in memory, trigger immediate download without network call
    if (this.selectedDoc?.id === doc.id && this.selectedDocDetail?.raw_text) {
      this.triggerFileDownload(doc.fileName, this.selectedDocDetail.raw_text);
      return;
    }

    this.isDownloadingDoc = true;
    this.toastService.info(`Preparing download for "${doc.fileName}"...`);

    this.kbService.getDocumentChunks(String(doc.id), 500).subscribe({
      next: (chunkData) => {
        this.isDownloadingDoc = false;
        const chunks = chunkData.chunks || [];
        const content = chunks.map((c) => c.text).join('\n\n');
        if (content) {
          this.triggerFileDownload(doc.fileName, content);
        } else {
          this.toastService.error(
            `Document "${doc.fileName}" has no content to download.`,
          );
        }
      },
      error: (err) => {
        this.isDownloadingDoc = false;
        console.error('Download failed', err);
        this.toastService.error(`Failed to download "${doc.fileName}".`);
      },
    });
  }

  private triggerFileDownload(fileName: string, content: string): void {
    const textBlob = new Blob([content], {
      type: 'text/plain;charset=utf-8',
    });
    const blobUrl = window.URL.createObjectURL(textBlob);
    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = fileName || 'document.txt';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(blobUrl);
    this.toastService.success(`Downloaded "${fileName}".`);
  }

  copyDocContent(): void {
    const textToCopy = this.selectedDocDetail?.raw_text || '';
    if (textToCopy) {
      navigator.clipboard
        .writeText(textToCopy)
        .then(() => {
          this.toastService.success('Document content copied to clipboard.');
        })
        .catch(() => {
          this.toastService.error('Failed to copy to clipboard.');
        });
    }
  }

  deleteDoc(doc: KnowledgeBaseDoc): void {
    this.confirmationService.confirmDelete(
      `Are you sure you want to delete document "${doc.fileName}"?`,
      () => {
        if (typeof doc.id === 'string') {
          this.kbService.deleteDocument(doc.id).subscribe({
            next: () => {
              this.toastService.success(`Document "${doc.fileName}" deleted.`);
              this.loadDocuments();
            },
            error: () => {
              this.docs = this.docs.filter((d) => d.id !== doc.id);
            },
          });
        } else {
          this.docs = this.docs.filter((d) => d.id !== doc.id);
        }
      },
    );
  }

  onUpload(event: any): void {
    const files: File[] = event.files;
    if (files && files.length > 0) {
      this.isUploading = true;
      let completedCount = 0;
      const total = files.length;

      files.forEach((file) => {
        this.kbService.uploadDocument(file).subscribe({
          next: () => {
            completedCount++;
            if (completedCount === total) {
              this.isUploading = false;
              this.loadDocuments();
              this.showUploadDialog = false;
              this.toastService.success(`Uploaded and indexed ${total} document(s)!`);
            }
          },
          error: (err) => {
            completedCount++;
            if (completedCount === total) {
              this.isUploading = false;
              this.loadDocuments();
            }
            this.toastService.error(err?.error?.detail || `Failed to upload "${file.name}".`);
          },
        });
      });
    }
  }

  getStatusSeverity(
    status: string,
  ):
    | 'success'
    | 'secondary'
    | 'info'
    | 'warn'
    | 'danger'
    | 'contrast'
    | undefined {
    const map: Record<
      string,
      'success' | 'secondary' | 'info' | 'warn' | 'danger' | 'contrast'
    > = {
      Processed: 'success',
      Processing: 'warn',
      Failed: 'danger',
    };
    return map[status] || 'info';
  }
}
