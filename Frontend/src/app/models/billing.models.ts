export interface RechargePlanTemplate {
  id: string;
  name: string;
  plan_type: 'standard' | 'custom' | 'topup';
  plan_category?: string;
  feature_key?: string | null;
  target_client_id?: string | null;
  target_client_ids?: string[] | null;
  addon_channels?: string[] | null;
  included_minutes: number;
  validity_days: number;
  price: number;
  rate_per_minute: number;
  razorpay_plan_id?: string | null;
  auto_pay_by_default?: boolean;
  is_active: boolean;
  description?: string | null;
  created_at?: string;
  tier_label?: string | null;
  is_popular?: boolean;
  features?: string[];
  tax_rate_percent?: number;
  tax_inclusive?: boolean;
  base_price_excl_tax?: number;
  tax_amount?: number;
  yearly_discount_percent?: number;
}

export interface ClientRecharge {
  id: string;
  client_id: string;
  plan_template_id?: string | null;
  plan_name_snapshot: string;
  purchased_minutes: number;
  remaining_minutes: number;
  booster_minutes?: number;
  rollover_minutes_carried?: number;
  total_allocated_minutes?: number;
  quota_percentage?: number;
  validity_days_snapshot: number;
  price_paid: number;
  recharged_at?: string | null;
  expires_at?: string | null;
  status:
    | 'active'
    | 'pending'
    | 'exhausted'
    | 'expired'
    | 'superseded'
    | 'failed'
    | 'cancelled';
  payment_reference?: string | null;
  razorpay_order_id?: string | null;
  razorpay_subscription_id?: string | null;
  is_auto_renew?: boolean;
  cancel_at_period_end?: boolean;
  active_channels?: string[] | null;
  next_cycle_channels?: string[] | null;
  invoice_url?: string | null;
  invoice_id?: string | null;
  failure_reason?: string | null;
  created_at?: string;
}

export interface ChannelAddonQuote {
  channel: string;
  channel_name: string;
  monthly_price: number;
  total_cycle_days: number;
  remaining_days: number;
  prorated_price: number;
  active_plan_expires_at?: string | null;
  next_cycle_bundle_price: number;
  auto_pay_synced: boolean;
}

export interface ChannelAddonOrderResponse {
  order_id: string;
  amount: number;
  currency: string;
  key_id: string;
  channel: string;
  prorated_price: number;
  remaining_days: number;
  next_cycle_bundle_price: number;
}

export interface ChannelAddonVerifyPayload {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
  channel: string;
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

export interface RazorpaySubscriptionResponse {
  subscription_id: string;
  plan_id: string;
  key_id: string;
  amount: number;
  currency: string;
  plan_name: string;
  included_minutes: number;
}

export interface RazorpaySubscriptionVerifyPayload {
  razorpay_subscription_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
  plan_template_id: string;
}

export interface RazorpayPaymentFailurePayload {
  razorpay_order_id?: string;
  order_id?: string;
  razorpay_subscription_id?: string;
  subscription_id?: string;
  razorpay_payment_id?: string;
  payment_id?: string;
  error_code?: string;
  error_description?: string;
}

export interface SubscriptionCancelResponse {
  subscription_id: string;
  status: string;
  cancel_at_cycle_end: boolean;
  expires_at?: string | null;
  message: string;
}

export interface CustomBundlePayload {
  include_voice: boolean;
  voice_minutes: number;
  channels: string[];
  billing_cycle: 'monthly' | 'yearly';
  base_plan_template_id?: string | null;
}

export interface UsageLog {
  id: string;
  client_id: string;
  recharge_id: string;
  call_sid: string;
  activity_type?: string | null;
  activity_label?: string | null;
  conversation_id?: string | null;
  call_duration_seconds: number;
  minutes_deducted: number;
  previous_balance: number;
  new_balance: number;
  recording_url?: string | null;
  deducted_at?: string;
}

export interface BillingSummary {
  client_id: string;
  active_recharge?: ClientRecharge | null;
  pending_recharges: ClientRecharge[];
  total_remaining_minutes: number;
  total_allocated_minutes?: number;
  quota_percentage?: number;
  is_quota_active: boolean;
}

export interface RechargeAllocatePayload {
  client_id?: string;
  client_ids?: string[];
  plan_template_id?: string;
  custom_minutes?: number;
  custom_validity_days?: number;
  custom_price?: number;
  custom_name?: string;
  payment_reference?: string;
}

export interface PlanTemplateCreatePayload {
  name: string;
  plan_type?: 'standard' | 'custom' | 'topup';
  plan_category?: string;
  feature_key?: string | null;
  target_client_id?: string | null;
  target_client_ids?: string[] | null;
  addon_channels?: string[] | null;
  included_minutes: number;
  validity_days: number;
  price: number;
  rate_per_minute?: number;
  auto_pay_by_default?: boolean;
  description?: string;
}

export interface ChannelActionResponse {
  ok?: boolean;
  channel?: string;
  status?: string;
  active_until?: string | null;
  active_channels: string[];
  next_cycle_channels: string[];
  next_cycle_bundle_price?: number;
  message: string;
}

/**
 * Model representing an item selected for addon purchase or topup.
 */
export interface AddonItemSelection {
  id: string;
  type: 'topup' | 'channel';
  key: string;
  name: string;
  price: number;
  regularPrice?: number;
  minutes?: number;
  isProrated?: boolean;
  remainingDays?: number;
}

/**
 * Configuration item for call minute booster top-ups.
 */
export interface BoosterOption {
  id: string;
  name: string;
  minutes: number;
  price: number;
  ratePerMinute: number;
  badge?: string;
  description: string;
}

/**
 * Channel item in the Plans & Pricing selection flow.
 */
export interface PricingChannelOption {
  key: string;
  name: string;
  icon: string;
  color: string;
  monthlyPrice: number;
  durationDays: number;
  description: string;
  features: string[];
}

/**
 * Encapsulated state of a user's selection in the pricing funnel.
 */
export interface PricingSelectionState {
  selectionType: 'standard' | 'custom';
  standardPlan?: RechargePlanTemplate | null;
  customMinutes: number;
  customPrice: number;
  selectedChannels: string[];
  totalMonthlyPrice: number;
}
