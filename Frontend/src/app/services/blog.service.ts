import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import { AuthService } from './auth.service';
import { environment } from '../../environments/environment';
import {
  Article,
  ArticleListResponse,
  BlogSettings,
  DashboardStats,
  GenerateBlogRequest,
  ReviewActionRequest,
} from '../models/blog.models';

@Injectable({
  providedIn: 'root',
})
export class BlogService {
  private apiPrefix = environment.apiPrefix;

  constructor(
    private apiService: ApiService,
    private authService: AuthService,
    private http: HttpClient
  ) {}

  /** GET /articles/stats */
  public getStats(companyId?: string): Observable<DashboardStats> {
    const endpoint = companyId ? `companies/${companyId}/articles/stats` : 'articles/stats';
    return this.apiService.get<DashboardStats>(endpoint, { companyScoped: true });
  }

  /** GET /articles */
  public listArticles(
    companyId?: string,
    status?: string,
    search?: string,
    skip: number = 0,
    limit: number = 50
  ): Observable<ArticleListResponse> {
    const endpoint = companyId ? `companies/${companyId}/articles` : 'articles';
    let params: Record<string, any> = { skip, limit };
    if (status && status !== 'all') {
      params['status'] = status;
    }
    if (search) {
      params['search'] = search;
    }
    return this.apiService.get<ArticleListResponse>(endpoint, { params, companyScoped: true });
  }

  /** GET /articles/{id} */
  public getArticle(articleId: string, companyId?: string): Observable<Article> {
    const endpoint = companyId ? `companies/${companyId}/articles/${articleId}` : `articles/${articleId}`;
    return this.apiService.get<Article>(endpoint, { companyScoped: true });
  }

  /** POST /articles */
  public createArticle(data: Partial<Article>, companyId?: string): Observable<Article> {
    const endpoint = companyId ? `companies/${companyId}/articles` : 'articles';
    return this.apiService.post<Article>(endpoint, data, { companyScoped: true });
  }

  /** PUT /articles/{id} */
  public updateArticle(articleId: string, data: Partial<Article>, companyId?: string): Observable<Article> {
    const endpoint = companyId ? `companies/${companyId}/articles/${articleId}` : `articles/${articleId}`;
    return this.apiService.put<Article>(endpoint, data, { companyScoped: true });
  }

  /** DELETE /articles/{id} */
  public deleteArticle(articleId: string, companyId?: string): Observable<void> {
    const endpoint = companyId ? `companies/${companyId}/articles/${articleId}` : `articles/${articleId}`;
    return this.apiService.delete<void>(endpoint, { companyScoped: true });
  }

  /** POST /articles/{id}/review */
  public reviewArticle(articleId: string, req: ReviewActionRequest, companyId?: string): Observable<Article> {
    const endpoint = companyId ? `companies/${companyId}/articles/${articleId}/review` : `articles/${articleId}/review`;
    return this.apiService.post<Article>(endpoint, req, { companyScoped: true });
  }

  /** POST /articles/{id}/publish */
  public publishArticle(articleId: string, targetChannels?: string[], companyId?: string): Observable<Article> {
    const endpoint = companyId ? `companies/${companyId}/articles/${articleId}/publish` : `articles/${articleId}/publish`;
    return this.apiService.post<Article>(
      endpoint,
      { target_channels: targetChannels },
      { companyScoped: true }
    );
  }

  /** GET /blog-settings */
  public getBlogSettings(companyId?: string): Observable<BlogSettings> {
    const endpoint = companyId ? `companies/${companyId}/blog-settings` : 'blog-settings';
    return this.apiService.get<BlogSettings>(endpoint, { companyScoped: true });
  }

  /** PUT /blog-settings */
  public updateBlogSettings(settings: Partial<BlogSettings>, companyId?: string): Observable<BlogSettings> {
    const endpoint = companyId ? `companies/${companyId}/blog-settings` : 'blog-settings';
    return this.apiService.put<BlogSettings>(endpoint, settings, { companyScoped: true });
  }

  /** POST /blog/generate */
  public generateBlogPreview(req: GenerateBlogRequest, companyId?: string): Observable<any> {
    const endpoint = companyId ? `companies/${companyId}/blog/generate` : 'blog/generate';
    return this.apiService.post<any>(endpoint, req, { companyScoped: true });
  }

  /** POST /blog/generate-and-save */
  public generateAndSave(req: GenerateBlogRequest, companyId?: string): Observable<Article> {
    const endpoint = companyId ? `companies/${companyId}/blog/generate-and-save` : 'blog/generate-and-save';
    return this.apiService.post<Article>(endpoint, req, { companyScoped: true });
  }

  /** POST /blog/generate-daily */
  public triggerDailyRun(companyId?: string): Observable<Article> {
    const endpoint = companyId ? `companies/${companyId}/blog/generate-daily` : 'blog/generate-daily';
    return this.apiService.post<Article>(endpoint, {}, { companyScoped: true });
  }

  /** GET /articles/review/preview?token=... (Public with Token) */
  public getArticleByToken(token: string): Observable<Article> {
    const params = new HttpParams().set('token', token);
    return this.http.get<Article>(`${this.apiPrefix}/articles/review/preview`, { params });
  }

  /** POST /articles/review/action?token=... (Public with Token) */
  public reviewArticleByToken(token: string, req: ReviewActionRequest): Observable<Article> {
    const params = new HttpParams().set('token', token);
    return this.http.post<Article>(`${this.apiPrefix}/articles/review/action`, req, { params });
  }
}
