export type CampaignKind = 'message' | 'call';

export type CampaignChannel =
  | 'whatsapp'
  | 'messenger'
  | 'instagram'
  | 'sms'
  | 'email'
  | 'voice';

export type CampaignPurpose =
  | 'promotional'
  | 'festive'
  | 'cold_outreach'
  | 'follow_up'
  | 'reactivation'
  | 'transactional';

export type CampaignStatus =
  | 'draft'
  | 'building'
  | 'ready'
  | 'running'
  | 'paused'
  | 'cancelled'
  | 'completed'
  | 'failed';

export type AudienceType = 'list' | 'leads' | 'customers';

export interface Campaign {
  id: string;
  name: string;
  kind: CampaignKind;
  channel: CampaignChannel;
  purpose: CampaignPurpose;
  status: CampaignStatus;
  audience_type: AudienceType;
  audience_id?: string;
  body: string;
  template_id?: string;
  scheduled_at?: string;
  concurrency?: number;
  rate_limit?: number;
  counters: CampaignCounters;
  created_at: string;
  updated_at: string;
}

export interface CampaignCounters {
  total: number;
  sent: number;
  delivered: number;
  failed: number;
  replied: number;
}

export interface CampaignCreateRequest {
  name: string;
  kind: CampaignKind;
  channel: CampaignChannel;
  purpose: CampaignPurpose;
  audience_type: AudienceType;
  list_id?: string;
  audience_filters?: Record<string, any>;
  message_body: string;
  template_id?: string;
  scheduled_at?: string;
  concurrency?: number;
  rate_limit?: number;
}

export interface CampaignPreview {
  recipient_count: number;
  sample_messages: { recipient: string; rendered_body: string }[];
  eta_minutes: number;
  warnings: CampaignWarning[];
}

export interface CampaignWarning {
  code: string;
  message: string;
  severity: 'info' | 'warn' | 'error';
}

export interface CampaignRecipient {
  id: string;
  identifier_masked: string;
  name: string | null;
  status: 'pending' | 'sent' | 'delivered' | 'failed' | 'replied';
  failure_reason?: string;
  sent_at?: string;
}
