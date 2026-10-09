export interface AccessibleCompany {
  id: string;
  name: string;
  is_active: boolean;
  has_active_subscription?: boolean | null;
}

export interface UserMe {
  email: string;
  full_name: string;
  role: string;
  client_id: string;
  client_name: string;
  permissions: string[];
  accessible_companies: AccessibleCompany[];
  has_active_subscription?: boolean;
  active_subscription_plan?: string | null;
  active_channels?: string[];
  active_features?: string[];
}

/**
 * Payload interface for updating the authenticated user's profile details.
 */
export interface UserProfileUpdatePayload {
  full_name: string;
  phone?: string;
  timezone?: string;
}

/**
 * Extended model for presenting the full user profile including preferences and company context.
 */
export interface UserProfileDetails {
  email: string;
  full_name: string;
  role: string;
  client_id: string;
  client_name: string;
  phone?: string;
  timezone?: string;
  permissions: string[];
  accessible_companies: AccessibleCompany[];
  has_active_subscription?: boolean;
  active_subscription_plan?: string | null;
  active_channels?: string[];
  active_features?: string[];
}

export interface RoleGrant {
  id?: string;
  user_email: string;
  role: string;
  full_name: string;
  client_id?: string;
  client_name?: string;
  is_active?: boolean;
  created_at?: string;
}

export interface PermissionCatalogue {
  permissions: Record<string, string>;
  role_permissions: Record<string, string[]>;
}

export interface AssignableUser {
  id: string;
  user_email: string;
  full_name: string;
  role: string;
  client_name: string;
  is_active: boolean;
}

export interface TeamMember {
  id: number;
  name: string;
  email: string;
  role: string;
  status: 'Active' | 'Inactive' | 'On Leave';
  phone: string;
  avatar: string;
  lastActive?: string;
  assignedLeads: number;
}

export interface RegisterUrlOptions {
  source?: string;
  returnUrl?: string;
  permissions?: string;
  plan?: string;
  planName?: string;
  selectedPlan?: string;
  socialMedia?: string;
  channels?: string;
  billingCycle?: string;
  cycle?: string;
  minutes?: number | string;
  voiceMinutes?: number | string;
  planId?: string;
  planPrice?: number | string;
  totalAmount?: number | string;
}

