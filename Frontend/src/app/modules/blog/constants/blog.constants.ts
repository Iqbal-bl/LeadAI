import {
  BlogPlatform,
  BlogTone,
  BlogLength,
  BlogPromptRequest,
} from '../models/blog.model';

/* ==========================================================
   Supported Publishing Channels / CMS Platforms
========================================================== */
export const DEFAULT_BLOG_PLATFORMS: BlogPlatform[] = [
  {
    id: 'p-wp',
    name: 'WordPress CMS',
    key: 'wordpress',
    icon: 'pi pi-globe',
    enabled: true,
    category: 'CMS & Website',
  },
  {
    id: 'p-med',
    name: 'Medium Publication',
    key: 'medium',
    icon: 'pi pi-book',
    enabled: true,
    category: 'Publishing Network',
  },
  {
    id: 'p-ghost',
    name: 'Ghost Blog',
    key: 'ghost',
    icon: 'pi pi-file',
    enabled: false,
    category: 'Headless CMS',
  },
  {
    id: 'p-devto',
    name: 'Dev.to Community',
    key: 'devto',
    icon: 'pi pi-code',
    enabled: true,
    category: 'Developer Platform',
  },
  {
    id: 'p-linkedin',
    name: 'LinkedIn Newsletter',
    key: 'linkedin',
    icon: 'pi pi-linkedin',
    enabled: true,
    category: 'Professional Network',
  },
  {
    id: 'p-custom',
    name: 'Custom Webhook API',
    key: 'custom_cms',
    icon: 'pi pi-server',
    enabled: false,
    category: 'Direct Integration',
  },
];

/* ==========================================================
   Blog Tone Options
========================================================== */
export const BLOG_TONE_OPTIONS: { label: string; value: BlogTone }[] = [
  { label: 'Professional', value: 'professional' },
  { label: 'Conversational', value: 'conversational' },
  { label: 'Thought Leadership', value: 'thought_leadership' },
  { label: 'Informative', value: 'informative' },
  { label: 'Storytelling', value: 'storytelling' },
  { label: 'Persuasive', value: 'persuasive' },
];

/* ==========================================================
   Target Word Length Presets
========================================================== */
export const BLOG_LENGTH_PRESETS: { label: string; value: BlogLength; words: number }[] = [
  { label: 'Short (~300w)', value: 'short', words: 300 },
  { label: 'Standard (~1000w)', value: 'standard', words: 1000 },
  { label: 'In-Depth (~2000w)', value: 'in_depth', words: 2000 },
];

/* ==========================================================
   Available Filter Categories
========================================================== */
export const BLOG_CATEGORY_OPTIONS: { label: string; value: string }[] = [
  { label: 'All Categories', value: 'all' },
  { label: 'Growth & Strategy', value: 'Growth & Strategy' },
  { label: 'Engineering & AI', value: 'Engineering & AI' },
  { label: 'Customer Experience', value: 'Customer Experience' },
  { label: 'Industry Trends', value: 'Industry Trends' },
  { label: 'Artificial Intelligence', value: 'Artificial Intelligence' },
];

/* ==========================================================
   Sorting Options
========================================================== */
export const BLOG_SORT_OPTIONS: { label: string; value: string }[] = [
  { label: 'Newest First', value: 'newest' },
  { label: 'Oldest First', value: 'oldest' },
  { label: 'Title (A - Z)', value: 'title_asc' },
  { label: 'Word Count (High to Low)', value: 'words_desc' },
];

/* ==========================================================
   Default Initial AI Prompt Form State
========================================================== */
export const DEFAULT_BLOG_PROMPT_FORM: BlogPromptRequest = {
  topic: 'What is Generative AI?',
  tone: 'professional',
  target_audience: 'General Audience',
  targetAudience: 'General Audience',
  keywords: ['Generative AI'],
  blog_type: 'text_and_image',
  include_images: true,
  num_images: 1,
  target_words: 300,
  target_length: 'short',
  length: 'short',
  language: 'English',
  cta_text: 'Learn more about AI.',
};
