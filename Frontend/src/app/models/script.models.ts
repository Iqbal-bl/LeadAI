export interface Script {
  id?: string;
  name: string;
  description: string;
  channel: 'all' | 'chat' | 'voice';
  language: string;
  script_xml: string;
  is_default?: boolean;
  is_active?: boolean;
  version?: number;
  voice_gender?: 'male' | 'female';
  voice_speaker?: string;
  multi_stt?: boolean;
}

export interface ScriptPreview {
  script_id: string;
  script_name?: string;
  channel: 'chat' | 'voice';
  system_prompt: string;
  sections?: any[];
  character_count: number;
}

export interface PromptTemplate {
  key: string;
  value: string;
  is_customised: boolean;
}
