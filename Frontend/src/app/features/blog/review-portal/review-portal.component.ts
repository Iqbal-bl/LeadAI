import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { BlogService } from '../../../services/blog.service';
import { Article, ReviewActionRequest } from '../../../models/blog.models';
import { ToastModule } from 'primeng/toast';
import { MessageService } from 'primeng/api';

@Component({
  selector: 'app-review-portal',
  standalone: true,
  imports: [CommonModule, FormsModule, ToastModule],
  template: `
    <div class="review-portal-container">
      <p-toast></p-toast>

      <!-- Loading State -->
      <div *ngIf="loading" class="center-card loading-card">
        <i class="pi pi-spin pi-spinner spinner"></i>
        <h2>Verifying secure review token...</h2>
        <p>Loading the latest blog draft and editorial package.</p>
      </div>

      <!-- Invalid / Expired Token Error -->
      <div *ngIf="!loading && error" class="center-card error-card">
        <div class="error-icon"><i class="pi pi-exclamation-triangle"></i></div>
        <h2>Review Link Expired or Invalid</h2>
        <p>{{ error }}</p>
      </div>

      <!-- Main Article Review View -->
      <div *ngIf="!loading && article" class="review-content-wrapper">
        <!-- Floating Header Bar -->
        <header class="review-header">
          <div class="header-left">
            <span class="brand-badge">LeadAI Editorial Portal</span>
            <span class="version-tag">v{{ article.current_version }}</span>
          </div>

          <div class="header-actions">
            <button class="btn btn-outline" (click)="openFeedbackModal('changes_requested')">
              <i class="pi pi-pencil"></i> Request Changes
            </button>
            <button class="btn btn-warning" (click)="openFeedbackModal('regenerate')">
              <i class="pi pi-refresh"></i> Regenerate (AI)
            </button>
            <button class="btn btn-success glow-btn" (click)="approveArticle()">
              <i class="pi pi-check-circle"></i> Approve & Publish Live
            </button>
          </div>
        </header>

        <!-- Article Reader Area -->
        <main class="reader-container">
          <div class="article-meta-banner">
            <span class="status-badge">{{ article.status }}</span>
            <h1 class="article-title">{{ article.title }}</h1>
            <div class="meta-info">
              <span>Author: <strong>{{ article.author_name || 'LeadAI Generator' }}</strong></span>
              <span>Prepared: {{ article.created_at | date:'medium' }}</span>
              <span>Target Channels: {{ (article.target_channels || []).join(', ') }}</span>
            </div>
          </div>

          <!-- Cover Image -->
          <div *ngIf="article.cover_image" class="cover-image-box">
            <img [src]="article.cover_image" alt="Article Cover Image" />
          </div>

          <!-- Formatted HTML Body -->
          <article class="article-body" [innerHTML]="article.content"></article>

          <!-- Bottom Action Deck -->
          <div class="bottom-action-deck">
            <h3>Decision Required for Publication</h3>
            <p>Approve this post to publish across connected channels or provide feedback for immediate AI regeneration.</p>
            <div class="deck-buttons">
              <button class="btn btn-outline" (click)="openFeedbackModal('changes_requested')">
                <i class="pi pi-pencil"></i> Request Changes
              </button>
              <button class="btn btn-warning" (click)="openFeedbackModal('regenerate')">
                <i class="pi pi-refresh"></i> Regenerate New Version
              </button>
              <button class="btn btn-success lg glow-btn" (click)="approveArticle()">
                <i class="pi pi-check-circle"></i> Approve & Publish to Channels
              </button>
            </div>
          </div>
        </main>
      </div>

      <!-- Feedback / Action Modal -->
      <div *ngIf="showModal" class="modal-backdrop" (click)="showModal = false">
        <div class="modal-card" (click)="$event.stopPropagation()">
          <h2>{{ modalAction === 'regenerate' ? 'AI Article Regeneration' : 'Editorial Feedback' }}</h2>
          <p>
            {{
              modalAction === 'regenerate'
                ? 'Provide prompt guidance on what to adjust (e.g. emphasize case studies, change tone, alter takeaways):'
                : 'Enter changes or feedback for the editorial log:'
            }}
          </p>

          <textarea
            rows="4"
            class="feedback-input"
            [(ngModel)]="feedbackText"
            placeholder="Type your instructions here..."
          ></textarea>

          <div class="modal-buttons">
            <button class="btn btn-outline" (click)="showModal = false">Cancel</button>
            <button
              class="btn btn-primary"
              [disabled]="submitting"
              (click)="submitFeedback()"
            >
              <i class="pi" [ngClass]="submitting ? 'pi-spin pi-spinner' : 'pi-check'"></i>
              <span>{{ submitting ? 'Submitting...' : 'Submit Decision' }}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .review-portal-container {
      min-height: 100vh;
      background: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      color: #1e293b;
    }

    .center-card {
      max-width: 520px;
      margin: 120px auto;
      background: #ffffff;
      border-radius: 16px;
      padding: 40px;
      text-align: center;
      border: 1px solid #e2e8f0;
      box-shadow: 0 10px 30px rgba(0,0,0,0.06);

      .spinner { font-size: 40px; color: #2563eb; margin-bottom: 20px; }
      .error-icon { font-size: 48px; color: #ef4444; margin-bottom: 16px; }
      h2 { font-size: 20px; font-weight: 700; margin: 0 0 10px 0; }
      p { font-size: 14.5px; color: #64748b; margin: 0; }
    }

    .review-header {
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(255, 255, 255, 0.92);
      backdrop-filter: blur(10px);
      border-bottom: 1px solid #e2e8f0;
      padding: 16px 36px;
      display: flex;
      justify-content: space-between;
      align-items: center;

      .brand-badge {
        font-weight: 800;
        font-size: 16px;
        background: linear-gradient(135deg, #2563eb, #7c3aed);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
      }

      .version-tag {
        margin-left: 10px;
        background: #e0e7ff;
        color: #3730a3;
        font-size: 12px;
        font-weight: 700;
        padding: 3px 8px;
        border-radius: 6px;
      }

      .header-actions {
        display: flex;
        gap: 12px;
      }
    }

    .reader-container {
      max-width: 880px;
      margin: 40px auto 80px auto;
      background: #ffffff;
      border-radius: 20px;
      border: 1px solid #e2e8f0;
      padding: 48px;
      box-shadow: 0 4px 25px rgba(0,0,0,0.04);
    }

    .article-meta-banner {
      border-bottom: 1px solid #f1f5f9;
      padding-bottom: 24px;
      margin-bottom: 28px;

      .status-badge {
        background: #fef3c7;
        color: #92400e;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
      }

      .article-title {
        font-size: 28px;
        font-weight: 800;
        letter-spacing: -0.5px;
        color: #0f172a;
        margin: 14px 0 12px 0;
        line-height: 1.35;
      }

      .meta-info {
        display: flex;
        gap: 20px;
        font-size: 13px;
        color: #64748b;
        flex-wrap: wrap;
      }
    }

    .cover-image-box img {
      width: 100%;
      max-height: 420px;
      object-fit: cover;
      border-radius: 14px;
      margin-bottom: 32px;
    }

    .article-body {
      font-size: 17px;
      line-height: 1.85;
      color: #334155;
    }

    .bottom-action-deck {
      margin-top: 60px;
      padding: 36px;
      background: linear-gradient(135deg, #f8fafc, #eff6ff);
      border: 1px solid #cde4fe;
      border-radius: 16px;
      text-align: center;

      h3 { font-size: 20px; font-weight: 700; margin: 0 0 8px 0; color: #0f172a; }
      p { font-size: 14.5px; color: #64748b; margin: 0 0 24px 0; }
      .deck-buttons { display: flex; justify-content: center; gap: 14px; flex-wrap: wrap; }
    }

    .btn {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 18px;
      font-size: 14px;
      font-weight: 600;
      border-radius: 10px;
      cursor: pointer;
      border: 1px solid transparent;
      transition: all 0.2s ease;

      &.btn-success {
        background: #059669;
        color: #ffffff;
        &:hover { background: #047857; }
      }

      &.btn-warning {
        background: #f59e0b;
        color: #ffffff;
        &:hover { background: #d97706; }
      }

      &.btn-outline {
        background: #ffffff;
        border-color: #cbd5e1;
        color: #334155;
        &:hover { background: #f8fafc; }
      }

      &.btn-primary {
        background: #2563eb;
        color: #ffffff;
        &:hover { background: #1d4ed8; }
      }

      &.glow-btn {
        box-shadow: 0 4px 14px rgba(5, 150, 105, 0.35);
      }

      &.lg { padding: 14px 28px; font-size: 15px; }
    }

    /* Modal */
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(15, 23, 42, 0.6);
      backdrop-filter: blur(4px);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      padding: 20px;
    }

    .modal-card {
      background: #ffffff;
      border-radius: 16px;
      padding: 32px;
      width: 540px;
      max-width: 95vw;

      h2 { font-size: 20px; font-weight: 700; margin: 0 0 8px 0; }
      p { font-size: 14px; color: #64748b; margin: 0 0 18px 0; }

      .feedback-input {
        width: 100%;
        padding: 12px;
        border-radius: 10px;
        border: 1px solid #cbd5e1;
        font-family: inherit;
        font-size: 14px;
        outline: none;
        resize: vertical;
        box-sizing: border-box;

        &:focus { border-color: #2563eb; }
      }

      .modal-buttons {
        display: flex;
        justify-content: flex-end;
        gap: 12px;
        margin-top: 20px;
      }
    }
  `],
  providers: [MessageService],
})
export class ReviewPortalComponent implements OnInit {
  token: string = '';
  loading = true;
  error: string | null = null;
  article: Article | null = null;

  showModal = false;
  modalAction: 'changes_requested' | 'regenerate' = 'changes_requested';
  feedbackText = '';
  submitting = false;

  constructor(
    private route: ActivatedRoute,
    private blogService: BlogService,
    private messageService: MessageService
  ) {}

  ngOnInit(): void {
    this.route.queryParams.subscribe((params) => {
      this.token = params['token'] || '';
      if (!this.token) {
        this.loading = false;
        this.error = 'No review token found in link URL.';
        return;
      }
      this.loadArticle();
    });
  }

  loadArticle(): void {
    this.loading = true;
    this.blogService.getArticleByToken(this.token).subscribe({
      next: (res) => {
        this.article = res;
        this.loading = false;
      },
      error: (err) => {
        this.loading = false;
        this.error = err?.error?.detail || 'Invalid or expired review link token.';
      },
    });
  }

  approveArticle(): void {
    if (!this.token) return;

    const req: ReviewActionRequest = {
      action: 'approved',
      notes: 'Approved via email review portal.',
      publish_now: true,
    };

    this.blogService.reviewArticleByToken(this.token, req).subscribe({
      next: (updated) => {
        this.article = updated;
        this.messageService.add({
          severity: 'success',
          summary: 'Approved & Published!',
          detail: 'The article is approved and published to connected channels.',
        });
      },
      error: (err) => {
        this.messageService.add({
          severity: 'error',
          summary: 'Action Failed',
          detail: err?.error?.detail || 'Failed to approve article.',
        });
      },
    });
  }

  openFeedbackModal(action: 'changes_requested' | 'regenerate'): void {
    this.modalAction = action;
    this.feedbackText = '';
    this.showModal = true;
  }

  submitFeedback(): void {
    if (!this.token) return;

    this.submitting = true;
    const req: ReviewActionRequest = {
      action: this.modalAction,
      notes: this.modalAction === 'changes_requested' ? this.feedbackText : undefined,
      regeneration_prompt: this.modalAction === 'regenerate' ? this.feedbackText : undefined,
    };

    this.blogService.reviewArticleByToken(this.token, req).subscribe({
      next: (updated) => {
        this.submitting = false;
        this.showModal = false;
        this.article = updated;
        this.messageService.add({
          severity: 'success',
          summary: this.modalAction === 'regenerate' ? 'Regeneration Complete' : 'Feedback Recorded',
          detail: `Article status updated to ${updated.status}.`,
        });
      },
      error: (err) => {
        this.submitting = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: err?.error?.detail || 'Failed to submit action.',
        });
      },
    });
  }
}
