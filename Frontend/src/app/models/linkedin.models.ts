export interface LinkedInAccount {
  id: string;
  name: string;
  person_urn: string;
  connected: boolean;
  access_token_valid: boolean;
  has_refresh_token: boolean;
  has_cookie_credentials: boolean;
  profile_picture_url?: string | null;
  email?: string | null;
  is_active: boolean;
  auto_accept?: boolean;
  welcome_message?: string | null;
  auto_dm_leads?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface LinkedInStatus {
  connected: boolean;
  person_urn?: string;
  access_token_valid?: boolean;
  has_refresh_token?: boolean;
  has_cookie_credentials?: boolean;
  connected_count?: number;
  accounts?: LinkedInAccount[];
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
  profile_url?: string;
  name: string;
  headline?: string;
  message?: string;
  sent_time?: number | string;
  avatar_url?: string;
  processing?: boolean;
  is_crm_lead?: boolean;
  [key: string]: any;
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
  auto_dm_leads?: boolean;
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
  is_lead_candidate?: boolean;
  lead_status?: string;
  lead_score?: number;
  lead_intent?: string;
  crm_account_id?: string;
  [key: string]: any;
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

export interface LinkedInSocialComment {
  id: string;
  post_urn: string;
  post_title?: string;
  post_snippet?: string;
  comment_urn: string;
  parent_comment_urn?: string;
  author_name: string;
  author_headline?: string;
  author_avatar?: string | null;
  author_profile_url?: string;
  comment_text: string;
  comment_created_at?: string | number;
  sentiment?: 'positive' | 'question' | 'lead_inquiry' | 'praise' | 'critical' | 'neutral';
  intent_score?: number;
  is_question?: boolean;
  is_lead_candidate?: boolean;
  suggested_reply?: string;
  suggested_reply_rationale?: string;
  status: 'pending_review' | 'approved' | 'auto_replied' | 'replied' | 'ignored';
  reply_text?: string;
  reply_urn?: string;
  replied_at?: string | number;
  replied_by?: string;
  customer_id?: string;
  
  // Local UI state
  isEditing?: boolean;
  draftReply?: string;
  customInstruction?: string;
  isGenerating?: boolean;
  isReplying?: boolean;
  isCapturingLead?: boolean;
}

export interface LinkedInCommentSettings {
  is_auto_reply_enabled: boolean;
  require_approval_for_questions: boolean;
  reply_tone: string;
  custom_instructions?: string | null;
  signature_text?: string | null;
  auto_capture_leads: boolean;
  min_lead_intent_threshold: number;
  exclude_keywords: string[];
}

export interface GetCommentsResponse {
  comments: LinkedInSocialComment[];
  total: number;
}

export interface SyncCommentsResponse {
  ok: boolean;
  message: string;
  data?: {
    synced_comments: number;
    new_leads: number;
    auto_replies: number;
    total_posts_scanned: number;
  };
}

export interface LinkedInAutoConnectSettings {
  enabled: boolean;
  runs_per_day: number;
  profiles_per_run: number;
  target_prompt: string;
  target_keywords: string;
  custom_message: string;
  active_hours_start: number;
  active_hours_end: number;
  last_run_at?: string | null;
  next_run_at?: string | null;
  total_sent_today?: number;
  total_sent_all_time?: number;
  last_run_status?: string | null;
  last_run_detail?: string | null;
}

export interface GetAutoConnectSettingsResponse {
  settings: LinkedInAutoConnectSettings;
}

export interface SaveAutoConnectSettingsResponse {
  ok: boolean;
  settings: LinkedInAutoConnectSettings;
}

export interface TriggerAutoConnectResponse {
  ok: boolean;
  message: string;
}

export interface LinkedInRemoteLoginStartRequest {
  username: string;
  password: string;
}

export interface LinkedInRemoteLoginResponse {
  ok: boolean;
  status: 'initializing' | 'submitting' | 'checkpoint_required' | 'success' | 'failed' | 'cancelled' | 'expired';
  session_id: string;
  challenge_type?: 'captcha' | 'email_pin' | 'sms_pin' | '2fa' | 'general_checkpoint' | 'none';
  message?: string;
  has_screenshot?: boolean;
  completed?: boolean;
  expires_in?: number;
}

export interface LinkedInRemoteLoginInteractRequest {
  action: 'click' | 'type' | 'press_key' | 'submit_pin' | 'refresh';
  x?: number | null;
  y?: number | null;
  text?: string | null;
  key?: string | null;
}

