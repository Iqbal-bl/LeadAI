import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import {
  KbDocument,
  KbDocumentChunks,
  KbStats,
  KbTestResult,
} from '../models/kb.models';

@Injectable({
  providedIn: 'root',
})
export class KbService {
  constructor(private apiService: ApiService) {}

  // GET /knowledge/documents
  public getDocuments(status?: string): Observable<KbDocument[]> {
    const params: Record<string, string> = {};
    if (status) {
      params['status'] = status;
    }
    return this.apiService.get<KbDocument[]>('knowledge/documents', {
      params,
      companyScoped: true,
    });
  }

  // POST /knowledge/documents (multipart/form-data)
  public uploadDocument(file: File, tags?: string): Observable<KbDocument> {
    const formData = new FormData();
    formData.append('file', file);

    const params: Record<string, string> = {};
    if (tags) {
      params['tags'] = tags;
    }

    return this.apiService.post<KbDocument>('knowledge/documents', formData, {
      params,
      companyScoped: true,
    });
  }

  // POST /knowledge/faq
  public createFAQ(payload: {
    title: string;
    content: string;
    tags?: string;
  }): Observable<KbDocument> {
    return this.apiService.post<KbDocument>('knowledge/faq', payload, {
      companyScoped: true,
    });
  }

  // POST /knowledge/text
  public createText(payload: {
    title: string;
    content: string;
    tags?: string;
  }): Observable<KbDocument> {
    return this.apiService.post<KbDocument>('knowledge/text', payload, {
      companyScoped: true,
    });
  }

  // GET /knowledge/documents/{id}/chunks
  public getDocumentChunks(
    id: string,
    limit?: number,
  ): Observable<KbDocumentChunks> {
    const params: Record<string, number> = {};
    if (limit) {
      params['limit'] = limit;
    }
    return this.apiService.get<KbDocumentChunks>(
      `knowledge/documents/${id}/chunks`,
      {
        params,
        companyScoped: true,
      },
    );
  }

  // POST /knowledge/documents/{id}/reindex
  public reindexDocument(id: string): Observable<void> {
    return this.apiService.post<void>(
      `knowledge/documents/${id}/reindex`,
      null,
      {
        companyScoped: true,
      },
    );
  }

  // DELETE /knowledge/documents/{id}
  public deleteDocument(id: string): Observable<void> {
    return this.apiService.delete<void>(`knowledge/documents/${id}`, {
      companyScoped: true,
    });
  }

  // POST /knowledge/test
  public testQuery(query: string, topK: number = 5): Observable<KbTestResult> {
    return this.apiService.post<KbTestResult>(
      'knowledge/test',
      { query, top_k: topK },
      {
        companyScoped: true,
      },
    );
  }

  // GET /knowledge/stats
  public getStats(): Observable<KbStats> {
    return this.apiService.get<KbStats>('knowledge/stats', {
      companyScoped: true,
    });
  }
}
