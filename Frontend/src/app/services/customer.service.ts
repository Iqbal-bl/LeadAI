import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';
import { ApiService } from './api.service';
import {
  Customer,
  CustomerRevealResponse,
  CustomerConvertRequest,
  CustomerMessageRequest,
  CustomerGreeting,
} from '../models/customer.models';

export interface CustomerQueryParams {
  stage?: string;
  status?: string;
  owner?: string;
  tag?: string;
  search?: string;
  follow_up_due?: boolean;
  page?: number;
  page_size?: number;
}

@Injectable({
  providedIn: 'root',
})
export class CustomerService {
  constructor(private apiService: ApiService) {}

  private mapCustomer(data: any): Customer {
    if (!data) return data;
    const displayName = data.display_name || data.name || '';
    const companyName = data.company_name !== undefined ? data.company_name : (data.company !== undefined ? data.company : null);
    const optInWhatsapp = data.opt_in_whatsapp !== undefined ? Boolean(data.opt_in_whatsapp) : Boolean(data.consent?.whatsapp_opt_in);
    const optInSms = data.opt_in_sms !== undefined ? Boolean(data.opt_in_sms) : Boolean(data.consent?.sms_opt_in);
    const optInEmail = data.opt_in_email !== undefined ? Boolean(data.opt_in_email) : Boolean(data.consent?.email_opt_in);
    const optInCall = data.opt_in_call !== undefined ? Boolean(data.opt_in_call) : Boolean(data.consent?.voice_opt_in);
    const dnd = data.do_not_disturb !== undefined ? Boolean(data.do_not_disturb) : Boolean(data.consent?.do_not_disturb);

    return {
      ...data,
      display_name: displayName,
      name: displayName,
      company_name: companyName,
      company: companyName,
      opt_in_whatsapp: optInWhatsapp,
      opt_in_sms: optInSms,
      opt_in_email: optInEmail,
      opt_in_call: optInCall,
      do_not_disturb: dnd,
      value: data.value !== undefined && data.value !== null ? Number(data.value) : 0,
      currency: data.currency || 'INR',
      consent: {
        whatsapp_opt_in: optInWhatsapp,
        sms_opt_in: optInSms,
        email_opt_in: optInEmail,
        voice_opt_in: optInCall,
        do_not_disturb: dnd,
      },
    };
  }

  /** GET /customers — filtered list (server-side ownership filtering for employees) */
  public getCustomers(params?: CustomerQueryParams): Observable<{ items: Customer[]; total_items: number }> {
    return this.apiService.get<{ items: any[]; total_items: number }>('customers', {
      params: params as any,
      companyScoped: true,
    }).pipe(
      map((res) => ({
        items: (res?.items || []).map((item) => this.mapCustomer(item)),
        total_items: res?.total_items || 0,
      }))
    );
  }

  /** GET /customers/{id} — customer detail */
  public getCustomer(id: string): Observable<Customer> {
    return this.apiService.get<any>(`customers/${id}`, {
      companyScoped: true,
    }).pipe(
      map((data) => this.mapCustomer(data))
    );
  }

  /** PATCH /customers/{id} — update customer fields */
  public updateCustomer(id: string, data: Partial<Customer>): Observable<Customer> {
    return this.apiService.patch<any>(`customers/${id}`, data, {
      companyScoped: true,
    }).pipe(
      map((res) => this.mapCustomer(res))
    );
  }

  /** POST /customers/convert — promote a lead to a customer (idempotent) */
  public convertLead(req: CustomerConvertRequest): Observable<Customer> {
    return this.apiService.post<any>('customers/convert', req, {
      companyScoped: true,
    }).pipe(
      map((res) => this.mapCustomer(res))
    );
  }

  /** POST /customers/{id}/message — send one-off outreach (consent-checked) */
  public sendMessage(id: string, req: CustomerMessageRequest): Observable<any> {
    return this.apiService.post<any>(`customers/${id}/message`, req, {
      companyScoped: true,
    });
  }

  /** POST /customers/{id}/reveal — click-to-reveal real phone/email (audit-logged) */
  public revealContact(id: string): Observable<CustomerRevealResponse> {
    return this.apiService.post<CustomerRevealResponse>(`customers/${id}/reveal`, null, {
      companyScoped: true,
    });
  }

  /** GET /customers/greetings/upcoming — birthdays/anniversaries in next N days */
  public getUpcomingGreetings(days: number = 7): Observable<CustomerGreeting[]> {
    return this.apiService.get<CustomerGreeting[]>('customers/greetings/upcoming', {
      params: { days },
      companyScoped: true,
    });
  }

  /** POST /customers — create a customer manually */
  public createCustomer(customer: any): Observable<Customer> {
    return this.apiService.post<any>('customers', customer, {
      companyScoped: true,
    }).pipe(
      map((res) => this.mapCustomer(res))
    );
  }
}
