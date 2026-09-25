import { ChatMessage } from './chat.models';

export interface ScoreBreakdown {
  base?: number;
  intent?: number;
  timeline?: number;
  sentiment?: number;
  engagement?: number;
  budget_known?: number;
  product_known?: number;
  [key: string]: number | undefined;
}

export interface LeadInfo {
  status: 'cold' | 'warm' | 'hot' | 'qualified' | string;
  score: number;
  interest: string | null;
  intent?: string | null;
  budget: string | null;
  timeline: string | null;
  product: string | null;
  sentiment: string | null;
  score_breakdown?: ScoreBreakdown | Record<string, number> | null;
  qualified_at?: string | null;
}

export interface MessageSource {
  score?: number;
  excerpt?: string;
  chunk_id?: string;
  document_id?: string;
}

export interface InboxMessage {
  id: string;
  sender: 'customer' | 'ai' | 'agent' | string;
  sender_email?: string | null;
  content: string;
  confidence?: number | null;
  sources?: MessageSource[] | null;
  model_used?: string | null;
  call_sid?: string | null;
  delivery_status?: 'sent' | 'failed' | 'skipped' | string | null;
  delivery_error?: string | null;
  created_at: string;
  // UI / legacy helper properties
  message?: string;
  timestamp?: string;
  type?: string;
  agent?: string;
}

export interface LeadInboxItem {
  id: string;
  client_id: string;
  channel:
    | 'web'
    | 'voice'
    | 'whatsapp'
    | 'messenger'
    | 'instagram'
    | 'facebook'
    | 'linkedin'
    | 'sms'
    | 'email'
    | string;
  status: 'open' | 'assigned' | 'needs_human' | 'closed' | string;
  customer_ref: string;
  customer_name: string | null;
  customer_phone_masked: string | null;
  summary: string | null;
  next_step: string | null;
  assigned_user_email: string | null;
  handoff_reason: string | null;
  language: string | null;
  message_count: number;
  last_message_at: string;
  created_at: string;
  lead: LeadInfo | null;
  above_threshold?: boolean;
  campaign_id?: string;
}

export interface DeliveryInfo {
  status: 'sent' | 'failed' | 'skipped' | 'not_applicable' | string;
  delivered: boolean;
  channel?: string | null;
  message_id?: string | null;
  error?: string | null;
  detail?: string | null;
}

export interface LeadDetail extends LeadInboxItem {
  messages: InboxMessage[];
  suggestions: string[];
  has_calls: boolean;
  call_count: number;
  calls?: any[];
  delivery?: DeliveryInfo | null;

  // UI computed / mapped fields
  name?: string;
  email?: string;
  phone?: string;
  company?: string;
  address?: string;
  industry?: string;
  tags?: string[];
  leadScore?: number;
  priority?: 'High' | 'Medium' | 'Low' | string;
  assignedTo?: string;
  createdAt?: string;
  updatedAt?: string;
  leadStatus?: string;
  source?: string;
  avatar?: string;
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
