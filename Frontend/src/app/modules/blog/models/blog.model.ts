/* ==========================================================
   Blog Approval & Publication Status
========================================================== */
export type BlogApprovalStatus =
  | 'draft'
  | 'pending_approval'
  | 'approved'
  | 'rejected'
  | 'scheduled'
  | 'published';

export type BlogTone =
  | 'professional'
  | 'conversational'
  | 'thought_leadership'
  | 'informative'
  | 'storytelling'
  | 'persuasive';

export type BlogLength = 'short' | 'standard' | 'in_depth';

export type BlogType = 'text_and_image' | 'text_only';

/* ==========================================================
   AI Blog Prompt Request
========================================================== */
export interface BlogPromptRequest {
  topic: string;
  tone: BlogTone | string;
  target_audience: string;
  targetAudience?: string; // alias for backwards compatibility
  keywords: string[];
  blog_type: BlogType;
  include_images: boolean;
  num_images: number;
  target_words: number;
  target_length: BlogLength | string;
  length?: BlogLength; // alias
  language: string;
  cta_text: string;
  custom_outline?: string;
  includeCallToAction?: boolean;
  targetPlatform?: string;
}

/* ==========================================================
   Publishing Channels / CMS Platforms
========================================================== */
export interface BlogPlatform {
  id: string;
  name: string;
  key: 'wordpress' | 'medium' | 'ghost' | 'devto' | 'linkedin' | 'custom_cms';
  icon: string;
  enabled: boolean;
  category?: string;
}

/* ==========================================================
   SEO & Meta Settings
========================================================== */
export interface BlogSeoMeta {
  metaTitle: string;
  metaDescription: string;
  slug: string;
  canonicalUrl?: string;
  focusKeywords: string[];
  ogImage?: string;
}

/* ==========================================================
   Admin Review & Feedback Notes
========================================================== */
export interface BlogReviewNote {
  id: string;
  authorName: string;
  authorRole: 'admin' | 'editor' | 'author';
  timestamp: string;
  content: string;
  actionTaken?: 'draft' | 'submitted' | 'approved' | 'changes_requested' | 'published';
}

/* ==========================================================
   Unified Blog Post Model
========================================================== */
export interface BlogPost {
  id: string;
  title: string;
  subtitle?: string;
  slug: string;
  content: string; // HTML rich content
  excerpt: string;
  coverImage?: string;
  coverImageCaption?: string;
  images?: string[];
  author: {
    name: string;
    avatar?: string;
    title?: string;
    email?: string;
    department?: string;
  };
  tags: string[];
  category: string;
  status: BlogApprovalStatus;
  scheduledDate?: string;
  publishedAt?: string;
  createdAt: string;
  updatedAt: string;
  selectedPlatforms: string[];
  seo: BlogSeoMeta;
  reviewNotes: BlogReviewNote[];
  wordCount?: number;
  readingTimeMinutes?: number;
}
