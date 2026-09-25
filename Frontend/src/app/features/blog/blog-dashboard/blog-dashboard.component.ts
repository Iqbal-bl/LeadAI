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

  // Active Tab: 'articles' | 'history' | 'generator' | 'settings'
  mainTab: 'articles' | 'history' | 'generator' | 'settings' = 'articles';

  // Upload History Filters & Search
  historyChannelFilter: string = 'all';
  historyModeFilter: string = 'all';
  historySearchQuery: string = '';

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

  // --------------------------------------------------------------------------
  // Upload & Publishing History Helpers
  // --------------------------------------------------------------------------
  get publishedArticles(): Article[] {
    return this.articles.filter(
      (a) => a.status === 'published' || !!a.published_at || (a.results && Object.keys(a.results).length > 0)
    );
  }

  get filteredHistoryArticles(): Article[] {
    return this.publishedArticles.filter((a) => {
      // Channel filter
      if (this.historyChannelFilter !== 'all') {
        const hasChannel =
          (a.target_channels || []).includes(this.historyChannelFilter) ||
          (a.results && a.results[this.historyChannelFilter]) ||
          (this.historyChannelFilter === 'linkedin' && !!a.linkedin_post_id) ||
          (this.historyChannelFilter === 'wordpress' && !!a.wordpress_post_url) ||
          (this.historyChannelFilter === 'facebook' && !!a.facebook_post_id) ||
          (this.historyChannelFilter === 'instagram' && !!a.instagram_media_id);
        if (!hasChannel) return false;
      }

      // Mode filter
      if (this.historyModeFilter !== 'all') {
        if (this.historyModeFilter === 'daily_scheduler' && a.generation_mode !== 'daily_scheduler') return false;
        if (this.historyModeFilter === 'manual' && a.generation_mode === 'daily_scheduler') return false;
      }

      // Search query
      if (this.historySearchQuery.trim()) {
        const q = this.historySearchQuery.toLowerCase();
        const matchTitle = (a.title || '').toLowerCase().includes(q);
        const matchSummary = (a.summary || '').toLowerCase().includes(q);
        const matchTags = (a.tags || []).some((t) => t.toLowerCase().includes(q));
        const matchLiId = (a.linkedin_post_id || '').toLowerCase().includes(q);
        const matchWpUrl = (a.wordpress_post_url || '').toLowerCase().includes(q);
        if (!matchTitle && !matchSummary && !matchTags && !matchLiId && !matchWpUrl) return false;
      }

      return true;
    });
  }

  get publishedHistoryStats() {
    const list = this.publishedArticles;
    const linkedinCount = list.filter(
      (a) => !!a.linkedin_post_id || (a.results && a.results['linkedin']?.success)
    ).length;
    const wordpressCount = list.filter(
      (a) => !!a.wordpress_post_url || (a.results && a.results['wordpress']?.success)
    ).length;
    const metaCount = list.filter(
      (a) =>
        !!a.facebook_post_id ||
        !!a.instagram_media_id ||
        (a.results && (a.results['facebook']?.success || a.results['instagram']?.success))
    ).length;
    const autonomousCount = list.filter((a) => a.generation_mode === 'daily_scheduler').length;

    let lastUploadDate: Date | null = null;
    list.forEach((a) => {
      const dt = a.published_at
        ? new Date(a.published_at)
        : a.created_at
        ? new Date(a.created_at)
        : null;
      if (dt && (!lastUploadDate || dt > lastUploadDate)) {
        lastUploadDate = dt;
      }
    });

    return {
      total: list.length,
      linkedin: linkedinCount,
      wordpress: wordpressCount,
      meta: metaCount,
      autonomous: autonomousCount,
      lastUpload: lastUploadDate,
    };
  }

  getLiveUrl(article: Article, channel: string): string | null {
    if (channel === 'wordpress') {
      return article.wordpress_post_url || (article.results && article.results['wordpress']?.url) || null;
    }
    if (channel === 'linkedin') {
      const directUrl = article.results && article.results['linkedin']?.url;
      if (directUrl) return directUrl;
      const id = article.linkedin_post_id || (article.results && article.results['linkedin']?.id);
      if (!id) return null;
      if (id.startsWith('http')) return id;
      if (id.includes(':')) return `https://www.linkedin.com/feed/update/${id}/`;
      return `https://www.linkedin.com/feed/update/urn:li:share:${id}/`;
    }
    if (channel === 'facebook') {
      const directUrl = article.results && article.results['facebook']?.url;
      if (directUrl) return directUrl;
      const id = article.facebook_post_id || (article.results && article.results['facebook']?.id);
      return id ? `https://www.facebook.com/${id}` : null;
    }
    if (channel === 'instagram') {
      const directUrl = article.results && article.results['instagram']?.url;
      if (directUrl) return directUrl;
      const id = article.instagram_media_id || (article.results && article.results['instagram']?.id);
      return id ? `https://www.instagram.com/p/${id}/` : null;
    }
    return null;
  }

  getChannelStatus(article: Article, channel: string): 'published' | 'failed' | 'skipped' | 'none' {
    if (!article.results || !article.results[channel]) {
      if (channel === 'linkedin' && article.linkedin_post_id) return 'published';
      if (channel === 'wordpress' && article.wordpress_post_url) return 'published';
      if (channel === 'facebook' && article.facebook_post_id) return 'published';
      if (channel === 'instagram' && article.instagram_media_id) return 'published';
      return (article.target_channels || []).includes(channel) ? 'skipped' : 'none';
    }
    const res = article.results[channel];
    if (res.success) return 'published';
    if (res.skipped) return 'skipped';
    return 'failed';
  }

  getPublishingMechanism(article: Article): { label: string; icon: string; badgeClass: string } {
    if (article.generation_mode === 'daily_scheduler') {
      return {
        label: 'LeadAI Daily Scheduler (Autonomous)',
        icon: 'pi pi-bolt',
        badgeClass: 'mechanism-auto',
      };
    }
    if (article.reviewed_at || (article.review_notes && article.review_notes.length > 0)) {
      const reviewer = article.author_name || 'Admin';
      return {
        label: `Admin Approved (${reviewer})`,
        icon: 'pi pi-check-circle',
        badgeClass: 'mechanism-approval',
      };
    }
    return {
      label: 'Manual 1-Click Publish',
      icon: 'pi pi-send',
      badgeClass: 'mechanism-manual',
    };
  }

  openLiveUrl(url: string | null | undefined): void {
    if (url) {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  }

  copyLink(text: string | null | undefined, label: string = 'Link'): void {
    if (!text) return;
    navigator.clipboard.writeText(text);
    this.messageService.add({
      severity: 'success',
      summary: 'Copied!',
      detail: `${label} copied to clipboard.`,
    });
  }

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
