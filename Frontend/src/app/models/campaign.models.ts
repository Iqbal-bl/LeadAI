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
  client_id?: string;
  name: string;
  description?: string | null;
  kind: CampaignKind | string;
  channel: CampaignChannel | string;
  channel_account_id?: string | null;
  purpose: CampaignPurpose | string;
  status: CampaignStatus | string;
  status_message?: string | null;
  audience_type: AudienceType | string;
  audience_id?: string;
  list_id?: string | null;
  audience_filter?: any;
  template_name?: string | null;
  template_language?: string | null;
  template_id?: string;
  message_body?: string | null;
  body?: string;
  script_id?: string | null;
  language?: string | null;
  scheduled_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  concurrency?: number;
  rate_per_minute?: number;
  rate_limit?: number;
  respect_opt_out?: boolean;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
  timezone?: string | null;
  total_count?: number;
  queued_count?: number;
  sent_count?: number;
  delivered_count?: number;
  read_count?: number;
  replied_count?: number;
  failed_count?: number;
  skipped_count?: number;
  leads_created?: number;
  counters?: CampaignCounters;
  created_at: string;
  created_by?: string | null;
  updated_at?: string;
}

export interface CampaignCounters {
  total: number;
  sent: number;
  delivered: number;
  failed: number;
  replied: number;
  queued?: number;
  read?: number;
  skipped?: number;
  leads_created?: number;
}

export interface CampaignCreateRequest {
  name: string;
  kind: CampaignKind;
  channel: CampaignChannel;
  channel_account_id?: string;
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
