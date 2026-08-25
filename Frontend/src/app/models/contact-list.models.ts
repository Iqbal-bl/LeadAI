export interface ContactList {
  id: string;
  name: string;
  total_rows: number;
  valid_count: number;
  invalid_count: number;
  duplicate_count: number;
  columns: string[];
  created_at: string;
  updated_at: string;
  campaign_id?: string;
  campaign_name?: string;
}

export interface ContactListPreview {
  total_rows: number;
  valid: number;
  invalid: number;
  duplicates: number;
  column_map: Record<string, string>;
  sample: Record<string, string>[];
  detected_phone_column: string;
}

export interface ContactListCreateRequest {
  name: string;
  file_id?: string;
  column_map: Record<string, string>;
}

export interface ContactListFromLeadsRequest {
  name: string;
  filters: {
    lead_status?: string;
    min_score?: number;
    max_score?: number;
    channel?: string;
    assigned_to?: string;
  };
}
