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
  product_id?: string | null;
  product_name?: string | null;
  campaign_type?: 'broadcast' | 'lead_campaign' | 'lead' | string | null;
  created_via?: 'manual' | 'import' | string;
  call_escalation_enabled?: boolean;
  output_file_id?: string | null;
  output_generated_at?: string | null;
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

export interface CampaignUpdateRequest {
  name?: string;
  description?: string | null;
  message_body?: string | null;
  template_name?: string | null;
  template_language?: string | null;
  template_params?: string[] | null;
  script_id?: string | null;
  scheduled_at?: string | null;
  concurrency?: number | null;
  rate_per_minute?: number | null;
  respect_opt_out?: boolean | null;
  quiet_hours_start?: number | null;
  quiet_hours_end?: number | null;
  timezone?: string | null;
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
  phone?: string | null;
  email?: string | null;
  phone_masked?: string | null;
  email_masked?: string | null;
  name: string | null;
  status: 'pending' | 'sent' | 'delivered' | 'failed' | 'replied';
  failure_reason?: string;
  sent_at?: string;
  call_consent_status?: 'asked' | 'accepted' | 'declined' | 'not_yet_asked' | string | null;
  CallConsentStatus?: string | null;
  conversation_id?: string | null;
  attempts?: number;
  product?: string | null;
}

export interface CampaignBatchMeta {
  sent?: number;
  failed?: number;
  skipped?: number;
  remaining?: number;
  resume_at?: string;
  reason?: string;
  added?: number;
  audience?: string;
  [key: string]: any;
}

export interface CampaignHistoryItem {
  id: string;
  action: string;
  message: string;
  meta?: CampaignBatchMeta | null;
  created_at: string;
  actor_email?: string | null;
  log_type?: string | null;
}

export interface CampaignHistoryResponse {
  total_items: number;
  page: number;
  page_size: number;
  items: CampaignHistoryItem[];
}

export type RestartMode = 'all' | 'failed_only' | 'pending_only';
export type ExecutionStatus = 'running' | 'completed' | 'stopped';

export interface CampaignExecution {
  id: string;
  campaign_id: string;
  status: ExecutionStatus | string;
  restart_mode: RestartMode | string;
  total_count: number;
  completed_count: number;
  failed_count: number;
  skipped_count: number;
  started_at: string;
  completed_at?: string | null;
}

export interface CampaignExecutionListResponse {
  total_items: number;
  page: number;
  page_size: number;
  items: CampaignExecution[];
}

export interface CampaignExecutionAttempt {
  id: string;
  recipient_id: string;
  name: string;
  phone_masked?: string | null;
  email_masked?: string | null;
  status: string;
  external_message_id?: string | null;
  sent_at?: string | null;
  delivered_at?: string | null;
  read_at?: string | null;
  replied_at?: string | null;
  failure_reason?: string | null;
  lead_score?: number | null;
  lead_status?: string | null;
  call_status?: string | null;
  call_duration_sec?: number | null;
}

export interface CampaignExecutionAttemptsResponse {
  total_items: number;
  page: number;
  page_size: number;
  items: CampaignExecutionAttempt[];
}
