export interface RechargePlanTemplate {
  id: string;
  name: string;
  plan_type: 'standard' | 'custom';
  target_client_id?: string | null;
  included_minutes: number;
  validity_days: number;
  price: number;
  rate_per_minute: number;
  is_active: boolean;
  description?: string | null;
  created_at?: string;
}

export interface ClientRecharge {
  id: string;
  client_id: string;
  plan_template_id?: string | null;
  plan_name_snapshot: string;
  purchased_minutes: number;
  remaining_minutes: number;
  validity_days_snapshot: number;
  price_paid: number;
  recharged_at?: string | null;
  expires_at?: string | null;
  status: 'active' | 'pending' | 'exhausted' | 'expired' | 'superseded' | 'failed' | 'cancelled';
  payment_reference?: string | null;
  razorpay_order_id?: string | null;
  invoice_url?: string | null;
  invoice_id?: string | null;
  failure_reason?: string | null;
  created_at?: string;
}

export interface RazorpayOrderResponse {
  order_id: string;
  amount: number;
  currency: string;
  key_id: string;
  plan_id: string;
  plan_name: string;
  included_minutes: number;
}

export interface RazorpayPaymentVerifyPayload {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
  plan_template_id: string;
}

export interface RazorpayPaymentFailurePayload {
  razorpay_order_id: string;
  error_code?: string;
  error_description?: string;
}

export interface UsageLog {
  id: string;
  client_id: string;
  recharge_id: string;
  call_sid: string;
  conversation_id?: string | null;
  call_duration_seconds: number;
  minutes_deducted: number;
  previous_balance: number;
  new_balance: number;
  deducted_at?: string;
}

export interface BillingSummary {
  client_id: string;
  active_recharge?: ClientRecharge | null;
  pending_recharges: ClientRecharge[];
  total_remaining_minutes: number;
  is_quota_active: boolean;
}

export interface RechargeAllocatePayload {
  client_id?: string;
  plan_template_id?: string;
  custom_minutes?: number;
  custom_validity_days?: number;
  custom_price?: number;
  custom_name?: string;
  payment_reference?: string;
}

export interface PlanTemplateCreatePayload {
  name: string;
  plan_type?: 'standard' | 'custom';
  target_client_id?: string | null;
  included_minutes: number;
  validity_days: number;
  price: number;
  rate_per_minute?: number;
  description?: string;
}
