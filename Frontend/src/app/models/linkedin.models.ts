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

export interface LinkedInInvitationItem {
  invitation_urn: string;
  shared_secret: string;
  sender_urn?: string;
  public_id?: string;
  name: string;
  headline?: string;
  message?: string;
  sent_time?: number | string;
  avatar_url?: string;
  processing?: boolean;
}

export interface GetInvitationsResponse {
  invitations: LinkedInInvitationItem[];
}

export interface LinkedInReplyInvitationPayload {
  invitation_urn: string;
  shared_secret: string;
  action: 'accept' | 'reject';
  sender_name?: string;
  sender_urn?: string;
  public_id?: string;
}

export interface LinkedInSettingsPayload {
  auto_accept: boolean;
  welcome_message?: string | null;
}

export interface BatchAcceptResponse {
  processed: number;
  accepted: number;
}

export interface LinkedInConversationParticipant {
  name: string;
  headline?: string;
  public_id?: string;
  urn?: string;
  picture_url?: string | null;
  is_self?: boolean;
}

export interface LinkedInConversation {
  conversation_urn: string;
  conversation_id: string;
  contact_name: string;
  contact_headline?: string;
  contact_public_id?: string;
  contact_urn?: string;
  contact_avatar?: string | null;
  participants?: LinkedInConversationParticipant[];
  last_message?: string;
  last_sender_name?: string;
  last_activity_at?: number;
  unread_count?: number;
  is_read?: boolean;
  total_events?: number;
}

export interface LinkedInMessage {
  event_urn?: string;
  created_at?: number;
  text: string;
  sender_name: string;
  sender_urn?: string;
  sender_public_id?: string;
  sender_avatar?: string | null;
  is_self: boolean;
}

export interface GetConversationsResponse {
  conversations: LinkedInConversation[];
}

export interface GetConversationMessagesResponse {
  messages: LinkedInMessage[];
}

export interface SendMessageResponse {
  success: boolean;
  message: string;
}

export interface SyncMessagesResponse {
  synced_conversations: number;
  synced_messages: number;
}


