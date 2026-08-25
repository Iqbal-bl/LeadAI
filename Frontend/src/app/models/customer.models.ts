export interface CustomerConsent {
  whatsapp_opt_in: boolean;
  sms_opt_in: boolean;
  email_opt_in: boolean;
  voice_opt_in: boolean;
  do_not_disturb: boolean;
}

export interface Customer {
  id: string;
  name: string;
  email_masked: string | null;
  phone_masked: string | null;
  company: string | null;
  stage: 'new' | 'active' | 'churned' | 'vip' | string;
  status: 'active' | 'inactive' | string;
  owner_email: string | null;
  owner_name: string | null;
  tags: string[];
  consent: CustomerConsent;
  lead_id: string | null;
  lead_score: number | null;
  follow_up_due: string | null;
  birthday: string | null;
  anniversary: string | null;
  created_at: string;
  updated_at: string;
}

export interface CustomerRevealResponse {
  phone: string | null;
  email: string | null;
  revealed_at: string;
}

export interface CustomerConvertRequest {
  lead_id: string;
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
