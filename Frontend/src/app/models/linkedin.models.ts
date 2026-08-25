export interface LinkedInStatus {
  connected: boolean;
  person_urn?: string;
  access_token_valid?: boolean;
  has_refresh_token?: boolean;
  has_cookie_credentials?: boolean;
  [key: string]: any;
}

export interface LinkedInCredentialsPayload {
  cookie_li_at?: string | null;
  username?: string | null;
  password?: string | null;
}

export interface LinkedInProfile {
  public_id: string;
  urn_id?: string;
  name: string;
  headline?: string;
  location?: string;
  profile_url?: string;
  avatar_url?: string;
  selected?: boolean;
}

export interface GenerateKeywordsRequest {
  prompt: string;
}

export interface GenerateKeywordsResponse {
  keywords: string;
}

export interface SearchProfilesRequest {
  keywords: string;
  limit?: number;
}

export interface SearchProfilesResponse {
  profiles: LinkedInProfile[];
}

export interface SendInvitationsProfileItem {
  public_id: string;
  urn_id?: string;
  name: string;
}

export interface SendInvitationsRequest {
  profiles: SendInvitationsProfileItem[];
  message?: string;
}

export interface InvitationResultItem {
  success: boolean;
  message: string;
}

export interface SendInvitationsResponse {
  results: Record<string, InvitationResultItem>;
}
