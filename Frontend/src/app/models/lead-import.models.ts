export interface LeadImportSchemaField {
  key: string;
  label: string;
  data_type: 'text' | 'email' | 'number' | string;
  required: boolean;
  source: 'fixed' | 'data_point' | string;
}

export interface LeadImportSchemaResponse {
  fields: LeadImportSchemaField[];
  sample_csv_header: string;
}

export interface LeadImportInvalidRow {
  row_number: number;
  reason: string;
}

export interface LeadImportBatch {
  campaign_id: string;
  name: string;
  product: string;
  lead_count: number;
}

export interface LeadImportResponse {
  total: number;
  valid: number;
  invalid: number;
  duplicates: number;
  invalid_rows?: LeadImportInvalidRow[];
  batches: LeadImportBatch[];
}

export interface LeadImportFormState {
  file: File | null;
  channel: 'chat' | 'call' | 'both';
  chat_channel: 'whatsapp' | 'instagram' | 'messenger';
  chat_channel_account_id: string;
  instagram_account_id?: string;
  facebook_account_id?: string;
  voice_script_id?: string;
  call_escalation: boolean;
}
