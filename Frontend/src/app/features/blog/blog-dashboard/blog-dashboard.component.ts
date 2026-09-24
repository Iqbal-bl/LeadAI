import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { SharedModule } from '../../../shared/shared.module';
import { BlogService } from '../../../services/blog.service';
import {
  Article,
  ArticleVersion,
  BlogSettings,
  DashboardStats,
  GenerateBlogRequest,
  ReviewActionRequest,
} from '../../../models/blog.models';
import { MessageService } from 'primeng/api';
import { Subscription } from 'rxjs';

@Component({
  selector: 'app-blog-dashboard',
  standalone: true,
  imports: [CommonModule, FormsModule, SharedModule],
  templateUrl: './blog-dashboard.component.html',
  styleUrl: './blog-dashboard.component.scss',
  providers: [MessageService],
})
export class BlogDashboardComponent implements OnInit, OnDestroy {
  // Stats
  stats: DashboardStats = {
    draft: 0,
    pending_approval: 0,
    approved: 0,
    changes_requested: 0,
    scheduled: 0,
    published: 0,
    rejected: 0,
    total: 0,
  };
  loadingStats = false;

  // Article list
  articles: Article[] = [];
  loadingArticles = false;
  activeStatusFilter: string = 'all';
  searchQuery: string = '';

  // Active Tab: 'articles' | 'settings' | 'generator'
  mainTab: 'articles' | 'settings' | 'generator' = 'articles';

  // Article Details / Editor Modal
  selectedArticle: Article | null = null;
  showArticleModal = false;
  activeArticleTab: 'preview' | 'content' | 'versions' | 'history' = 'preview';

  // Compare Versions
  compareVersion1: ArticleVersion | null = null;
  compareVersion2: ArticleVersion | null = null;

  // Review / Action Dialog
  showReviewModal = false;
  reviewAction: 'approved' | 'changes_requested' | 'rejected' | 'regenerate' = 'approved';
  reviewNotes = '';
  regenerationPrompt = '';
  publishNowOnApprove = true;
  submittingReview = false;

  // Direct Publish Dialog
  showPublishModal = false;
  publishingArticle: Article | null = null;
  publishChannels: { [key: string]: boolean } = {
    linkedin: true,
    facebook: false,
    instagram: false,
    wordpress: true,
  };
  isPublishing = false;

  // Daily Generation & Quick Generator
  isGeneratingDaily = false;
  isGeneratingCustom = false;
  customGenForm: GenerateBlogRequest = {
    topic: '',
    tone: 'thought_leadership',
    target_audience: 'B2B Leaders & Practitioners',
    keywords: [],
    target_words: 1000,
    include_images: true,
    num_images: 1,
    cta_text: 'Book Free Strategy Session Today',
    cta_url: '#strategy-session',
    target_channels: ['linkedin', 'wordpress'],
    requires_approval: true,
  };
  customKeywordsInput = '';

  // Blog Settings
  settings: BlogSettings = {
    client_id: '',
    is_auto_blog_enabled: false,
    mode: 'manual_confirmation',
    schedule_time: '09:00',
    keywords: [],
    target_words: 1000,
    include_images: true,
    num_images: 1,
    target_channels: ['linkedin', 'wordpress'],
  };
  loadingSettings = false;
  savingSettings = false;
  settingsKeywordsInput = '';
  wordpressPasswordInput = '';

  // Channel selections map for settings
  settingsChannels: { [key: string]: boolean } = {
    linkedin: true,
    facebook: false,
    instagram: false,
    wordpress: true,
  };

  private subs: Subscription = new Subscription();

  constructor(
    private blogService: BlogService,
    private messageService: MessageService
  ) {}

  ngOnInit(): void {
    this.loadStats();
    this.loadArticles();
    this.loadSettings();
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  // --------------------------------------------------------------------------
  // Data Loading
  // --------------------------------------------------------------------------
  loadStats(): void {
    this.loadingStats = true;
    this.blogService.getStats().subscribe({
      next: (res) => {
        this.stats = res;
        this.loadingStats = false;
      },
      error: (err) => {
        this.loadingStats = false;
      },
    });
  }

  loadArticles(): void {
    this.loadingArticles = true;
    this.blogService
      .listArticles(undefined, this.activeStatusFilter, this.searchQuery)
      .subscribe({
        next: (res) => {
          this.articles = res.items || [];
          this.loadingArticles = false;
        },
        error: (err) => {
          this.loadingArticles = false;
          this.messageService.add({
            severity: 'error',
            summary: 'Error',
            detail: 'Failed to load articles.',
          });
        },
      });
  }

  loadSettings(): void {
    this.loadingSettings = true;
    this.blogService.getBlogSettings().subscribe({
      next: (res) => {
        this.settings = res;
        this.settingsKeywordsInput = (res.keywords || []).join(', ');
        // Set channels map
        const channels = res.target_channels || [];
        this.settingsChannels = {
          linkedin: channels.includes('linkedin'),
          facebook: channels.includes('facebook'),
          instagram: channels.includes('instagram'),
          wordpress: channels.includes('wordpress'),
        };
        this.loadingSettings = false;
      },
      error: (err) => {
        this.loadingSettings = false;
      },
    });
  }

  getLocalTimeFromUtc(utcTimeStr?: string): string {
    if (!utcTimeStr) return '';
    const parts = utcTimeStr.split(':');
    if (parts.length < 2) return utcTimeStr;
    const now = new Date();
    const utcDate = new Date(Date.UTC(
      now.getUTCFullYear(),
      now.getUTCMonth(),
      now.getUTCDate(),
      parseInt(parts[0], 10),
      parseInt(parts[1], 10)
    ));
    return utcDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: true });
  }

  filterByStatus(status: string): void {
    this.activeStatusFilter = status;
    this.loadArticles();
  }

  onSearch(): void {
    this.loadArticles();
  }

  // --------------------------------------------------------------------------
  // 1-Click Daily Trending Topic Generation
  // --------------------------------------------------------------------------
  triggerDailyRun(): void {
    this.isGeneratingDaily = true;
    this.messageService.add({
      severity: 'info',
      summary: 'Generating Article',
      detail: 'AI is discovering a trending topic and assembling content & visuals...',
    });

    this.blogService.triggerDailyRun().subscribe({
      next: (article) => {
        this.isGeneratingDaily = false;
        this.messageService.add({
          severity: 'success',
          summary: 'Blog Created!',
          detail: `"${article.title}" generated successfully (${article.status}).`,
        });
        this.loadStats();
        this.loadArticles();
        this.openArticleModal(article);
      },
      error: (err) => {
        this.isGeneratingDaily = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Generation Failed',
          detail: err?.error?.detail || 'Failed to generate daily blog post.',
        });
      },
    });
  }

  // --------------------------------------------------------------------------
  // Custom Generation
  // --------------------------------------------------------------------------
  generateCustomArticle(): void {
    if (!this.customGenForm.topic.trim()) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Topic Required',
        detail: 'Please enter a topic or topic idea.',
      });
      return;
    }

    this.isGeneratingCustom = true;
    if (this.customKeywordsInput) {
      this.customGenForm.keywords = this.customKeywordsInput
        .split(',')
        .map((k) => k.trim())
        .filter((k) => k.length > 0);
    }

    this.blogService.generateAndSave(this.customGenForm).subscribe({
      next: (article) => {
        this.isGeneratingCustom = false;
        this.messageService.add({
          severity: 'success',
          summary: 'Article Generated',
          detail: `"${article.title}" created successfully!`,
        });
        this.loadStats();
        this.loadArticles();
        this.mainTab = 'articles';
        this.openArticleModal(article);
      },
      error: (err) => {
        this.isGeneratingCustom = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Generation Error',
          detail: err?.error?.detail || 'Failed to generate custom article.',
        });
      },
    });
  }

  // --------------------------------------------------------------------------
  // Article Modal & Review Workflow
  // --------------------------------------------------------------------------
  openArticleModal(article: Article): void {
    this.selectedArticle = article;
    this.activeArticleTab = 'preview';
    this.showArticleModal = true;

    // Default comparison versions if multiple versions exist
    if (article.versions && article.versions.length >= 2) {
      this.compareVersion1 = article.versions[article.versions.length - 2];
      this.compareVersion2 = article.versions[article.versions.length - 1];
    } else {
      this.compareVersion1 = article.versions ? article.versions[0] : null;
      this.compareVersion2 = article.versions ? article.versions[0] : null;
    }
  }

  openReviewModal(article: Article, defaultAction: 'approved' | 'changes_requested' | 'regenerate' = 'approved'): void {
    this.selectedArticle = article;
    this.reviewAction = defaultAction;
    this.reviewNotes = '';
    this.regenerationPrompt = '';
    this.publishNowOnApprove = true;
    this.showReviewModal = true;
  }

  submitReview(): void {
    if (!this.selectedArticle) return;

    this.submittingReview = true;
    const req: ReviewActionRequest = {
      action: this.reviewAction,
      notes: this.reviewNotes,
      regeneration_prompt: this.regenerationPrompt,
      publish_now: this.publishNowOnApprove,
    };

    this.blogService.reviewArticle(this.selectedArticle.id, req).subscribe({
      next: (updated) => {
        this.submittingReview = false;
        this.showReviewModal = false;
        this.selectedArticle = updated;
        this.messageService.add({
          severity: 'success',
          summary: 'Review Recorded',
          detail: `Article status updated to ${updated.status}.`,
        });
        this.loadStats();
        this.loadArticles();
      },
      error: (err) => {
        this.submittingReview = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Review Error',
          detail: err?.error?.detail || 'Failed to submit review.',
        });
      },
    });
  }

  openPublishModal(article: Article): void {
    this.publishingArticle = article;
    this.publishChannels = {
      linkedin: true,
      facebook: false,
      instagram: false,
      wordpress: true,
    };
    this.showPublishModal = true;
  }

  confirmPublish(): void {
    if (!this.publishingArticle) return;

    const channels = Object.keys(this.publishChannels).filter(
      (k) => this.publishChannels[k]
    );

    if (channels.length === 0) {
      this.messageService.add({
        severity: 'warn',
        summary: 'No Channels Selected',
        detail: 'Please select at least one channel to publish.',
      });
      return;
    }

    this.isPublishing = true;
    this.blogService.publishArticle(this.publishingArticle.id, channels).subscribe({
      next: (updated) => {
        this.isPublishing = false;
        this.showPublishModal = false;
        if (this.selectedArticle && this.selectedArticle.id === updated.id) {
          this.selectedArticle = updated;
        }
        this.messageService.add({
          severity: 'success',
          summary: 'Published Successfully!',
          detail: `Article published across ${channels.join(', ')}.`,
        });
        this.loadStats();
        this.loadArticles();
      },
      error: (err) => {
        this.isPublishing = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Publishing Error',
          detail: err?.error?.detail || 'Failed to publish article.',
        });
      },
    });
  }

  deleteArticle(article: Article, event: Event): void {
    event.stopPropagation();
    if (!confirm(`Are you sure you want to delete "${article.title}"?`)) {
      return;
    }

    this.blogService.deleteArticle(article.id).subscribe({
      next: () => {
        this.messageService.add({
          severity: 'info',
          summary: 'Deleted',
          detail: 'Article removed.',
        });
        if (this.selectedArticle?.id === article.id) {
          this.showArticleModal = false;
        }
        this.loadStats();
        this.loadArticles();
      },
      error: (err) => {
        this.messageService.add({
          severity: 'error',
          summary: 'Delete Error',
          detail: 'Failed to delete article.',
        });
      },
    });
  }

  // --------------------------------------------------------------------------
  // Save Settings
  // --------------------------------------------------------------------------
  saveSettings(): void {
    this.savingSettings = true;

    // Convert keywords input
    const keywords = this.settingsKeywordsInput
      .split(',')
      .map((k) => k.trim())
      .filter((k) => k.length > 0);

    // Convert target channels map
    const target_channels = Object.keys(this.settingsChannels).filter(
      (k) => this.settingsChannels[k]
    );

    const payload: Partial<BlogSettings> = {
      is_auto_blog_enabled: this.settings.is_auto_blog_enabled,
      mode: this.settings.mode,
      schedule_time: this.settings.schedule_time,
      topic_niche: this.settings.topic_niche,
      keywords: keywords,
      target_audience: this.settings.target_audience,
      tone: this.settings.tone,
      language: this.settings.language,
      target_words: this.settings.target_words,
      include_images: this.settings.include_images,
      num_images: this.settings.num_images,
      cta_text: this.settings.cta_text,
      cta_url: this.settings.cta_url,
      target_channels: target_channels,
      admin_notification_email: this.settings.admin_notification_email,
      wordpress_url: this.settings.wordpress_url,
      wordpress_username: this.settings.wordpress_username,
    };

    if (this.wordpressPasswordInput.trim()) {
      (payload as any).wordpress_app_password = this.wordpressPasswordInput.trim();
    }

    this.blogService.updateBlogSettings(payload).subscribe({
      next: (res) => {
        this.settings = res;
        this.savingSettings = false;
        this.wordpressPasswordInput = '';
        this.messageService.add({
          severity: 'success',
          summary: 'Settings Saved',
          detail: 'Auto-blog and channel settings updated successfully.',
        });
      },
      error: (err) => {
        this.savingSettings = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: err?.error?.detail || 'Failed to update settings.',
        });
      },
    });
  }

  // Helper
  getStatusClass(status: string): string {
    switch (status) {
      case 'published':
        return 'badge-published';
      case 'approved':
        return 'badge-approved';
      case 'pending_approval':
        return 'badge-pending';
      case 'changes_requested':
        return 'badge-changes';
      case 'scheduled':
        return 'badge-scheduled';
      case 'rejected':
        return 'badge-rejected';
      case 'generating':
        return 'badge-generating';
      default:
        return 'badge-draft';
    }
  }

  getStatusLabel(status: string): string {
    switch (status) {
      case 'pending_approval':
        return 'Pending Review';
      case 'changes_requested':
        return 'Changes Requested';
      case 'generating':
        return 'Generating...';
      default:
        return status.charAt(0).toUpperCase() + status.slice(1);
    }
  }
}
