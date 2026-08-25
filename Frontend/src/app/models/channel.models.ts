export type ChannelType = 'whatsapp' | 'messenger' | 'instagram' | 'linkedin';

export interface LinkedInStatus {
  connected: boolean;
  person_urn?: string;
  access_token_valid?: boolean;
  has_refresh_token?: boolean;
  [key: string]: any;
}

export interface Channel {
  id: string;
  client_id?: string;
  channel: ChannelType | string;
  provider?: string;
  name?: string;
  display_name?: string;
  external_id: string;
  display_number?: string;
  business_account_id?: string;
  api_version?: string;
  is_active?: boolean;
  auto_reply?: boolean;
  script_id?: string;
  default_language?: string;
  has_access_token?: boolean;
  has_app_secret?: boolean;
  verify_token?: string;
  webhook_url?: string;
  last_inbound_at?: string | null;
  last_outbound_at?: string | null;
  last_error?: string | null;
  last_error_at?: string | null;
  created_at?: string;
  updated_at?: string;
  status?: 'active' | 'inactive' | 'pending' | string;
}

export interface ChannelCreateRequest {
  channel: ChannelType | string;
  name: string;
  external_id: string;
  access_token: string;
  app_secret?: string;
  verify_token?: string;
  business_account_id?: string;
  display_number?: string;
  api_version?: string;
  auto_reply?: boolean;
  script_id?: string;
  default_language?: string;
}

export interface ChannelUpdateRequest {
  name?: string;
  access_token?: string;
  app_secret?: string;
  verify_token?: string;
  display_number?: string;
  api_version?: string;
  is_active?: boolean;
  auto_reply?: boolean;
  script_id?: string;
  default_language?: string;
}

export interface ChannelCreateResponse extends Channel {}

export interface ChannelTestRequest {
  to: string;
  message?: string;
  template_name?: string;
  template_language?: string;
  template_params?: any[];
}

export interface ChannelTestResponse {
  success: boolean;
  message: string;
}

export interface ChannelDeleteResponse {
  success: boolean;
  message: string;
}

export interface ChannelContact {
  id?: string;
  identifier_masked?: string;
  phone?: string;
  name?: string | null;
  in_session_window?: boolean;
  last_message_at?: string;
  channel?: ChannelType | string;
  [key: string]: any;
}

export interface ChannelStatus {
  total_channels?: number;
  active_channels?: number;
  total_contacts?: number;
  messages_today?: number;
  [key: string]: any;
}

