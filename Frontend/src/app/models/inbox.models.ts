import { ChatMessage } from './chat.models';

export interface LeadInfo {
  status: 'cold' | 'warm' | 'hot' | 'qualified' | string;
  score: number;
  interest: string | null;
  budget: string | null;
  timeline: string | null;
  product: string | null;
  sentiment: string | null;
  score_breakdown: Record<string, number>;
  qualified_at: string | null;
}

export interface LeadInboxItem {
  id: string;
  client_id: string;
  channel:
    | 'web'
    | 'voice'
    | 'whatsapp'
    | 'messenger'
    | 'sms'
    | 'email'
    | string;
  status: 'open' | 'needs_human' | 'closed' | string;
  customer_ref: string;
  customer_name: string | null;
  customer_phone_masked: string;
  summary: string;
  next_step: string;
  assigned_user_email: string | null;
  handoff_reason: string | null;
  language: string;
  message_count: number;
  last_message_at: string;
  created_at: string;
  lead: LeadInfo;
  above_threshold?: boolean;
  campaign_id?: string;
}

export interface LeadDetail extends LeadInboxItem {
  messages: ChatMessage[];
  calls: any[];
  suggestions: string[];
}

export interface ContactInfo {
  phone: string;
  email: string | null;
  whatsapp: string | null;
  instagram: string | null;
  revealed_at: string;
  warning: string;
  display_name: string;
}

export interface InboxResponse {
  total_items: number;
  page: number;
  page_size: number;
  items: LeadInboxItem[];
}
