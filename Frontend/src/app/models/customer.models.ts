export interface CustomerConsent {
  whatsapp_opt_in: boolean;
  sms_opt_in: boolean;
  email_opt_in: boolean;
  voice_opt_in: boolean;
  do_not_disturb: boolean;
}

export interface Customer {
  id: string;
  client_id?: string;
  display_name: string;
  name?: string; // backwards-compatible alias
  company_name: string | null;
  company?: string | null; // backwards-compatible alias
  phone_masked: string | null;
  email_masked: string | null;
  linkedin_masked?: string | null;
  /** LinkedIn profile URL — masked in general responses, unmasked on audited reveal. */
  linkedin_profile_url?: string | null;
  stage: 'new' | 'active' | 'churned' | 'vip' | 'opportunity' | string;
  status: 'active' | 'inactive' | string;
  owner_email: string | null;
  owner_name?: string | null;
  product?: string | null;
  value?: number;
  currency?: string;
  source?: string | null;
  tags?: any;
  notes?: string | null;
  opt_in_whatsapp: boolean;
  opt_in_sms: boolean;
  opt_in_email: boolean;
  opt_in_call: boolean;
  do_not_disturb: boolean;
  converted_at?: string | null;
  last_contacted_at?: string | null;
  next_follow_up_at?: string | null;
  birthday?: string | null;
  anniversary?: string | null;
  source_conversation_id?: string | null;
  fields?: Record<string, any> | null;
  lead_id?: string | null;
  lead_score?: number | null;
  follow_up_due?: string | null;
  created_at: string;
  updated_at?: string;
  consent?: CustomerConsent;
}

export interface CustomerRevealResponse {
  id?: string;
  display_name?: string;
  phone: string | null;
  email: string | null;
  whatsapp?: string | null;
  linkedin?: string | null;
  linkedin_profile_url?: string | null;
  social_identities?: Array<{
    channel: string;
    external_user_id?: string;
    profile_name?: string;
    handle?: string;
    profile_url?: string;
  }>;
  revealed_at?: string;
}

export interface CustomerConvertRequest {
  conversation_id: string;
  lead_id?: string;
  owner_email?: string | null;
  stage?: string;
  value?: number | null;
  notes?: string | null;
}

export interface CustomerMessageRequest {
  channel: 'whatsapp' | 'sms' | 'email' | 'voice';
  message: string;
  template_id?: string;
}

export interface CustomerGreeting {
  customer_id: string;
  customer_name: string;
  event_type: 'birthday' | 'anniversary';
  event_date: string;
  days_until: number;
}
