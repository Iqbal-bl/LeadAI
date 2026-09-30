export interface ContactList {
  id: string;
  name: string;
  total_rows?: number;
  total_count?: number;
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

// Flat, matching the backend's ContactListFromLeads schema (schemas_ext.py) exactly —
// a nested `filters` object here was silently dropped by Pydantic (extra fields are
// ignored by default), so a request like {filters: {min_score: 50}} built a list from
// EVERY lead, unfiltered, with no error.
export interface ContactListFromLeadsRequest {
  name: string;
  description?: string;
  status?: string[];
  min_score?: number;
  above_threshold?: boolean;
  channel?: string;
  created_after?: string;
}
