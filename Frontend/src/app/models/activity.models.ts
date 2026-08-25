export interface ActivityLogItem {
  id: string;
  client_id: string;
  actor_email: string;
  actor_role: string;
  action: string;
  log_type: 'Security' | 'Warning' | 'Error' | string;
  entity_type: string;
  entity_id: string;
  message: string;
  meta: Record<string, any>;
  ip_address: string;
  created_at: string;
}

export interface ActivityResponse {
  total_items: number;
  page: number;
  page_size: number;
  items: ActivityLogItem[];
}
