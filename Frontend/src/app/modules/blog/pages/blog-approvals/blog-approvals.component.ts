import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { MessageService, ConfirmationService } from 'primeng/api';
import { TableModule } from 'primeng/table';
import { ButtonModule } from 'primeng/button';
import { InputTextModule } from 'primeng/inputtext';
import { DialogModule } from 'primeng/dialog';
import { ToastModule } from 'primeng/toast';
import { TagModule } from 'primeng/tag';
import { AvatarModule } from 'primeng/avatar';
import { TooltipModule } from 'primeng/tooltip';
import { DropdownModule } from 'primeng/dropdown';
import { IconFieldModule } from 'primeng/iconfield';
import { InputIconModule } from 'primeng/inputicon';
import { CalendarModule } from 'primeng/calendar';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { BlogPost, BlogApprovalStatus, BlogPlatform } from '../../models/blog.model';
import { BlogService } from '../../services/blog.service';
import {
  BLOG_CATEGORY_OPTIONS,
  BLOG_SORT_OPTIONS,
} from '../../constants/blog.constants';

@Component({
  selector: 'app-blog-approvals',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    TableModule,
    ButtonModule,
    InputTextModule,
    DialogModule,
    ToastModule,
    TagModule,
    AvatarModule,
    TooltipModule,
    DropdownModule,
    IconFieldModule,
    InputIconModule,
    CalendarModule,
    ConfirmDialogModule,
  ],
  providers: [MessageService, ConfirmationService],
  templateUrl: './blog-approvals.component.html',
})
export class BlogApprovalsComponent implements OnInit, OnDestroy {
  private blogService = inject(BlogService);
  private messageService = inject(MessageService);
  private confirmationService = inject(ConfirmationService);
  private router = inject(Router);
  private sub = new Subscription();

  // All blogs & filtered blogs
  allPosts: BlogPost[] = [];
  filteredPosts: BlogPost[] = [];
  platforms: BlogPlatform[] = [];
  loading = false;

  // View & Filter states
  viewMode: 'table' | 'grid' = 'table';
  searchQuery = '';
  selectedStatusFilter: 'all' | BlogApprovalStatus = 'all';
  selectedCategory = 'all';
  selectedSort = 'newest';

  // Available Filter Categories & Sort Options
  categoryOptions: { label: string; value: string }[] = [...BLOG_CATEGORY_OPTIONS];

  sortOptions: { label: string; value: string }[] = [...BLOG_SORT_OPTIONS];

  // Preview Dialog State
  showPreviewDialog = false;
  selectedPostForPreview: BlogPost | null = null;
  activePreviewTab: 'article' | 'seo' | 'history' = 'article';

  // Approve Dialog State
  showApproveDialog = false;
  selectedPostForApprove: BlogPost | null = null;
  approvalNote = '';

  // Request Changes Dialog State
  showRejectDialog = false;
  selectedPostForReject: BlogPost | null = null;
  rejectionReason = '';

  // Schedule Dialog State
  showScheduleDialog = false;
  selectedPostForSchedule: BlogPost | null = null;
  scheduledDateTime: Date = new Date(Date.now() + 24 * 60 * 60 * 1000);

  // Status Metrics
  metrics = {
    total: 0,
    pending: 0,
    approved: 0,
    changesRequested: 0,
    published: 0,
    scheduled: 0,
    drafts: 0,
  };

  ngOnInit(): void {
    this.loadPlatforms();
    this.loadStats();
    this.loadArticles();
  }

  ngOnDestroy(): void {
    this.sub.unsubscribe();
  }

  private loadPlatforms(): void {
    this.blogService.getPlatforms().subscribe((res) => {
      this.platforms = res;
    });
  }

  loadStats(): void {
    this.sub.add(
      this.blogService.getArticleStats().subscribe({
        next: (stats) => {
          if (stats) {
            // Total pipeline count excludes raw drafts
            this.metrics.total = (stats.total_articles || 0) - (stats.draft || 0);
            this.metrics.pending = stats.pending_approval || 0;
            this.metrics.approved = stats.approved || 0;
            this.metrics.changesRequested = stats.changes_requested || 0;
            this.metrics.published = stats.published || 0;
            this.metrics.scheduled = stats.scheduled || 0;
            this.metrics.drafts = 0;
          }
        },
        error: (err) => {
          console.warn('Could not load stats from backend:', err);
        },
      })
    );
  }

  loadArticles(): void {
    this.sub.add(
      this.blogService.getArticlesFromBackend().subscribe({
        next: (posts) => {
          // Only show posts that have entered the editorial pipeline (exclude draft blogs)
          this.allPosts = (posts || []).filter((p) => p.status !== 'draft');
          this.applyFilters();
        },
        error: (err) => {
          console.warn('Could not load articles from backend:', err);
          this.allPosts = [];
          this.filteredPosts = [];
        },
      })
    );
  }

  submitDraftForApproval(post: BlogPost): void {
    this.blogService.submitForApproval(post, 'Submitted draft for editorial review.').subscribe(() => {
      this.messageService.add({
        severity: 'success',
        summary: 'Submitted for Approval',
        detail: `"${post.title}" has been sent for review and approval.`,
      });
    });
  }

  applyFilters(): void {
    let result = [...this.allPosts];

    // Status Filter
    if (this.selectedStatusFilter !== 'all') {
      result = result.filter((p) => p.status === this.selectedStatusFilter);
    }

    // Category Filter
    if (this.selectedCategory !== 'all') {
      result = result.filter((p) => p.category?.toLowerCase() === this.selectedCategory.toLowerCase());
    }

    // Search Query
    if (this.searchQuery.trim()) {
      const q = this.searchQuery.toLowerCase().trim();
      result = result.filter((p) =>
        (p.title || '').toLowerCase().includes(q) ||
        (p.excerpt || '').toLowerCase().includes(q) ||
        (p.author?.name || '').toLowerCase().includes(q) ||
        (p.tags || []).some((t) => t.toLowerCase().includes(q)) ||
        (p.slug || '').toLowerCase().includes(q)
      );
    }

    // Sorting
    if (this.selectedSort === 'newest') {
      result.sort((a, b) => new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime());
    } else if (this.selectedSort === 'oldest') {
      result.sort((a, b) => new Date(a.createdAt || 0).getTime() - new Date(b.createdAt || 0).getTime());
    } else if (this.selectedSort === 'title_asc') {
      result.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
    } else if (this.selectedSort === 'words_desc') {
      result.sort((a, b) => (b.wordCount || 0) - (a.wordCount || 0));
    }

    this.filteredPosts = result;
  }

  setStatusFilter(status: 'all' | BlogApprovalStatus): void {
    this.selectedStatusFilter = status;
    this.applyFilters();
  }

  /* ==========================================================
     Navigation & Editing
  ========================================================== */

  createBlogPost(): void {
    this.router.navigate(['/client/create-blog']);
  }

  editPostInComposer(post: BlogPost): void {
    this.router.navigate(['/client/create-blog'], { queryParams: { id: post.id } });
  }

  /* ==========================================================
     Article Preview Modal
  ========================================================== */

  openPreviewModal(post: BlogPost): void {
    this.selectedPostForPreview = post;
    this.activePreviewTab = 'article';
    this.showPreviewDialog = true;
  }

  closePreviewModal(): void {
    this.showPreviewDialog = false;
    this.selectedPostForPreview = null;
  }

  /* ==========================================================
     Approval Workflow Actions
  ========================================================== */

  openApproveModal(post: BlogPost): void {
    this.selectedPostForApprove = post;
    this.approvalNote = 'Post reviewed and approved for publication.';
    this.showApproveDialog = true;
  }

  confirmApproval(): void {
    if (!this.selectedPostForApprove) return;
    const post = this.selectedPostForApprove;
    this.blogService.approvePost(post, this.approvalNote).subscribe({
      next: (updated) => {
        this.showApproveDialog = false;
        this.selectedPostForApprove = null;
        if (this.selectedPostForPreview?.id === updated.id) {
          this.selectedPostForPreview = updated;
        }
        this.loadStats();
        this.loadArticles();
        this.messageService.add({
          severity: 'success',
          summary: 'Blog Approved',
          detail: `"${post.title}" has been approved for publication.`,
        });
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Action Failed',
          detail: 'Could not approve the blog post.',
        });
      },
    });
  }

  openRejectModal(post: BlogPost): void {
    this.selectedPostForReject = post;
    this.rejectionReason = '';
    this.showRejectDialog = true;
  }

  confirmReject(): void {
    if (!this.selectedPostForReject) return;
    if (!this.rejectionReason.trim()) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Feedback Required',
        detail: 'Please provide editorial revision instructions for the author.',
      });
      return;
    }

    const post = this.selectedPostForReject;
    this.blogService.requestChanges(post, this.rejectionReason.trim()).subscribe({
      next: (updated) => {
        this.showRejectDialog = false;
        this.selectedPostForReject = null;
        if (this.selectedPostForPreview?.id === updated.id) {
          this.selectedPostForPreview = updated;
        }
        this.loadStats();
        this.loadArticles();
        this.messageService.add({
          severity: 'info',
          summary: 'Revisions Requested',
          detail: `Feedback sent back to ${post.author?.name || 'author'}.`,
        });
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Action Failed',
          detail: 'Could not update blog status.',
        });
      },
    });
  }

  publishPostNow(post: BlogPost): void {
    this.confirmationService.confirm({
      message: `Are you ready to publish "${post.title}" immediately to [${(post.selectedPlatforms || ['wordpress', 'medium']).join(', ')}]?`,
      header: 'Publish Blog Post Now',
      icon: 'pi pi-send',
      acceptButtonStyleClass: 'p-button-primary',
      rejectButtonStyleClass: 'p-button-text',
      accept: () => {
        this.blogService.publishPost(post).subscribe({
          next: (updated) => {
            if (this.selectedPostForPreview?.id === updated.id) {
              this.selectedPostForPreview = updated;
            }
            this.loadStats();
            this.loadArticles();
            this.messageService.add({
              severity: 'success',
              summary: 'Published Successfully',
              detail: `"${post.title}" is now live on selected channels.`,
            });
          },
          error: () => {
            this.messageService.add({
              severity: 'error',
              summary: 'Publish Error',
              detail: 'Failed to publish post.',
            });
          },
        });
      },
    });
  }

  openScheduleModal(post: BlogPost): void {
    this.selectedPostForSchedule = post;
    this.scheduledDateTime = new Date(Date.now() + 24 * 60 * 60 * 1000);
    this.showScheduleDialog = true;
  }

  confirmSchedule(): void {
    if (!this.selectedPostForSchedule) return;
    const post = this.selectedPostForSchedule;
    const formattedDate = this.scheduledDateTime.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });

    this.blogService.schedulePost(post, formattedDate).subscribe({
      next: (updated) => {
        this.showScheduleDialog = false;
        this.selectedPostForSchedule = null;
        if (this.selectedPostForPreview?.id === updated.id) {
          this.selectedPostForPreview = updated;
        }
        this.loadStats();
        this.loadArticles();
        this.messageService.add({
          severity: 'success',
          summary: 'Release Scheduled',
          detail: `"${post.title}" scheduled for ${formattedDate}.`,
        });
      },
    });
  }

  deletePost(post: BlogPost): void {
    this.confirmationService.confirm({
      message: `Are you sure you want to remove "${post.title}"? This cannot be undone.`,
      header: 'Delete Blog Post',
      icon: 'pi pi-trash',
      acceptButtonStyleClass: 'p-button-danger',
      rejectButtonStyleClass: 'p-button-text',
      accept: () => {
        this.blogService.deletePost(post.id).subscribe({
          next: () => {
            if (this.showPreviewDialog && this.selectedPostForPreview?.id === post.id) {
              this.showPreviewDialog = false;
            }
            this.loadStats();
            this.loadArticles();
            this.messageService.add({
              severity: 'info',
              summary: 'Post Removed',
              detail: `"${post.title}" has been deleted.`,
            });
          },
        });
      },
    });
  }

  /* ==========================================================
     UI Helpers & Styling
  ========================================================== */

  getStatusBadgeSeverity(status: BlogApprovalStatus): 'success' | 'info' | 'warn' | 'danger' | 'secondary' | 'contrast' {
    switch (status) {
      case 'approved':
        return 'success';
      case 'pending_approval':
        return 'warn';
      case 'published':
        return 'info';
      case 'scheduled':
        return 'contrast';
      case 'rejected':
        return 'danger';
      case 'draft':
      default:
        return 'secondary';
    }
  }

  getStatusLabel(status: BlogApprovalStatus): string {
    switch (status) {
      case 'pending_approval':
        return 'Pending Approval';
      case 'approved':
        return 'Approved';
      case 'rejected':
        return 'Changes Requested';
      case 'published':
        return 'Published';
      case 'scheduled':
        return 'Scheduled';
      case 'draft':
        return 'Draft';
      default:
        return status;
    }
  }

  getPlatformIcon(key: string): string {
    switch (key) {
      case 'wordpress':
        return 'pi pi-globe';
      case 'medium':
        return 'pi pi-book';
      case 'linkedin':
        return 'pi pi-linkedin';
      case 'devto':
        return 'pi pi-code';
      case 'ghost':
        return 'pi pi-file';
      case 'custom_cms':
        return 'pi pi-server';
      default:
        return 'pi pi-share-alt';
    }
  }

  getPlatformLabel(key: string): string {
    switch (key) {
      case 'wordpress':
        return 'WordPress';
      case 'medium':
        return 'Medium';
      case 'linkedin':
        return 'LinkedIn';
      case 'devto':
        return 'Dev.to';
      case 'ghost':
        return 'Ghost';
      case 'custom_cms':
        return 'Webhook API';
      default:
        return key;
    }
  }

  getAuthorInitials(name?: string): string {
    if (!name) return 'AU';
    const parts = name.trim().split(' ');
    if (parts.length >= 2) {
      return (parts[0][0] + parts[1][0]).toUpperCase();
    }
    return name.slice(0, 2).toUpperCase();
  }

  formatDate(dateString?: string): string {
    if (!dateString) return '—';
    try {
      const d = new Date(dateString);
      return d.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      });
    } catch {
      return dateString;
    }
  }
}
