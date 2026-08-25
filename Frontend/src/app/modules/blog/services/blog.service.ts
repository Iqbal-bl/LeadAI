import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { BehaviorSubject, Observable, of, throwError } from 'rxjs';
import { map, tap, catchError, switchMap } from 'rxjs/operators';
import {
  BlogPost,
  BlogPromptRequest,
  BlogPlatform,
  BlogApprovalStatus,
  BlogReviewNote,
} from '../models/blog.model';
import { DEFAULT_BLOG_PLATFORMS } from '../constants/blog.constants';
import { AuthService } from '../../../services/auth.service';

@Injectable({
  providedIn: 'root',
})
export class BlogService {
  private http = inject(HttpClient);
  private authService = inject(AuthService);
  private readonly apiUrl = 'http://127.0.0.1:8000';

  get authorName(): string {
    return this.authService.getCurrentUser()?.full_name || 'Author';
  }
  get authorRole(): string {
    return this.authService.getCurrentUser()?.role || 'author';
  }
  get authorEmail(): string {
    return this.authService.getCurrentUser()?.email || '';
  }

  /* ==========================================================
     Supported CMS / Blog Publishing Channels
  ========================================================== */
  private defaultPlatforms: BlogPlatform[] = [...DEFAULT_BLOG_PLATFORMS];

  private currentPostSubject = new BehaviorSubject<BlogPost | null>(null);
  public currentPost$ = this.currentPostSubject.asObservable();

  private isGeneratingSubject = new BehaviorSubject<boolean>(false);
  public isGenerating$ = this.isGeneratingSubject.asObservable();

  private postsSubject = new BehaviorSubject<BlogPost[]>([]);
  public posts$ = this.postsSubject.asObservable();

  /**
   * Get all blog posts observable
   */
  getBlogPosts(): Observable<BlogPost[]> {
    return this.posts$;
  }

  /**
   * Fetch all articles from Backend GET /api/articles
   */
  getArticlesFromBackend(): Observable<BlogPost[]> {
    return this.http.get<any>(`${this.apiUrl}/api/articles`).pipe(
      map((res) => {
        const rawList: any[] = Array.isArray(res)
          ? res
          : res?.items && Array.isArray(res.items)
          ? res.items
          : [];
        const mappedList: BlogPost[] = rawList.map((item) =>
          this.mapApiArticleToBlogPost(item)
        );
        this.postsSubject.next(mappedList);
        return mappedList;
      }),
      catchError((err) => {
        console.error('Backend GET /api/articles failed:', err);
        this.postsSubject.next([]);
        return throwError(() => err);
      })
    );
  }

  /**
   * Fetch article statistics from Backend GET /api/articles/stats
   */
  getArticleStats(): Observable<{
    total_articles: number;
    draft: number;
    pending_approval: number;
    approved: number;
    changes_requested: number;
    published: number;
    scheduled: number;
  }> {
    return this.http.get<any>(`${this.apiUrl}/api/articles/stats`).pipe(
      catchError((err) => {
        console.error('Backend GET /api/articles/stats failed:', err);
        return throwError(() => err);
      })
    );
  }

  /**
   * Maps backend ArticleResponse to frontend BlogPost model
   */
  public mapApiArticleToBlogPost(item: any): BlogPost {
    const wordCount = this.calculateWordCount(item.content || '');
    const rawStatus = (item.status || 'draft').toLowerCase();
    let mappedStatus: BlogApprovalStatus = 'draft';
    if (rawStatus === 'pending_approval' || rawStatus === 'pending') {
      mappedStatus = 'pending_approval';
    } else if (rawStatus === 'approved') {
      mappedStatus = 'approved';
    } else if (rawStatus === 'changes_requested' || rawStatus === 'rejected') {
      mappedStatus = 'rejected';
    } else if (rawStatus === 'published') {
      mappedStatus = 'published';
    } else if (rawStatus === 'scheduled') {
      mappedStatus = 'scheduled';
    }

    const reviewNotes: BlogReviewNote[] = (item.review_notes || []).map(
      (n: any) => ({
        id: n.id ? String(n.id) : 'rn_' + Date.now(),
        authorName: n.author_name || 'Reviewer',
        authorRole:
          n.author_role === 'admin' || n.author_role === 'manager'
            ? 'admin'
            : 'author',
        timestamp: n.created_at || new Date().toISOString(),
        content: n.content || n.note || '',
        actionTaken: n.action_taken || 'submitted',
      })
    );

    return {
      id: String(item.id),
      title: item.title || 'Untitled Article',
      subtitle: item.summary || item.subtitle || '',
      slug: item.slug || this.slugify(item.title || 'article'),
      content: item.content || '',
      excerpt:
        item.summary ||
        (item.content
          ? item.content.replace(/<[^>]*>/g, '').slice(0, 160) + '...'
          : ''),
      coverImage:
        item.cover_image ||
        (item.images && item.images.length > 0 ? item.images[0] : ''),
      images: item.images || [],
      author: {
        name: item.author_name || 'Author',
        title: item.author_role || 'author',
        avatar:
          'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=120&auto=format&fit=crop&q=80',
      },
      tags: item.tags || [],
      category: item.category || 'Artificial Intelligence',
      status: mappedStatus,
      createdAt: item.created_at || item.createdAt || new Date().toISOString(),
      updatedAt: item.updated_at || item.updatedAt || new Date().toISOString(),
      scheduledDate: item.scheduled_at,
      publishedAt:
        mappedStatus === 'published'
          ? item.updated_at || item.created_at
          : undefined,
      reviewNotes: reviewNotes,
      wordCount: wordCount,
      selectedPlatforms: ['wordpress', 'medium', 'linkedin'],
      seo: {
        metaTitle: item.title,
        metaDescription: item.summary,
        slug: item.slug,
        focusKeywords: item.tags || [],
      },
    };
  }

  /**
   * Get single blog post by ID
   */
  getBlogPostById(id: string): Observable<BlogPost | null> {
    const posts = this.postsSubject.getValue();
    const post = posts.find((p) => p.id === id) || null;
    if (post) {
      return of({ ...post });
    }
    return this.getArticleByIdFromBackend(id).pipe(
      map((article) => (article ? this.mapApiArticleToBlogPost(article) : null))
    );
  }

  /**
   * Save or update blog post draft in backend API
   */
  savePost(post: BlogPost): Observable<BlogPost> {
    if (!post.title && !post.content) {
      return of(post);
    }

    const headers = new HttpHeaders({
      'Content-Type': 'application/json',
    });

    const payload = {
      title: post.title || 'Untitled Draft',
      content: post.content || '',
      images: post.images && post.images.length > 0 ? post.images : (post.coverImage ? [post.coverImage] : []),
      tags: post.tags && post.tags.length > 0 ? post.tags : ['AI', 'LangGraph', 'FastAPI'],
      status: post.status || 'draft',
      author_name: post.author?.name || this.authorName,
      author_role: post.author?.title || this.authorRole,
      author_email: post.author?.email || this.authorEmail || undefined,
    };

    return this.http.post<any>(`${this.apiUrl}/api/articles`, payload, { headers }).pipe(
      map((res: any) => {
        const savedPost: BlogPost = {
          ...post,
          id: res?.id ? String(res.id) : post.id,
          title: res?.title || post.title,
          content: res?.content || post.content,
          tags: res?.tags || post.tags,
          images: res?.images || post.images,
          status: res?.status || post.status || 'draft',
          updatedAt: res?.updated_at || new Date().toISOString(),
        };
        this.updateLocalPosts(savedPost);
        return savedPost;
      }),
      catchError((error) => {
        console.error('Backend /api/articles call failed:', error);
        return throwError(() => error);
      })
    );
  }

  private updateLocalPosts(post: BlogPost): void {
    const current = this.postsSubject.getValue();
    const index = current.findIndex((p) => p.id === post.id);
    let updated: BlogPost[];

    if (index >= 0) {
      updated = [...current];
      updated[index] = { ...post, updatedAt: new Date().toISOString() };
    } else {
      updated = [
        {
          ...post,
          createdAt: post.createdAt || new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        },
        ...current,
      ];
    }

    this.postsSubject.next(updated);
    this.currentPostSubject.next(post);
  }

  /**
   * Delete a blog post by ID
   */
  deletePost(id: string): Observable<boolean> {
    return this.http.delete<any>(`${this.apiUrl}/api/articles/${id}`).pipe(
      map(() => {
        const current = this.postsSubject.getValue();
        const updated = current.filter((p) => p.id !== id);
        this.postsSubject.next(updated);
        return true;
      }),
      catchError((err) => {
        console.error(`Backend DELETE /api/articles/${id} failed:`, err);
        return throwError(() => err);
      })
    );
  }

  /**
   * Get all supported publishing platforms
   */
  getPlatforms(): Observable<BlogPlatform[]> {
    return of([...this.defaultPlatforms]);
  }

  /**
   * Create an initial blank blog post draft
   */
  createInitialDraft(): BlogPost {
    const now = new Date().toISOString();
    return {
      id: '',
      title: '',
      subtitle: '',
      slug: '',
      content: '',
      excerpt: '',
      coverImage: '',
      coverImageCaption: '',
      author: {
        name: this.authorName,
        avatar: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=120&auto=format&fit=crop&q=80',
        title: this.authorRole,
        email: this.authorEmail,
      },
      tags: [],
      category: 'General',
      status: 'draft',
      createdAt: now,
      updatedAt: now,
      selectedPlatforms: ['wordpress', 'medium'],
      seo: {
        metaTitle: '',
        metaDescription: '',
        slug: '',
        focusKeywords: [],
      },
      reviewNotes: [],
      wordCount: 0,
      readingTimeMinutes: 0,
    };
  }

  /**
   * Update active working draft in-memory
   */
  saveWorkingDraft(post: BlogPost): Observable<BlogPost> {
    const updated = { ...post, updatedAt: new Date().toISOString() };
    this.currentPostSubject.next(updated);
    return of(updated);
  }

  /**
   * Get current working draft in-memory
   */
  getWorkingDraft(): Observable<BlogPost | null> {
    return of(this.currentPostSubject.getValue());
  }

  /**
   * Clear active working draft in-memory
   */
  clearWorkingDraft(): void {
    this.currentPostSubject.next(null);
  }

  /**
   * Generate Blog Post Draft from AI Prompt (HTTP Integration with LangGraph Backend)
   */
  generateBlogWithAi(request: BlogPromptRequest): Observable<BlogPost> {
    this.isGeneratingSubject.next(true);

    const isTextAndImage = request.blog_type === 'text_and_image' || request.include_images === true;
    const numImages = isTextAndImage ? (request.num_images && request.num_images > 0 ? request.num_images : 1) : 0;

    const payload = {
      topic: request.topic,
      tone: request.tone || 'professional',
      target_audience: request.target_audience || request.targetAudience || 'General Audience',
      keywords: request.keywords && request.keywords.length > 0 ? request.keywords : [request.topic || 'Generative AI'],
      blog_type: isTextAndImage ? 'text_and_image' : 'text_only',
      include_images: isTextAndImage,
      num_images: numImages,
      target_words: request.target_words || 300,
      target_length: request.target_length || request.length || 'short',
      language: request.language || 'English',
      cta_text: request.cta_text || 'Learn more about AI.',
    };

    return this.http.post<any>(`${this.apiUrl}/api/blog/generate`, payload).pipe(
      map((response) => this.mapBackendResponseToBlogPost(response, payload)),
      tap((post) => {
        this.saveWorkingDraft(post);
        this.isGeneratingSubject.next(false);
      }),
      catchError((error) => {
        this.isGeneratingSubject.next(false);
        console.error('Backend AI blog generation failed:', error);
        return throwError(() => error);
      })
    );
  }

  /**
   * Maps backend LangGraph response to BlogPost entity
   */
  private mapBackendResponseToBlogPost(response: any, payload: any): BlogPost {
    const slug = this.slugify(response.title || payload.topic || 'generative-ai-blog');
    const tags = response.tags || (payload.keywords && payload.keywords.length > 0 ? payload.keywords : ['Generative AI']);
    
    let htmlContent = response.content || response.html || response.markdown_content || '';
    if (htmlContent && !htmlContent.includes('<p>') && !htmlContent.includes('<h2>') && !htmlContent.includes('<div>')) {
      htmlContent = this.convertMarkdownToHtml(htmlContent);
    }

    const wordCount = this.calculateWordCount(htmlContent);
    const readingTime = Math.max(1, Math.ceil(wordCount / 200));
    const imagesList: string[] = Array.isArray(response.images) ? response.images : [];
    const coverImage = response.cover_image || (imagesList.length > 0 ? imagesList[0] : '');

    return {
      id: response.id ? String(response.id) : '',
      title: response.title || (payload.topic ? this.capitalize(payload.topic) : 'Untitled AI Article'),
      subtitle: response.subtitle || `A concise ${payload.tone} guide designed for ${payload.target_audience}.`,
      slug: slug,
      content: htmlContent,
      excerpt: response.excerpt || response.summary || (htmlContent ? htmlContent.replace(/<[^>]*>/g, '').slice(0, 160) + '...' : ''),
      coverImage: coverImage,
      coverImageCaption: response.cover_image_caption || '',
      images: imagesList,
      author: {
        name: this.authorName,
        avatar: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=120&auto=format&fit=crop&q=80',
        title: this.authorRole,
        email: this.authorEmail,
      },
      tags: tags,
      category: 'Artificial Intelligence',
      status: 'draft',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      selectedPlatforms: ['wordpress', 'medium', 'linkedin'],
      seo: {
        metaTitle: `${response.title || payload.topic} | LeadAI Insights`,
        metaDescription: `Discover key concepts and insights about ${payload.topic}.`,
        slug: slug,
        focusKeywords: tags,
        ogImage: coverImage,
      },
      reviewNotes: [
        {
          id: 'rn_' + Date.now(),
          authorName: 'AI Engine',
          authorRole: 'editor',
          timestamp: new Date().toISOString(),
          content: `Draft generated via AI engine (${payload.tone} tone, ${payload.target_words} target words).`,
          actionTaken: 'submitted',
        },
      ],
      wordCount: wordCount,
      readingTimeMinutes: readingTime,
    };
  }

  /**
   * Submit post for Admin Review & Approval
   * Calls POST /api/articles/{article_id}/submit to set status to 'pending_approval' and dispatch review emails
   */
  submitForApproval(post: BlogPost, noteText?: string): Observable<BlogPost> {
    const headers = new HttpHeaders({
      'Content-Type': 'application/json',
    });

    const submitPayload = {
      note: noteText || 'Submitted for review and editorial approval.',
      author_name: post.author?.name || this.authorName,
      author_role: post.author?.title || this.authorRole,
    };

    const executeSubmit = (articleId: string, currentPost: BlogPost): Observable<BlogPost> => {
      return this.http
        .post<any>(`${this.apiUrl}/api/articles/${articleId}/submit`, submitPayload, { headers })
        .pipe(
          map((res: any) => {
            const existingNotes = (currentPost.reviewNotes || []).filter((n) => n.actionTaken !== 'draft');
            const updatedNotes: BlogReviewNote[] = [
              ...existingNotes,
              {
                id: 'rn_' + Date.now(),
                authorName: submitPayload.author_name,
                authorRole: 'author',
                timestamp: new Date().toISOString(),
                content: submitPayload.note,
                actionTaken: 'submitted',
              },
            ];

            const updatedPost: BlogPost = {
              ...currentPost,
              id: String(res?.id || articleId),
              status: 'pending_approval',
              updatedAt: res?.updated_at || new Date().toISOString(),
              reviewNotes: updatedNotes,
            };
            this.updateLocalPosts(updatedPost);
            return updatedPost;
          }),
          catchError((error) => {
            console.error('Backend /api/articles/{id}/submit call failed:', error);
            return throwError(() => error);
          })
        );
    };

    if (!post.id || post.id.startsWith('blog_')) {
      return this.savePost(post).pipe(
        switchMap((saved) => executeSubmit(saved.id, saved))
      );
    } else {
      return executeSubmit(post.id, post);
    }
  }

  /**
   * Fetch single article by ID from Backend API
   */
  getArticleByIdFromBackend(id: string, token?: string): Observable<any> {
    let headers = new HttpHeaders({
      'Content-Type': 'application/json',
    });
    if (token) {
      headers = headers.set('Authorization', `Bearer ${token}`);
    }

    return this.http.get<any>(`${this.apiUrl}/api/articles/${id}`, { headers }).pipe(
      catchError((err) => {
        console.error(`Backend GET /api/articles/${id} failed:`, err);
        return throwError(() => err);
      })
    );
  }

  /**
   * Submit Review decision (approved, changes_requested, rejected)
   * Calls POST /api/articles/{article_id}/review
   */
  submitArticleReview(
    articleId: string,
    reviewData: {
      action: 'approved' | 'changes_requested' | 'rejected';
      reviewer_name?: string;
      reviewer_role?: string;
      feedback?: string;
    },
    token?: string
  ): Observable<any> {
    let headers = new HttpHeaders({
      'Content-Type': 'application/json',
    });
    if (token) {
      headers = headers.set('Authorization', `Bearer ${token}`);
    }

    const payload = {
      action: reviewData.action,
      reviewer_name: reviewData.reviewer_name || this.authorName || 'Manager',
      reviewer_role: reviewData.reviewer_role || this.authorRole || 'manager',
      feedback: reviewData.feedback || '',
    };

    return this.http.post<any>(`${this.apiUrl}/api/articles/${articleId}/review`, payload, { headers }).pipe(
      tap((res) => {
        const current = this.postsSubject.getValue();
        const existing = current.find((p) => p.id === articleId);
        if (existing) {
          const updatedNotes = [
            ...(existing.reviewNotes || []),
            {
              id: 'rn_' + Date.now(),
              authorName: payload.reviewer_name,
              authorRole: 'admin' as const,
              timestamp: new Date().toISOString(),
              content: payload.feedback || `Review decision: ${payload.action}`,
              actionTaken: payload.action as any,
            },
          ];
          this.updateLocalPosts({
            ...existing,
            status: payload.action === 'approved' ? 'approved' : 'rejected',
            reviewNotes: updatedNotes,
            updatedAt: new Date().toISOString(),
          });
        }
      }),
      catchError((error) => {
        console.error(`Backend /api/articles/${articleId}/review failed:`, error);
        return throwError(() => error);
      })
    );
  }

  /**
   * Admin approves blog post
   */
  approvePost(post: BlogPost, adminFeedback?: string): Observable<any> {
    return this.submitArticleReview(post.id, {
      action: 'approved',
      reviewer_name: this.authorName,
      reviewer_role: this.authorRole,
      feedback: adminFeedback || 'Post reviewed and approved for publication.',
    });
  }

  /**
   * Admin requests revisions or rejects
   */
  requestChanges(post: BlogPost, feedbackReason: string): Observable<any> {
    return this.submitArticleReview(post.id, {
      action: 'changes_requested',
      reviewer_name: this.authorName,
      reviewer_role: this.authorRole,
      feedback: feedbackReason || 'Please review key stats and refine the conclusion call to action.',
    });
  }

  /**
   * Publish approved blog post to selected platforms
   */
  publishPost(post: BlogPost): Observable<BlogPost> {
    const updatedPost: BlogPost = {
      ...post,
      status: 'published',
      publishedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    return this.savePost(updatedPost);
  }

  /**
   * Schedule blog post for future publish
   */
  schedulePost(post: BlogPost, scheduledDate: string): Observable<BlogPost> {
    const updatedPost: BlogPost = {
      ...post,
      status: 'scheduled',
      scheduledDate: scheduledDate,
      updatedAt: new Date().toISOString(),
    };

    return this.savePost(updatedPost);
  }

  /**
   * Helper to slugify title
   */
  public slugify(text: string): string {
    return text
      .toString()
      .toLowerCase()
      .trim()
      .replace(/\s+/g, '-')
      .replace(/[^\w\-]+/g, '')
      .replace(/\-\-+/g, '-')
      .replace(/^-+/, '')
      .replace(/-+$/, '');
  }

  /**
   * Calculate word count from HTML string
   */
  public calculateWordCount(htmlContent: string): number {
    if (!htmlContent) return 0;
    const text = htmlContent.replace(/<[^>]*>/g, ' ').trim();
    if (!text) return 0;
    return text.split(/\s+/).filter((w) => w.length > 0).length;
  }

  public capitalize(str: string): string {
    if (!str) return '';
    return str.charAt(0).toUpperCase() + str.slice(1);
  }

  /**
   * Convert markdown text to clean HTML
   */
  public convertMarkdownToHtml(markdown: string): string {
    if (!markdown) return '';
    let html = markdown
      .replace(/^### (.*$)/gim, '<h3>$1</h3>')
      .replace(/^## (.*$)/gim, '<h2>$1</h2>')
      .replace(/^# (.*$)/gim, '<h1>$1</h1>')
      .replace(/^\> (.*$)/gim, '<blockquote>$1</blockquote>')
      .replace(/\*\*(.*?)\*\*/gim, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/gim, '<em>$1</em>')
      .replace(/^\- (.*$)/gim, '<li>$1</li>')
      .replace(/^\* (.*$)/gim, '<li>$1</li>')
      .replace(/\n\n+/g, '</p><p>');

    if (!html.startsWith('<h') && !html.startsWith('<p') && !html.startsWith('<blockquote')) {
      html = `<p>${html}</p>`;
    }
    return html;
  }
}
