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

export interface CompanyCreatePayload {
  name: string;
  email?: string;
  phone_number?: string;
  description?: string;
  admin_email?: string;
  admin_name?: string;
  permissions?: string[];
}

export interface PermissionItemOut {
  key: string; // e.g. "social.whatsapp", "social.facebook", "social.instagram", "voice_agent", "social.linkedin", "email_marketing"
  is_enabled: boolean;
}

export interface CompanyPermissionsOut {
  company_id: string;
  company_name: string;
  permissions: PermissionItemOut[];
}

export interface CompanyPermissionsPatchIn {
  permissions: PermissionItemOut[];
}

// Backward compatibility aliases
export type ServiceItemOut = PermissionItemOut;
export type CompanyServicesOut = CompanyPermissionsOut;
export type ServicePatchItem = PermissionItemOut & { config_json?: Record<string, any> | null };
export interface CompanyServicesPatchIn {
  services: ServicePatchItem[];
}

