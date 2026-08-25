export interface AccessibleCompany {
  id: string;
  name: string;
  is_active: boolean;
}

export interface UserMe {
  email: string;
  full_name: string;
  role: string;
  client_id: string;
  client_name: string;
  permissions: string[];
  accessible_companies: AccessibleCompany[];
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
  lastActive: string;
  assignedLeads: number;
}

