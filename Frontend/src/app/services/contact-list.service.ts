import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import {
  ContactList,
  ContactListPreview,
  ContactListCreateRequest,
  ContactListFromLeadsRequest,
} from '../models/contact-list.models';

@Injectable({
  providedIn: 'root',
})
export class ContactListService {
  constructor(private apiService: ApiService) {}

  /** GET /lists — list all contact lists */
  public getLists(): Observable<ContactList[]> {
    return this.apiService.get<ContactList[]>('lists', {
      companyScoped: true,
    });
  }

  /** POST /lists/preview — parse file without saving, returns preview data */
  public previewList(file: File): Observable<ContactListPreview> {
    const formData = new FormData();
    formData.append('file', file);
    return this.apiService.post<ContactListPreview>('lists/preview', formData, {
      companyScoped: true,
    });
  }

  /** POST /lists — save the list with confirmed column mapping */
  public createList(name: string, file: File, columnMap: Record<string, string>): Observable<ContactList> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('name', name);
    formData.append('column_map', JSON.stringify(columnMap));
    return this.apiService.post<ContactList>('lists', formData, {
      companyScoped: true,
    });
  }

  /** POST /lists/from-leads — build a list from existing leads by filter */
  public createFromLeads(req: ContactListFromLeadsRequest): Observable<ContactList> {
    return this.apiService.post<ContactList>('lists/from-leads', req, {
      companyScoped: true,
    });
  }

  /** DELETE /lists/{id} — returns 409 if a campaign uses the list */
  public deleteList(id: string): Observable<any> {
    return this.apiService.delete<any>(`lists/${id}`, {
      companyScoped: true,
    });
  }

  /** GET /lists/{list_id}/items — paginated contact list rows */
  public getListItems(
    listId: string,
    params?: {
      page?: number;
      page_size?: number;
      only_invalid?: boolean;
    }
  ): Observable<{
    total_items: number;
    page: number;
    page_size: number;
    items: any[];
  }> {
    const queryParams: Record<string, string | number | boolean> = {};
    if (params?.page) queryParams['page'] = params.page;
    if (params?.page_size) queryParams['page_size'] = params.page_size;
    if (params?.only_invalid !== undefined) queryParams['only_invalid'] = params.only_invalid;

    return this.apiService.get<any>(`lists/${listId}/items`, {
      params: queryParams,
      companyScoped: true,
    });
  }
}
