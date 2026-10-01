import { Component, OnInit, ViewChild } from '@angular/core';
import { SharedModule } from '../../../../../shared/shared.module';
import { ProductService } from '../../../../../services/product.service';
import { KbService } from '../../../../../services/kb.service';
import { Product } from '../../../../../models/product.models';
import { KbDocument } from '../../../../../models/kb.models';
import { CLIENT_PERMISSIONS } from '../../../constants/permission.constants';
import { ConfirmationService } from '../../../../../shared/services/confirmation.service';
import { ToastService } from '../../../../../shared/services/toast.service';
import { Menu } from 'primeng/menu';
import { MenuItem } from 'primeng/api';

@Component({
  selector: 'client-product-list',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './product-list.component.html',
  styleUrl: './product-list.component.scss',
})
export class ProductListComponent implements OnInit {
  @ViewChild('productActionMenu') productActionMenu!: Menu;

  PERMISSIONS = CLIENT_PERMISSIONS;
  products: Product[] = [];
  isLoading = false;
  isSaving = false;
  searchQuery = '';
  selectedTypeFilter = '';

  // Add / Edit Product Dialog state
  showProductDialog = false;
  isEditMode = false;
  editingProductId: string | null = null;

  // Manage / Bind Knowledge Base Dialog state
  showBindDialog = false;
  managingProduct: Product | null = null;
  companyKbDocs: KbDocument[] = [];
  isLoadingKbDocs = false;
  isBinding = false;
  bindMode: 'existing' | 'upload' = 'existing';
  selectedExistingKbId = '';
  bindUploadFile: File | null = null;

  form: {
    product_name: string;
    product_type: string;
    file: File | null;
    existingFileName?: string | null;
  } = {
    product_name: '',
    product_type: '',
    file: null,
    existingFileName: null,
  };

  selectedProduct: Product | null = null;
  activeActionMenuItems: MenuItem[] = [];

  productTypeOptions = [
    { label: 'Physical Product', value: 'Physical Product' },
    { label: 'Digital Service', value: 'Digital Service' },
    { label: 'SaaS / Software', value: 'SaaS / Software' },
    { label: 'Financial / Loan', value: 'Financial / Loan' },
    { label: 'Real Estate / Property', value: 'Real Estate / Property' },
    { label: 'Consulting / Advisory', value: 'Consulting / Advisory' },
    { label: 'Subscription Plan', value: 'Subscription Plan' },
    { label: 'Other', value: 'Other' },
  ];

  constructor(
    private productService: ProductService,
    private kbService: KbService,
    private confirmationService: ConfirmationService,
    private toastService: ToastService,
  ) {}

  ngOnInit(): void {
    this.loadProducts();
  }

  loadProducts(): void {
    this.isLoading = true;
    this.productService.getProducts(this.searchQuery, this.selectedTypeFilter).subscribe({
      next: (res) => {
        this.products = res.items || [];
        this.isLoading = false;
      },
      error: (err) => {
        this.isLoading = false;
        console.error('Failed to load products', err);
        this.toastService.error('Failed to load products.');
      },
    });
  }

  openAddDialog(): void {
    this.isEditMode = false;
    this.editingProductId = null;
    this.resetForm();
    this.showProductDialog = true;
  }

  openEditDialog(product: Product): void {
    this.isEditMode = true;
    this.editingProductId = product.id;
    this.form = {
      product_name: product.product_name,
      product_type: product.product_type,
      file: null,
      existingFileName: product.knowledge_base_file,
    };
    this.showProductDialog = true;
  }

  resetForm(): void {
    this.form = {
      product_name: '',
      product_type: 'Physical Product',
      file: null,
      existingFileName: null,
    };
  }

  onFileSelected(event: any): void {
    const file = event.target?.files?.[0] || event.files?.[0];
    if (file) {
      if (file.size > 10 * 1024 * 1024) {
        this.toastService.error('File size exceeds the 10MB limit.');
        return;
      }
      this.form.file = file;
    }
  }

  removeSelectedFile(): void {
    this.form.file = null;
  }

  saveProduct(): void {
    if (!this.form.product_name.trim()) {
      this.toastService.warn('Please provide a product name.');
      return;
    }
    if (!this.form.product_type.trim()) {
      this.toastService.warn('Please specify a product type.');
      return;
    }

    this.isSaving = true;
    const formData = new FormData();
    formData.append('product_name', this.form.product_name.trim());
    formData.append('product_type', this.form.product_type.trim());
    if (this.form.file) {
      formData.append('file', this.form.file);
    }

    if (this.isEditMode && this.editingProductId) {
      this.productService.updateProduct(this.editingProductId, formData).subscribe({
        next: (updated) => {
          this.isSaving = false;
          this.toastService.success(`Product "${updated.product_name}" updated successfully.`);
          this.showProductDialog = false;
          this.loadProducts();
        },
        error: (err) => {
          this.isSaving = false;
          const msg = err?.error?.detail || 'Failed to update product.';
          this.toastService.error(msg);
        },
      });
    } else {
      this.productService.createProduct(formData).subscribe({
        next: (created) => {
          this.isSaving = false;
          this.toastService.success(`Product "${created.product_name}" added with knowledge base file.`);
          this.showProductDialog = false;
          this.resetForm();
          this.loadProducts();
        },
        error: (err) => {
          this.isSaving = false;
          const msg = err?.error?.detail || 'Failed to add product.';
          this.toastService.error(msg);
        },
      });
    }
  }

  openProductMenu(event: Event, product: Product): void {
    this.selectedProduct = product;
    this.activeActionMenuItems = [
      {
        label: 'Manage & Bind KB',
        icon: 'pi pi-book',
        command: () => this.openBindDialog(product),
      },
      {
        label: 'Edit Product',
        icon: 'pi pi-pencil',
        command: () => this.openEditDialog(product),
      },
      {
        separator: true,
      },
      {
        label: 'Delete Product',
        icon: 'pi pi-trash',
        styleClass: 'text-red-500',
        command: () => this.confirmDelete(product),
      },
    ];
    this.productActionMenu.toggle(event);
  }

  // -------------------------------------------------------------------------
  // KB Binding & Management Methods
  // -------------------------------------------------------------------------

  openBindDialog(product: Product): void {
    this.managingProduct = product;
    this.bindMode = 'existing';
    this.selectedExistingKbId = '';
    this.bindUploadFile = null;
    this.showBindDialog = true;
    this.loadCompanyKbDocs();
  }

  loadCompanyKbDocs(): void {
    this.isLoadingKbDocs = true;
    this.kbService.getDocuments().subscribe({
      next: (docs) => {
        this.companyKbDocs = docs || [];
        this.isLoadingKbDocs = false;
      },
      error: (err) => {
        this.isLoadingKbDocs = false;
        console.error('Failed to load KB documents', err);
      },
    });
  }

  get availableKbOptions(): { label: string; value: string }[] {
    if (!this.managingProduct) return [];
    const boundIds = new Set<string>();
    if (this.managingProduct.kb_document_id) {
      boundIds.add(this.managingProduct.kb_document_id);
    }
    if (this.managingProduct.bound_kb_documents) {
      this.managingProduct.bound_kb_documents.forEach((d) => boundIds.add(d.id));
    }
    return this.companyKbDocs
      .filter((d) => !boundIds.has(d.id))
      .map((d) => ({
        label: `${d.title} ${d.file_name ? '• ' + d.file_name : ''} (${d.chunk_count} chunks)`,
        value: d.id,
      }));
  }

  onBindFileSelected(event: any): void {
    const file = event.target?.files?.[0] || event.files?.[0];
    if (file) {
      if (file.size > 10 * 1024 * 1024) {
        this.toastService.error('File size exceeds the 10MB limit.');
        return;
      }
      this.bindUploadFile = file;
    }
  }

  removeBindUploadFile(): void {
    this.bindUploadFile = null;
  }

  bindSelectedExistingKb(): void {
    if (!this.managingProduct) return;
    if (!this.selectedExistingKbId) {
      this.toastService.warn('Please select a knowledge base document to bind.');
      return;
    }
    this.isBinding = true;
    this.productService.bindExistingKb(this.managingProduct.id, this.selectedExistingKbId).subscribe({
      next: (updatedProduct) => {
        this.isBinding = false;
        this.managingProduct = updatedProduct;
        this.toastService.success('Knowledge base successfully bound to product.');
        this.selectedExistingKbId = '';
        this.loadProducts();
      },
      error: (err) => {
        this.isBinding = false;
        const msg = err?.error?.detail || 'Failed to bind knowledge base.';
        this.toastService.error(msg);
      },
    });
  }

  uploadAndBindNewKb(): void {
    if (!this.managingProduct) return;
    if (!this.bindUploadFile) {
      this.toastService.warn('Please choose a file to upload and bind.');
      return;
    }
    this.isBinding = true;
    this.productService.uploadAndBindKb(this.managingProduct.id, this.bindUploadFile).subscribe({
      next: (updatedProduct) => {
        this.isBinding = false;
        this.managingProduct = updatedProduct;
        this.toastService.success(`"${this.bindUploadFile?.name}" indexed and bound to product.`);
        this.bindUploadFile = null;
        this.loadProducts();
      },
      error: (err) => {
        this.isBinding = false;
        const msg = err?.error?.detail || 'Failed to upload and bind knowledge base.';
        this.toastService.error(msg);
      },
    });
  }

  unbindKb(docId: string, docTitle?: string): void {
    if (!this.managingProduct) return;
    const title = docTitle || 'this knowledge base';
    this.confirmationService.confirmDelete(
      `Are you sure you want to unbind "${title}" from ${this.managingProduct.product_name}? (The document will remain in your company knowledge library)`,
      () => {
        if (!this.managingProduct) return;
        this.productService.unbindKb(this.managingProduct.id, docId).subscribe({
          next: (updatedProduct) => {
            this.managingProduct = updatedProduct;
            this.toastService.success('Knowledge base unbound from product.');
            this.loadProducts();
          },
          error: (err) => {
            const msg = err?.error?.detail || 'Failed to unbind document.';
            this.toastService.error(msg);
          },
        });
      },
    );
  }

  confirmDelete(product: Product): void {
    this.confirmationService.confirmDelete(
      `Are you sure you want to delete product "${product.product_name}"? This will also archive its indexed knowledge base notes.`,
      () => {
        this.productService.deleteProduct(product.id).subscribe({
          next: () => {
            this.toastService.success(`Product "${product.product_name}" deleted.`);
            this.loadProducts();
          },
          error: (err) => {
            const msg = err?.error?.detail || 'Failed to delete product.';
            this.toastService.error(msg);
          },
        });
      },
    );
  }

  getTypeSeverity(
    type: string,
  ): 'success' | 'secondary' | 'info' | 'warn' | 'danger' | 'contrast' | undefined {
    const map: Record<string, 'success' | 'secondary' | 'info' | 'warn' | 'danger' | 'contrast'> = {
      'Physical Product': 'success',
      'Digital Service': 'info',
      'SaaS / Software': 'contrast',
      'Financial / Loan': 'warn',
      'Real Estate / Property': 'secondary',
      'Subscription Plan': 'info',
    };
    return map[type] || 'secondary';
  }
}
