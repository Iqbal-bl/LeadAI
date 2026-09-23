import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import {
  BillingSummary,
  ChannelAddonOrderResponse,
  ChannelAddonQuote,
  ChannelAddonVerifyPayload,
  ClientRecharge,
  CustomBundlePayload,
  PlanTemplateCreatePayload,
  RazorpayOrderResponse,
  RazorpayPaymentFailurePayload,
  RazorpayPaymentVerifyPayload,
  RazorpaySubscriptionResponse,
  RazorpaySubscriptionVerifyPayload,
  RechargeAllocatePayload,
  RechargePlanTemplate,
  SubscriptionCancelResponse,
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

  /** Tenant: Create Razorpay Order */
  public createRazorpayOrder(planTemplateId: string): Observable<RazorpayOrderResponse> {
    return this.apiService.post<RazorpayOrderResponse>(
      'billing/create-order',
      { plan_template_id: planTemplateId },
      { companyScoped: true }
    );
  }

  /** Tenant: Verify Razorpay Payment Signature */
  public verifyRazorpayPayment(payload: RazorpayPaymentVerifyPayload): Observable<ClientRecharge> {
    return this.apiService.post<ClientRecharge>(
      'billing/verify-payment',
      payload,
      { companyScoped: true }
    );
  }

  /** Tenant: Create Razorpay Recurring Subscription */
  public createRazorpaySubscription(planTemplateId: string): Observable<RazorpaySubscriptionResponse> {
    return this.apiService.post<RazorpaySubscriptionResponse>(
      'billing/create-subscription',
      { plan_template_id: planTemplateId },
      { companyScoped: true }
    );
  }

  /** Tenant: Verify Razorpay Subscription Payment Signature */
  public verifyRazorpaySubscription(payload: RazorpaySubscriptionVerifyPayload): Observable<ClientRecharge> {
    return this.apiService.post<ClientRecharge>(
      'billing/verify-subscription',
      payload,
      { companyScoped: true }
    );
  }

  /** Tenant: Cancel AutoPay mandate at cycle end */
  public cancelSubscription(): Observable<SubscriptionCancelResponse> {
    return this.apiService.post<SubscriptionCancelResponse>(
      'billing/cancel-subscription',
      {},
      { companyScoped: true }
    );
  }

  /** Tenant: Cancel a specific channel add-on from renewing next cycle */
  public cancelChannel(channel: string): Observable<{ ok: boolean; message: string; active_channels: string[]; next_cycle_channels: string[] }> {
    return this.apiService.post<{ ok: boolean; message: string; active_channels: string[]; next_cycle_channels: string[] }>(
      'billing/cancel-channel',
      { channel },
      { companyScoped: true }
    );
  }

  /** Tenant: Create custom bundle recurring subscription */
  public createCustomBundle(payload: CustomBundlePayload): Observable<RazorpaySubscriptionResponse> {
    return this.apiService.post<RazorpaySubscriptionResponse>(
      'billing/custom-bundle/create-subscription',
      payload,
      { companyScoped: true }
    );
  }

  /** Tenant: Get mid-cycle prorated quote for adding a channel to active plan */
  public getChannelAddonQuote(channel: string): Observable<ChannelAddonQuote> {
    return this.apiService.get<ChannelAddonQuote>('billing/channel-addon/quote', {
      params: { channel },
      companyScoped: true,
    });
  }

  /** Tenant: Create Razorpay order for mid-cycle prorated channel add-on */
  public createChannelAddonOrder(channel: string): Observable<ChannelAddonOrderResponse> {
    return this.apiService.post<ChannelAddonOrderResponse>(
      'billing/channel-addon/create-order',
      { channel },
      { companyScoped: true }
    );
  }

  /** Tenant: Verify payment and immediately activate channel add-on on active plan */
  public verifyChannelAddonPayment(payload: ChannelAddonVerifyPayload): Observable<ClientRecharge> {
    return this.apiService.post<ClientRecharge>(
      'billing/channel-addon/verify-payment',
      payload,
      { companyScoped: true }
    );
  }

  /** Tenant: Report Razorpay Payment Failure or Dismissal */
  public recordPaymentFailure(payload: RazorpayPaymentFailurePayload): Observable<any> {
    return this.apiService.post<any>(
      'billing/record-failure',
      payload,
      { companyScoped: true }
    );
  }

  /** Tenant: Get comprehensive payment/recharge history */
  public getPaymentHistory(limit: number = 100): Observable<ClientRecharge[]> {
    return this.apiService.get<ClientRecharge[]>('billing/payment-history', {
      params: { limit },
      companyScoped: true,
    });
  }

  /** Tenant: Purchase/apply a direct recharge (Super Admin / complimentary) */
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

  /** Admin: Soft-delete / retire master plan template */
  public deleteAdminPlan(planId: string): Observable<{ ok: boolean; message: string }> {
    return this.apiService.delete<{ ok: boolean; message: string }>(`admin/billing/plans/${planId}`);
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
