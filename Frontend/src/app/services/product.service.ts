import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';
import { Product, ProductListResponse } from '../models/product.models';

@Injectable({
  providedIn: 'root',
})
export class ProductService {
  constructor(private apiService: ApiService) {}

  /**
   * List products for the authenticated company
   * GET /api/leadai/products
   */
  public getProducts(search?: string, productType?: string): Observable<ProductListResponse> {
    const params: Record<string, string> = {};
    if (search) {
      params['search'] = search;
    }
    if (productType) {
      params['product_type'] = productType;
    }
    return this.apiService.get<ProductListResponse>('products', {
      params,
      companyScoped: true,
    });
  }

  /**
   * Get single product details
   * GET /api/leadai/products/{id}
   */
  public getProduct(id: string): Observable<Product> {
    return this.apiService.get<Product>(`products/${id}`, {
      companyScoped: true,
    });
  }

  /**
   * Add a product with an attached knowledge base file
   * POST /api/leadai/products (multipart/form-data)
   */
  public createProduct(formData: FormData): Observable<Product> {
    return this.apiService.post<Product>('products', formData, {
      companyScoped: true,
    });
  }

  /**
   * Update product details or replace knowledge base file
   * PUT /api/leadai/products/{id} (multipart/form-data)
   */
  public updateProduct(id: string, formData: FormData): Observable<Product> {
    return this.apiService.put<Product>(`products/${id}`, formData, {
      companyScoped: true,
    });
  }

  /**
   * Soft-delete a product
   * DELETE /api/leadai/products/{id}
   */
  public deleteProduct(id: string): Observable<{ success: boolean; message: string }> {
    return this.apiService.delete<{ success: boolean; message: string }>(`products/${id}`, {
      companyScoped: true,
    });
  }

  /**
   * Bind an existing company knowledge base document to a product
   * POST /api/leadai/products/{id}/bind-existing-kb
   */
  public bindExistingKb(productId: string, kbDocumentId: string): Observable<Product> {
    return this.apiService.post<Product>(
      `products/${productId}/bind-existing-kb`,
      { kb_document_id: kbDocumentId },
      { companyScoped: true }
    );
  }

  /**
   * Upload and bind a new knowledge base file to a product
   * POST /api/leadai/products/{id}/bind-kb (multipart/form-data)
   */
  public uploadAndBindKb(productId: string, file: File): Observable<Product> {
    const formData = new FormData();
    formData.append('file', file);
    return this.apiService.post<Product>(`products/${productId}/bind-kb`, formData, {
      companyScoped: true,
    });
  }

  /**
   * Unbind a knowledge base document from a product
   * DELETE /api/leadai/products/{id}/bind-kb/{kb_document_id}
   */
  public unbindKb(productId: string, kbDocumentId: string): Observable<Product> {
    return this.apiService.delete<Product>(`products/${productId}/bind-kb/${kbDocumentId}`, {
      companyScoped: true,
    });
  }
}
