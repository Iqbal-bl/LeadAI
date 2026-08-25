import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import { Script, ScriptPreview, PromptTemplate } from '../models/script.models';

@Injectable({
  providedIn: 'root',
})
export class ScriptService {
  constructor(private apiService: ApiService) {}

  // GET /scripts
  public getScripts(
    channel?: 'all' | 'chat' | 'voice',
    includeInactive: boolean = false,
  ): Observable<Script[]> {
    const params: Record<string, string | boolean> = {
      include_inactive: includeInactive,
    };
    if (channel) {
      params['channel'] = channel;
    }
    return this.apiService.get<Script[]>('scripts', {
      params,
      companyScoped: true,
    });
  }

  // POST /scripts
  public createScript(payload: Script): Observable<Script> {
    return this.apiService.post<Script>('scripts', payload, {
      companyScoped: true,
    });
  }

  // GET /scripts/active
  public getActiveScript(channel: 'chat' | 'voice'): Observable<Script> {
    return this.apiService.get<Script>('scripts/active', {
      params: { channel },
      companyScoped: true,
    });
  }

  // GET /scripts/importable
  public getImportableScripts(): Observable<{ filename: string }[]> {
    return this.apiService.get<{ filename: string }[]>('scripts/importable', {
      companyScoped: true,
    });
  }

  // POST /scripts/import
  public importScript(filename: string): Observable<Script> {
    return this.apiService.post<Script>(
      'scripts/import',
      { filename },
      {
        companyScoped: true,
      },
    );
  }

  // GET /scripts/{id}
  public getScript(
    id: string,
  ): Observable<
    Script & { rendered_prompt?: string; parsed_sections?: any[] }
  > {
    return this.apiService.get<
      Script & { rendered_prompt?: string; parsed_sections?: any[] }
    >(`scripts/${id}`, {
      companyScoped: true,
    });
  }

  // PATCH /scripts/{id}
  public updateScript(
    id: string,
    payload: Partial<Script>,
  ): Observable<Script> {
    return this.apiService.patch<Script>(`scripts/${id}`, payload, {
      companyScoped: true,
    });
  }

  // DELETE /scripts/{id}
  public deleteScript(id: string): Observable<void> {
    return this.apiService.delete<void>(`scripts/${id}`, {
      companyScoped: true,
    });
  }

  // POST /scripts/{id}/set-default
  public setDefaultScript(id: string): Observable<void> {
    return this.apiService.post<void>(`scripts/${id}/set-default`, null, {
      companyScoped: true,
    });
  }

  // POST /scripts/{id}/preview
  public previewScript(
    id: string,
    channel: 'chat' | 'voice',
  ): Observable<ScriptPreview> {
    return this.apiService.post<ScriptPreview>(`scripts/${id}/preview`, null, {
      params: { channel },
      companyScoped: true,
    });
  }

  // GET /prompts
  public getPrompts(): Observable<PromptTemplate[]> {
    return this.apiService.get<PromptTemplate[]>('prompts', {
      companyScoped: true,
    });
  }

  // PUT /prompts/{key}
  public updatePrompt(key: string, value: string): Observable<PromptTemplate> {
    return this.apiService.put<PromptTemplate>(
      `prompts/${key}`,
      { value },
      {
        companyScoped: true,
      },
    );
  }

  // POST /prompts/{key}/reset
  public resetPrompt(key: string): Observable<PromptTemplate> {
    return this.apiService.post<PromptTemplate>(`prompts/${key}/reset`, null, {
      companyScoped: true,
    });
  }
}
