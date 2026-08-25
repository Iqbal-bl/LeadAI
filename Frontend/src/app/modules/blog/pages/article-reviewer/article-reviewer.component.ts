import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { ToastModule } from 'primeng/toast';
import { DialogModule } from 'primeng/dialog';
import { ButtonModule } from 'primeng/button';
import { TagModule } from 'primeng/tag';
import { MessageService } from 'primeng/api';
import { BlogService } from '../../services/blog.service';

@Component({
  selector: 'app-article-reviewer',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterModule,
    ToastModule,
    DialogModule,
    ButtonModule,
    TagModule,
  ],
  providers: [MessageService],
  templateUrl: './article-reviewer.component.html',
  styleUrls: ['./article-reviewer.component.scss'],
})
export class ArticleReviewerComponent implements OnInit {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private blogService = inject(BlogService);
  private messageService = inject(MessageService);

  articleId = '';
  token = '';
  loading = true;
  submitting = false;
  submittedDecision: 'approved' | 'changes_requested' | 'rejected' | null = null;

  article: any = null;

  // Review Dialog State
  showReviewDialog = false;
  selectedAction: 'approved' | 'changes_requested' | 'rejected' = 'approved';
  reviewerName = 'Editorial Manager';
  reviewerRole = 'manager';
  feedbackNote = '';

  ngOnInit(): void {
    this.route.params.subscribe((params) => {
      this.articleId = params['id'] || '';
      this.route.queryParams.subscribe((queryParams) => {
        this.token = queryParams['token'] || '';
        if (this.articleId) {
          this.loadArticle();
        } else {
          this.loading = false;
        }
      });
    });
  }

  loadArticle(): void {
    this.loading = true;
    this.blogService.getArticleByIdFromBackend(this.articleId, this.token).subscribe({
      next: (res) => {
        if (res) {
          this.article = res;
        } else {
          // Fallback to local blog post if backend is running locally
          this.blogService.getBlogPostById(this.articleId).subscribe((localPost) => {
            this.article = localPost;
          });
        }
        this.loading = false;
      },
      error: () => {
        this.blogService.getBlogPostById(this.articleId).subscribe((localPost) => {
          this.article = localPost;
        });
        this.loading = false;
      },
    });
  }

  openReviewModal(action: 'approved' | 'changes_requested' | 'rejected'): void {
    this.selectedAction = action;
    if (action === 'approved') {
      this.feedbackNote = 'Approved. Excellent writing, good SEO structure, and ready for publication.';
    } else if (action === 'changes_requested') {
      this.feedbackNote = 'Please refine the introduction section and strengthen the key takeaways.';
    } else {
      this.feedbackNote = 'The article topic or tone does not align with the current content guidelines.';
    }
    this.showReviewDialog = true;
  }

  submitDecision(): void {
    if (!this.articleId) return;

    if (this.selectedAction === 'changes_requested' && !this.feedbackNote.trim()) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Feedback Required',
        detail: 'Please provide constructive feedback explaining the requested changes.',
      });
      return;
    }

    this.submitting = true;
    this.blogService
      .submitArticleReview(
        this.articleId,
        {
          action: this.selectedAction,
          reviewer_name: this.reviewerName,
          reviewer_role: this.reviewerRole,
          feedback: this.feedbackNote,
        },
        this.token
      )
      .subscribe({
        next: (res) => {
          this.submitting = false;
          this.showReviewDialog = false;
          this.submittedDecision = this.selectedAction;

          if (this.article) {
            this.article.status = this.selectedAction;
          }

          const actionLabel =
            this.selectedAction === 'approved'
              ? 'Article Approved'
              : this.selectedAction === 'changes_requested'
              ? 'Revisions Requested'
              : 'Article Rejected';

          this.messageService.add({
            severity: this.selectedAction === 'approved' ? 'success' : 'info',
            summary: actionLabel,
            detail: `Decision submitted successfully. Author has been notified.`,
          });
        },
        error: (err) => {
          this.submitting = false;
          this.showReviewDialog = false;
          const errorMsg =
            err?.error?.detail ||
            err?.message ||
            'Review action could not be processed.';
          this.messageService.add({
            severity: 'error',
            summary: 'Action Not Allowed',
            detail: errorMsg,
          });
          // Reload article from backend to reflect true status in database
          this.loadArticle();
        },
      });
  }

  getWordCount(content?: string): number {
    if (!content) return 0;
    const cleanText = content.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
    return cleanText ? cleanText.split(' ').filter(Boolean).length : 0;
  }

  getStatusBadgeClass(status: string): string {
    switch (status) {
      case 'approved':
        return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800';
      case 'changes_requested':
        return 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300 border-amber-200 dark:border-amber-800';
      case 'rejected':
        return 'bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300 border-rose-200 dark:border-rose-800';
      case 'pending_approval':
        return 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300 border-blue-200 dark:border-blue-800';
      default:
        return 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 border-slate-200 dark:border-slate-700';
    }
  }

  getStatusLabel(status: string): string {
    switch (status) {
      case 'approved':
        return 'Approved';
      case 'changes_requested':
        return 'Revisions Requested';
      case 'rejected':
        return 'Rejected';
      case 'pending_approval':
        return 'Pending Review';
      case 'published':
        return 'Published';
      default:
        return status || 'In Review';
    }
  }
}
