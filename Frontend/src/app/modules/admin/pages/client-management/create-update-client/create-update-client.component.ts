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

  availableServices = [
    { key: 'social.whatsapp', name: 'WhatsApp Business', icon: 'pi pi-whatsapp', desc: 'Meta Cloud API & auto-reply' },
    { key: 'social.facebook', name: 'Facebook Messenger', icon: 'pi pi-facebook', desc: 'Page messaging & ads chat' },
    { key: 'social.instagram', name: 'Instagram Direct', icon: 'pi pi-instagram', desc: 'DMs, Story replies & comments' },
    { key: 'voice_agent', name: 'AI Voice & Dialler', icon: 'pi pi-phone', desc: 'Outbound AI calls & transcriptions' },
    { key: 'social.linkedin', name: 'LinkedIn Outreach', icon: 'pi pi-linkedin', desc: 'B2B outreach & connection automation' },
    { key: 'email_marketing', name: 'Email Marketing', icon: 'pi pi-envelope', desc: 'Campaigns & automated sequences' },
  ];
  selectedServices: string[] = [
    'social.whatsapp',
    'social.facebook',
    'social.instagram',
    'voice_agent',
    'social.linkedin',
  ];

  toggleService(key: string): void {
    if (this.selectedServices.includes(key)) {
      this.selectedServices = this.selectedServices.filter((k) => k !== key);
    } else {
      this.selectedServices = [...this.selectedServices, key];
    }
  }

  isServiceSelected(key: string): boolean {
    return this.selectedServices.includes(key);
  }

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

    // Also load configured company permissions
    this.companyService.getCompanyPermissions(this.companyId, options).subscribe({
      next: (res) => {
        if (res?.permissions && res.permissions.length > 0) {
          this.selectedServices = res.permissions
            .filter((p) => p.is_enabled)
            .map((p) => p.key.toLowerCase());
        }
      },
      error: () => {},
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

      // Patch permissions in parallel
      this.companyService.patchCompanyPermissions(
        this.companyId,
        {
          permissions: this.availableServices.map((s) => ({
            key: s.key,
            is_enabled: this.selectedServices.includes(s.key),
          })),
        },
        options
      ).subscribe({ error: () => {} });

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
      // Create Mode with selected permissions array
      const createPayload = {
        name: formValue.name,
        email: formValue.email,
        description: formValue.description,
        admin_name: formValue.admin_name,
        admin_email: formValue.admin_email,
        permissions: this.selectedServices,
      };

      this.companyService.createCompany(createPayload).subscribe({
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
