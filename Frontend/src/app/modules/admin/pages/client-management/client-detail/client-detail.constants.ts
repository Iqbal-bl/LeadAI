import { CompanySettings } from '../../../../../models/company.models';

export interface ServiceAccessItem {
  id: string;
  key?: string;
  isEnabled?: boolean;
  name: string;
  category: 'Voice' | 'Social' | 'Web' | 'Messaging';
  description: string;
  icon: string;
  brandColor: string;
  bgGradient: string;
  status: 'active' | 'configured' | 'available' | 'disabled';
  statusLabel: string;
  badgeSeverity: 'success' | 'info' | 'warn' | 'secondary';
  features: string[];
  configDetails?: {
    accountName?: string;
    accountHandle?: string;
    displayNumber?: string;
    autoReply?: boolean;
    lastActive?: string;
    extraNote?: string;
  };
}

export interface ServiceStaticConfig {
  id: string;
  key: string;
  legacyKey?: string;
  name: string;
  category: 'Voice' | 'Social' | 'Web' | 'Messaging';
  description: string;
  icon: string;
  brandColor: string;
  bgGradient: string;
  features: string[];
  defaultConfig?: {
    accountName?: string;
    accountHandle?: string;
    displayNumber?: string;
    autoReply?: boolean;
    lastActive?: string;
    extraNote?: string;
  };
}

export const DEFAULT_SARVAM_FEMALE_VOICE = 'ritu';
export const DEFAULT_SARVAM_MALE_VOICE = 'shubh';

export const DEFAULT_COMPANY_SETTINGS: CompanySettings = {
  handoff_threshold: 65,
  retrieval_top_k: 5,
  default_language: 'en',
  auto_assign_enabled: true,
  auto_call_on_hot_lead: true,
  widget_enabled: true,
  widget_greeting: 'Hello! How can our AI assistant help you today?',
  agent_name: null,
  voice_gender: 'female',
  voice_speed: 1.1,
  voice_speaker: DEFAULT_SARVAM_FEMALE_VOICE,
  stt_tts_provider: 'sarvam',
  multi_stt: true,
};

export const DEFAULT_VOICE_SETTINGS = {
  voice_gender: 'female' as const,
  voice_speed: 1.1,
  voice_speaker: DEFAULT_SARVAM_FEMALE_VOICE,
  stt_tts_provider: 'sarvam' as const,
  multi_stt: true,
};

export interface VoiceSpeakerOption {
  label: string;
  value: string;
  gender: 'female' | 'male';
}

/**
 * Confirmed Sarvam bulbul:v3 voice roster for AI call telephony (37 real voices).
 * Grouped by gender with lowercase API values and recommended badges.
 */
export const SARVAM_VOICE_ROSTER: VoiceSpeakerOption[] = [
  // Female Voices (14)
  {
    label: 'Ritu (Default - Standard Platform Voice)',
    value: 'ritu',
    gender: 'female',
  },
  {
    label: 'Priya (⭐ Recommended - Best Overall)',
    value: 'priya',
    gender: 'female',
  },
  {
    label: 'Ishita (⭐ Recommended - Hindi & Multi-Language)',
    value: 'ishita',
    gender: 'female',
  },
  { label: 'Neha', value: 'neha', gender: 'female' },
  { label: 'Pooja', value: 'pooja', gender: 'female' },
  { label: 'Simran', value: 'simran', gender: 'female' },
  { label: 'Kavya', value: 'kavya', gender: 'female' },
  { label: 'Shreya', value: 'shreya', gender: 'female' },
  { label: 'Roopa', value: 'roopa', gender: 'female' },
  { label: 'Tanya', value: 'tanya', gender: 'female' },
  { label: 'Shruti', value: 'shruti', gender: 'female' },
  { label: 'Suhani', value: 'suhani', gender: 'female' },
  { label: 'Kavitha', value: 'kavitha', gender: 'female' },
  { label: 'Rupali', value: 'rupali', gender: 'female' },

  // Male Voices (23)
  {
    label: 'Shubh (⭐ Recommended - Hindi, Telugu, Kannada, Odia, Malayalam)',
    value: 'shubh',
    gender: 'male',
  },
  {
    label: 'Mani (⭐ Recommended - Best Overall & Punjabi)',
    value: 'mani',
    gender: 'male',
  },
  {
    label: 'Ratan (⭐ Recommended - English & Multi-Language)',
    value: 'ratan',
    gender: 'male',
  },
  { label: 'Aditya', value: 'aditya', gender: 'male' },
  { label: 'Rahul', value: 'rahul', gender: 'male' },
  { label: 'Rohan', value: 'rohan', gender: 'male' },
  { label: 'Amit', value: 'amit', gender: 'male' },
  { label: 'Dev', value: 'dev', gender: 'male' },
  { label: 'Varun', value: 'varun', gender: 'male' },
  { label: 'Manan', value: 'manan', gender: 'male' },
  { label: 'Sumit', value: 'sumit', gender: 'male' },
  { label: 'Kabir', value: 'kabir', gender: 'male' },
  { label: 'Aayan', value: 'aayan', gender: 'male' },
  { label: 'Ashutosh', value: 'ashutosh', gender: 'male' },
  { label: 'Advait', value: 'advait', gender: 'male' },
  { label: 'Anand', value: 'anand', gender: 'male' },
  { label: 'Tarun', value: 'tarun', gender: 'male' },
  { label: 'Sunny', value: 'sunny', gender: 'male' },
  { label: 'Gokul', value: 'gokul', gender: 'male' },
  { label: 'Vijay', value: 'vijay', gender: 'male' },
  { label: 'Mohit', value: 'mohit', gender: 'male' },
  { label: 'Rehan', value: 'rehan', gender: 'male' },
  { label: 'Soham', value: 'soham', gender: 'male' },
];

export const WIDGET_EMBED_CONFIG = {
  scriptSrc: 'https://cdn.leadai.com/widget.js',
};

export const SERVICES_STATIC_CONFIG: ServiceStaticConfig[] = [
  {
    id: 'voice-calling',
    key: 'voice_agent',
    name: 'AI Voice & Dialler',
    category: 'Voice',
    description:
      'Inbound & outbound synthetic voice dialler with automated calling for hot leads, speech-to-text live transcription, and audio recordings.',
    icon: 'pi pi-phone',
    brandColor: '#f59e0b',
    bgGradient: 'linear-gradient(135deg, #f59e0b 0%, #d97706 100%)',
    features: [
      'Outbound AI Lead Calling',
      'Live Call Transcripts',
      'Call Audio Recording & Playback',
      'Human Agent Handoff Routing',
    ],
    defaultConfig: {
      displayNumber: '+1 (800) 555-0199',
      extraNote: 'Twilio Voice Integration Active',
    },
  },
  {
    id: 'whatsapp',
    key: 'social.whatsapp',
    legacyKey: 'whatsapp',
    name: 'WhatsApp Business API',
    category: 'Social',
    description:
      'Official Meta Cloud API integration for WhatsApp messaging, verified template notifications, and automated 24/7 AI chat replies.',
    icon: 'pi pi-whatsapp',
    brandColor: '#25D366',
    bgGradient: 'linear-gradient(135deg, #25D366 0%, #128C7E 100%)',
    features: [
      'Meta Cloud API v21.0',
      'Automated AI Inbound Replies',
      'Rich Media & Document Delivery',
      'Verified Business Number',
    ],
    defaultConfig: {
      accountName: 'Main WhatsApp Line',
      displayNumber: '+1 (555) 019-2834',
    },
  },
  {
    id: 'messenger',
    key: 'social.facebook',
    legacyKey: 'facebook',
    name: 'Facebook Messenger',
    category: 'Social',
    description:
      'Meta Page Messenger webhook routing. Engages prospects directly from Facebook ads, post comments, and company page inbox.',
    icon: 'pi pi-facebook',
    brandColor: '#0084FF',
    bgGradient: 'linear-gradient(135deg, #0084FF 0%, #0063E6 100%)',
    features: [
      'Page Messaging Webhooks',
      'Facebook Ads Click-to-Chat Capture',
      'Instant AI Qualification',
      'Seamless Human Takeover',
    ],
    defaultConfig: {
      accountName: 'Facebook Page Inbox',
    },
  },
  {
    id: 'instagram',
    key: 'social.instagram',
    legacyKey: 'instagram',
    name: 'Instagram Direct (DM)',
    category: 'Social',
    description:
      'Automated Instagram Direct message responses, story mention replies, and comment-to-DM conversion funnels.',
    icon: 'pi pi-instagram',
    brandColor: '#E4405F',
    bgGradient: 'linear-gradient(135deg, #E4405F 0%, #833AB4 100%)',
    features: [
      'Instagram Business Graph API',
      'Story Reply Lead Generation',
      'DM Instant AI Response',
      'Comment Automation',
    ],
    defaultConfig: {
      accountHandle: '@techcorp_solutions',
    },
  },
  {
    id: 'linkedin',
    key: 'social.linkedin',
    legacyKey: 'linkedin',
    name: 'LinkedIn Automation',
    category: 'Social',
    description:
      'B2B Social outreach and company profile integration with automated connection requests, message sync, and lead discovery.',
    icon: 'pi pi-linkedin',
    brandColor: '#0A66C2',
    bgGradient: 'linear-gradient(135deg, #0A66C2 0%, #004182 100%)',
    features: [
      'LinkedIn OAuth Authorization',
      'Profile & Company Sync',
      'Automated Social Outreach',
      'B2B Lead Qualification',
    ],
  },
  {
    id: 'sms-email',
    key: 'email_marketing',
    legacyKey: 'email',
    name: 'Email Marketing & Outreach',
    category: 'Messaging',
    description:
      'Two-way messaging, transactional email drip campaigns, and automated follow-up triggers.',
    icon: 'pi pi-envelope',
    brandColor: '#8b5cf6',
    bgGradient: 'linear-gradient(135deg, #8b5cf6 0%, #a855f7 100%)',
    features: [
      'Automated SMS Follow-ups',
      'Transactional Email Delivery',
      'Opt-In / Opt-Out Consent Tracking',
      'Delivery Status Webhooks',
    ],
    defaultConfig: {
      displayNumber: '+1 (555) 018-9922',
      extraNote: 'AWS SES Active',
    },
  },
  {
    id: 'webchat',
    key: 'web',
    name: 'Web Chat Widget',
    category: 'Web',
    description:
      'Lightweight embeddable chat widget for client websites with custom brand colors, greeting scripts, and lead capture forms.',
    icon: 'pi pi-desktop',
    brandColor: '#6366f1',
    bgGradient: 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)',
    features: [
      '1-Line Script Embed Snippet',
      'Customizable AI Greeting',
      'RAG Knowledge Base Answering',
      'Automated Lead Intake Form',
    ],
  },
];
