export interface Lead {
  id: number;
  name: string;
  email: string;
  phone: string;
  company: string;
  address: string;
  industry: string;
  tags: string[];
  leadScore: number;
  priority: 'High' | 'Medium' | 'Low';
  status: 'New' | 'Assigned' | 'Follow-up' | 'Interested' | 'Negotiation' | 'Won' | 'Lost' | 'Closed';
  source: string;
  assignedTo: string;
  createdAt: string;
  updatedAt: string;
  avatar: string;
}

export interface TimelineEvent {
  timestamp: string;
  speaker: string;
  summary: string;
  confidence: number;
  source: 'AI' | 'Human' | 'System';
  icon: string;
  color: string;
}

export interface AiSummary {
  conversationSummary: string;
  highlights: string[];
  keyRequirements: string[];
  painPoints: string[];
  budget: string;
  timeline: string;
  decisionMaker: string;
  buyingIntent: 'High' | 'Medium' | 'Low';
}

export interface AiSuggestion {
  title: string;
  description: string;
  icon: string;
  priority: 'High' | 'Medium' | 'Low';
  type: string;
}
