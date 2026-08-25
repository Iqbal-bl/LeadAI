export interface KbDocument {
  id: string;
  title: string;
  file_name: string;
  content_type: string;
  source_type: string;
  status: 'pending' | 'indexing' | 'indexed' | 'failed' | 'deleted';
  status_message: string | null;
  chunk_count: number;
  char_count: number;
  embedding_model: string;
  tags: string;
  created_at: string;
  created_by: string;
}

export interface KbChunk {
  id: string;
  position: number;
  text: string;
  token_count: number;
  embedding_model: string;
  embedding_dim: number;
}

export interface KbDocumentChunks {
  document_id: string;
  title: string;
  total_chunks: number;
  chunks: KbChunk[];
}

export interface KbStats {
  client_id: string;
  documents: number;
  chunks: number;
  models: Record<string, number>;
  needs_reindex: boolean;
  backend: string;
  embedding_backend: string;
  embedding_model: string;
}

export interface KbSource {
  chunk_id: string;
  document_id: string;
  score: number;
  excerpt: string;
}

export interface KbTestResult {
  company: string;
  query: string;
  answer: string;
  confidence: number;
  needs_human: boolean;
  handoff_reason: string | null;
  model: string;
  latency_ms: number;
  sources: KbSource[];
}

export interface KnowledgeBaseDoc {
  id: number;
  fileName: string;
  fileType: string;
  chunks: number;
  uploadDate: string;
  uploadedBy: string;
  status: 'Processed' | 'Processing' | 'Failed';
}

export interface Faq {
  id: number;
  question: string;
  answer: string;
  category: string;
  updatedDate: string;
}

