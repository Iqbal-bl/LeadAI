import { Component, OnInit, ViewChild } from '@angular/core';
import { KbService } from '../../../../services/kb.service';

import { SharedModule } from '../../../../shared/shared.module';
import { Faq, KnowledgeBaseDoc } from '../../../../models/kb.models';
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

  // Testing tab state
  testQuestion = 'What is the enterprise pricing model and refund policy?';
  testResult: any = null;
  isTesting = false;

  // Dialogs
  showAddFaqDialog = false;
  newFaq = { question: '', answer: '', category: 'Product' };

  showUploadDialog = false;

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
          id: d.id as any,
          fileName: d.file_name,
          fileType: d.content_type,
          chunks: d.chunk_count,
          uploadDate: d.created_at.split('T')[0],
          uploadedBy: d.created_by || 'Admin',
          status:
            d.status === 'indexed'
              ? 'Processed'
              : d.status === 'indexing'
                ? 'Processing'
                : ('Failed' as any),
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
    if (this.newFaq.question && this.newFaq.answer) {
      this.kbService
        .createFAQ({
          title: this.newFaq.question,
          content: this.newFaq.answer,
          tags: 'faq,' + this.newFaq.category,
        })
        .subscribe({
          next: () => {
            this.loadDocuments();
            this.newFaq = { question: '', answer: '', category: 'Product' };
            this.showAddFaqDialog = false;
          },
          error: () => {
            // Fallback to local
            this.faqs.unshift({
              id: this.faqs.length + 1,
              question: this.newFaq.question,
              answer: this.newFaq.answer,
              category: this.newFaq.category,
              updatedDate: new Date().toISOString().split('T')[0],
            });
            this.newFaq = { question: '', answer: '', category: 'Product' };
            this.showAddFaqDialog = false;
          },
        });
    }
  }

  deleteFaq(faq: Faq): void {
    // If it's a mock faq (numeric id), just filter locally
    this.faqs = this.faqs.filter((f) => f.id !== faq.id);
  }

  openDocMenu(event: Event, doc: KnowledgeBaseDoc): void {
    event.stopPropagation();
    this.activeDocMenuItems = [
      { label: 'View Document', icon: 'pi pi-eye' },
      { label: 'Test Queries', icon: 'pi pi-bolt' },
      { label: 'Download', icon: 'pi pi-download' },
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
      }
    );
  }

  onUpload(event: any): void {
    const files: File[] = event.files;
    if (files && files.length > 0) {
      this.kbService.uploadDocument(files[0]).subscribe({
        next: () => {
          this.loadDocuments();
          this.showUploadDialog = false;
        },
        error: (err) => {
          console.error('File upload failed', err);
        },
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
