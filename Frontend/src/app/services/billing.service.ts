import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import {
  BillingSummary,
  ClientRecharge,
  PlanTemplateCreatePayload,
  RechargeAllocatePayload,
  RechargePlanTemplate,
  UsageLog,
} from '../models/billing.models';

@Injectable({
  providedIn: 'root',
})
export class BillingService {
  constructor(private apiService: ApiService) {}

  /** Tenant: Get current active plan & balance summary */
  public getCurrentPlan(): Observable<BillingSummary> {
    return this.apiService.get<BillingSummary>('billing/current-plan', {
      companyScoped: true,
    });
  }

  /** Tenant: Get available standard & custom plans for this company */
  public getAvailablePlans(): Observable<RechargePlanTemplate[]> {
    return this.apiService.get<RechargePlanTemplate[]>('billing/available-plans', {
      companyScoped: true,
    });
  }

  /** Tenant: Purchase/apply a recharge */
  public recharge(payload: RechargeAllocatePayload): Observable<ClientRecharge> {
    return this.apiService.post<ClientRecharge>('billing/recharge', payload, {
      companyScoped: true,
    });
  }

  /** Tenant: Get call minute usage history */
  public getUsageHistory(limit: number = 50): Observable<UsageLog[]> {
    return this.apiService.get<UsageLog[]>('billing/usage-history', {
      params: { limit },
      companyScoped: true,
    });
  }

  /** Admin: List all master plan templates */
  public getAdminPlans(): Observable<RechargePlanTemplate[]> {
    return this.apiService.get<RechargePlanTemplate[]>('admin/billing/plans');
  }

  /** Admin: Create master standard or custom plan */
  public createAdminPlan(payload: PlanTemplateCreatePayload): Observable<RechargePlanTemplate> {
    return this.apiService.post<RechargePlanTemplate>('admin/billing/plans', payload);
  }

  /** Admin: Update master plan template */
  public updateAdminPlan(planId: string, payload: Partial<PlanTemplateCreatePayload>): Observable<RechargePlanTemplate> {
    return this.apiService.put<RechargePlanTemplate>(`admin/billing/plans/${planId}`, payload);
  }

  /** Admin: Direct recharge grant to a client account */
  public adminRechargeClient(payload: RechargeAllocatePayload): Observable<ClientRecharge> {
    return this.apiService.post<ClientRecharge>('admin/billing/recharge-client', payload);
  }

  /** Admin: System-wide billing summary for all clients */
  public getAdminClientsSummary(): Observable<BillingSummary[]> {
    return this.apiService.get<BillingSummary[]>('admin/billing/clients-summary');
  }
}
