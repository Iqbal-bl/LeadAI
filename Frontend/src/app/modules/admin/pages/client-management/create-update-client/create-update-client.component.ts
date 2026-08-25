import {
  Component,
  EventEmitter,
  Input,
  OnInit,
  Output,
  inject,
} from '@angular/core';
import { FormBuilder, FormGroup, Validators } from '@angular/forms';
import { CompanyService } from '../../../../../services/company.service';
import { Company } from '../../../../../models/company.models';
import { MessageService } from 'primeng/api';
import { ActivatedRoute, Router } from '@angular/router';
import { AuthService } from '../../../../../services/auth.service';

import { SharedModule } from '../../../../../shared/shared.module';

@Component({
  selector: 'admin-create-update-client',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './create-update-client.component.html',
  styleUrl: './create-update-client.component.scss',
})
export class CreateUpdateClientComponent implements OnInit {
  @Input() company: Company | null = null;
  @Output() onSave = new EventEmitter<void>();
  @Output() onCancel = new EventEmitter<void>();

  private fb = inject(FormBuilder);
  private companyService = inject(CompanyService);
  private messageService = inject(MessageService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private authService = inject(AuthService);

  companyForm!: FormGroup;
  isEditMode = false;
  loading = false;
  loadingCompany = false;
  loadingError = false;
  companyId: string | null = null;

  ngOnInit(): void {
    this.route.params.subscribe((params) => {
      const companyIdParam = params['company_id'] || params['clientId'];
      if (companyIdParam) {
        this.isEditMode = true;
        this.companyId = companyIdParam;
        this.initForm();
        this.loadCompanyDetails();
      } else if (this.company) {
        this.isEditMode = true;
        this.companyId = this.company.id;
        this.initForm();
      } else {
        this.isEditMode = false;
        this.initForm();
      }
    });
  }

  private initForm(): void {
    if (this.isEditMode) {
      this.companyForm = this.fb.group({
        name: [
          this.company?.name || '',
          [Validators.required, Validators.minLength(2)],
        ],
        email: [this.company?.email || '', [Validators.required, Validators.email]],
        phone_number: [this.company?.phone_number || ''],
        description: [this.company?.description || ''],
        is_active: [this.company?.is_active ?? true],
      });
      if (this.companyId && !this.company) {
        this.companyForm.disable();
      }
    } else {
      this.companyForm = this.fb.group({
        name: ['', [Validators.required, Validators.minLength(2)]],
        email: ['', [Validators.required, Validators.email]],
        description: [''],
        admin_name: ['', Validators.required],
        admin_email: ['', [Validators.required, Validators.email]],
      });
    }
  }

  loadCompanyDetails(): void {
    if (!this.companyId) return;

    this.loadingCompany = true;
    this.loadingError = false;
    if (this.companyForm) {
      this.companyForm.disable();
    }

    const options: any = {};
    if (this.authService.isPlatformAdmin()) {
      options.params = { client_id: this.companyId };
    }

    this.companyService.getCompany(this.companyId, options).subscribe({
      next: (data) => {
        this.company = data;
        this.companyForm.patchValue({
          name: data.name,
          email: data.email,
          phone_number: data.phone_number,
          description: data.description,
          is_active: data.is_active,
        });
        this.companyForm.enable();
        this.loadingCompany = false;
      },
      error: (err) => {
        this.loadingCompany = false;
        this.loadingError = true;
        this.messageService.add({
          severity: 'error',
          summary: 'Error Loading Company',
          detail: err?.message || 'Failed to fetch company details',
        });
      },
    });
  }

  submitForm(): void {
    if (this.companyForm.invalid) {
      this.companyForm.markAllAsTouched();
      return;
    }

    this.loading = true;
    const formValue = { ...this.companyForm.value };

    if (typeof formValue.name === 'string') formValue.name = formValue.name.trim();
    if (typeof formValue.email === 'string') formValue.email = formValue.email.trim();
    if (typeof formValue.phone_number === 'string') formValue.phone_number = formValue.phone_number.trim();
    if (typeof formValue.description === 'string') formValue.description = formValue.description.trim();
    if (typeof formValue.admin_name === 'string') formValue.admin_name = formValue.admin_name.trim();
    if (typeof formValue.admin_email === 'string') formValue.admin_email = formValue.admin_email.trim();

    if (this.isEditMode && this.companyId) {
      const payload = {
        name: formValue.name,
        email: formValue.email,
        phone_number: formValue.phone_number,
        description: formValue.description,
        is_active: formValue.is_active,
      };

      const options: any = {};
      if (this.authService.isPlatformAdmin()) {
        options.params = { client_id: this.companyId };
      }

      this.companyService.updateCompany(this.companyId, payload, options).subscribe({
        next: () => {
          this.loading = false;
          this.companyForm.markAsPristine();
          this.messageService.add({
            severity: 'success',
            summary: 'Success',
            detail: 'Company updated successfully.',
          });
          if (this.onSave.observed) {
            this.onSave.emit();
          } else {
            this.router.navigate(['/admin/clients/list']);
          }
        },
        error: (err) => {
          this.loading = false;
          this.messageService.add({
            severity: 'error',
            summary: 'Error',
            detail: err?.message || 'Failed to update company.',
          });
        },
      });
    } else {
      this.companyService.createCompany(formValue).subscribe({
        next: () => {
          this.loading = false;
          this.messageService.add({
            severity: 'success',
            summary: 'Success',
            detail: 'Company created successfully',
          });
          if (this.onSave.observed) {
            this.onSave.emit();
          } else {
            this.router.navigate(['/admin/clients/list']);
          }
        },
        error: (err) => {
          this.loading = false;
          this.messageService.add({
            severity: 'error',
            summary: 'Error',
            detail: err?.message || 'Failed to create company',
          });
        },
      });
    }
  }

  cancel(): void {
    if (this.onCancel.observed) {
      this.onCancel.emit();
    } else {
      this.router.navigate(['/admin/clients/list']);
    }
  }
}
