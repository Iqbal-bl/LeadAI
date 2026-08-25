export interface PromptVersion {
  id: number;
  name: string;
  version: string;
  content: string;
  status: 'Draft' | 'Published' | 'Archived';
  createdBy: string;
  createdAt: string;
  variables: string[];
}
