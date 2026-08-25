export interface Company {
  id: string;
  name: string;
  email: string;
  phone_number: string | null;
  description: string;
  is_active: boolean;
  created_at: string;
  user_count: number;
  document_count: number;
  chunk_count: number;
  script_count: number;
  conversation_count: number;
}

export interface CompanySettings {
  handoff_threshold: number;
  retrieval_top_k: number;
  default_language: string;
  auto_assign_enabled: boolean;
  auto_call_on_hot_lead: boolean;
  widget_enabled: boolean;
  widget_greeting: string;
  effective_handoff_threshold?: number;
  effective_retrieval_top_k?: number;
}

export interface ServiceItemOut {
  key: string; // e.g. "whatsapp", "facebook", "instagram", "voice_agent", "linkedin"
  is_enabled: boolean;
}

export interface CompanyServicesOut {
  company_id: string;
  company_name: string;
  services: ServiceItemOut[];
}

export interface ServicePatchItem {
  key: string;
  is_enabled: boolean;
  config_json?: Record<string, any> | null;
}

export interface CompanyServicesPatchIn {
  services: ServicePatchItem[];
}

