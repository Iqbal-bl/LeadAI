export interface ArticleVersion {
  id: string;
  version_number: number;
  title: string;
  content: string;
  summary?: string;
  cover_image?: string;
  images?: string[];
  tags?: string[];
  regeneration_prompt?: string;
  created_at?: string;
}

export interface ReviewNote {
  id: string;
  version_number?: number;
  author_name: string;
  author_role: string;
  content: string;
  action_taken: string;
  created_at?: string;
}

export interface Article {
  id: string;
  client_id: string;
  title: string;
  slug?: string;
  content: string;
  summary?: string;
  cover_image?: string;
  images?: string[];
  tags?: string[];
  status: 'draft' | 'pending_approval' | 'approved' | 'changes_requested' | 'scheduled' | 'published' | 'rejected' | 'generating' | 'generation_failed';
  requires_approval: boolean;
  generation_mode: string;
  current_version: number;
  author_name?: string;
  author_email?: string;
  author_role?: string;
  scheduled_at?: string;
  published_at?: string;
  submitted_at?: string;
  reviewed_at?: string;
  target_channels?: string[];
  results?: Record<string, any>;
  linkedin_post_id?: string;
  facebook_post_id?: string;
  instagram_media_id?: string;
  wordpress_post_url?: string;
  review_token?: string;
  created_at?: string;
  updated_at?: string;
  versions?: ArticleVersion[];
  review_notes?: ReviewNote[];
}

export interface ArticleListResponse {
  total: number;
  items: Article[];
}

export interface DashboardStats {
  draft: number;
  pending_approval: number;
  approved: number;
  changes_requested: number;
  scheduled: number;
  published: number;
  rejected: number;
  total: number;
}

export interface BlogSettings {
  client_id: string;
  is_auto_blog_enabled: boolean;
  mode: 'automatic' | 'manual_confirmation';
  schedule_time: string;
  last_run_at?: string;
  next_run_at?: string;
  topic_niche?: string;
  keywords: string[];
  target_audience?: string;
  tone?: string;
  language?: string;
  target_words: number;
  include_images: boolean;
  num_images: number;
  cta_text?: string;
  cta_url?: string;
  target_channels: string[];
  admin_notification_email?: string;
  wordpress_url?: string;
  wordpress_username?: string;
  has_wordpress_password?: boolean;
}

export interface GenerateBlogRequest {
  topic: string;
  tone?: string;
  target_audience?: string;
  keywords?: string[];
  blog_type?: string;
  include_images?: boolean;
  num_images?: number;
  target_words?: number;
  language?: string;
  cta_text?: string;
  cta_url?: string;
  target_channels?: string[];
  requires_approval?: boolean;
  admin_reviewer_email?: string;
}

export interface ReviewActionRequest {
  action: 'approved' | 'changes_requested' | 'rejected' | 'regenerate';
  notes?: string;
  reviewer_name?: string;
  reviewer_role?: string;
  regeneration_prompt?: string;
  publish_now?: boolean;
  target_channels?: string[];
}
