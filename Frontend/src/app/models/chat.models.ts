export interface PublicCompany {
  id: string;
  name: string;
  description: string;
  widget_greeting: string;
}

export interface ChatStartPayload {
  company: string;
  display_name?: string | null;
  phone?: string | null;
  email?: string | null;
  whatsapp?: string | null;
  instagram?: string | null;
  channel?: string;
  language?: string;
}

export interface ChatSession {
  session_token: string;
  conversation_id: string;
  company: string;
  greeting: string;
  expires_in_minutes: number;
}

export interface ChatMessage {
  id?: string;
  sender?: 'customer' | 'ai' | 'agent' | 'system';
  message: string;
  reply?: string;
  confidence?: number;
  needs_human?: boolean;
  handed_off_to_human?: boolean;
  sources?: any[];
  lead_status?: string;
  lead_score?: number;
  created_at?: string;
  content: string;
}
