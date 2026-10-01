export interface BoundKbDoc {
  id: string;
  title: string;
  file_name?: string | null;
  content_type?: string | null;
  chunk_count: number;
  status: string;
  is_primary: boolean;
  created_at?: string | null;
}

export interface Product {
  id: string;
  client_id: string;
  product_name: string;
  product_type: string;
  knowledge_base_file?: string | null;
  kb_document_id?: string | null;
  bound_kb_document_ids?: string[];
  bound_kb_documents?: BoundKbDoc[];
  created_at?: string | null;
  created_by?: string | null;
  updated_at?: string | null;
  updated_by?: string | null;
  is_deleted?: boolean;
}

export interface ProductListResponse {
  total: number;
  items: Product[];
}
