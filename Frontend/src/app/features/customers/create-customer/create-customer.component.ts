import { Component, EventEmitter, Input, OnInit, Output } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { CustomerService } from '../../../services/customer.service';
import { TeamManagementService } from '../../../services/team-management.service';
import { MessageService } from 'primeng/api';

@Component({
  selector: 'app-create-customer',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './create-customer.component.html',
  styleUrl: './create-customer.component.scss',
})
export class CreateCustomerComponent implements OnInit {
  @Input() visible = false;
  @Output() complete = new EventEmitter<void>();
  @Output() close = new EventEmitter<void>();

  // Form Fields
  displayName = '';
  phone = '';
  email = '';
  whatsapp = '';
  companyName = '';
  stage = 'customer';
  ownerEmail = '';
  product = '';
  value: number | null = null;
  currency = 'INR';
  source = '';
  tags = '';
  notes = '';
  birthday: Date | null = null;
  anniversary: Date | null = null;

  saving = false;
  employees: { label: string; value: string }[] = [];

  stageOptions = [
    { label: 'Customer', value: 'customer' },
    { label: 'New', value: 'new' },
    { label: 'Active', value: 'active' },
    { label: 'VIP', value: 'vip' },
    { label: 'Churned', value: 'churned' },
  ];

  currencyOptions = [
    { label: 'INR (₹)', value: 'INR' },
    { label: 'USD ($)', value: 'USD' },
    { label: 'EUR (€)', value: 'EUR' },
    { label: 'GBP (£)', value: 'GBP' },
  ];

  constructor(
    private customerService: CustomerService,
    private teamManagementService: TeamManagementService,
    private messageService: MessageService,
  ) {}

  ngOnInit(): void {
    this.loadEmployees();
  }

  loadEmployees(): void {
    this.teamManagementService.getEmployees().subscribe({
      next: (res) => {
        if (res && Array.isArray(res.items)) {
          this.employees = res.items.map((emp) => ({
            label: emp.name ? `${emp.name} (${emp.email})` : emp.email,
            value: emp.email,
          }));
        }
      },
      error: () => {
        this.employees = [];
      },
    });
  }

  get canSave(): boolean {
    return !!this.displayName.trim();
  }

  save(): void {
    if (!this.canSave) return;

    this.saving = true;
    const payload = {
      display_name: this.displayName.trim(),
      phone: this.phone.trim() || undefined,
      email: this.email.trim() || undefined,
      whatsapp: this.whatsapp.trim() || undefined,
      company_name: this.companyName.trim() || undefined,
      stage: this.stage,
      owner_email: this.ownerEmail || undefined,
      product: this.product.trim() || undefined,
      value: this.value !== null ? this.value : undefined,
      currency: this.currency,
      source: this.source.trim() || undefined,
      tags: this.tags.trim() || undefined,
      notes: this.notes.trim() || undefined,
      birthday: this.birthday ? this.birthday.toISOString() : undefined,
      anniversary: this.anniversary ? this.anniversary.toISOString() : undefined,
      fields: {},
    };

    this.customerService.createCustomer(payload).subscribe({
      next: () => {
        this.saving = false;
        this.messageService.add({
          severity: 'success',
          summary: 'Customer Created',
          detail: 'The customer has been manually created successfully.',
        });
        this.resetForm();
        this.complete.emit();
      },
      error: (err) => {
        this.saving = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: err.error?.detail || 'Failed to create customer. Please check your inputs.',
          life: 6000,
        });
      },
    });
  }

  resetForm(): void {
    this.displayName = '';
    this.phone = '';
    this.email = '';
    this.whatsapp = '';
    this.companyName = '';
    this.stage = 'customer';
    this.ownerEmail = '';
    this.product = '';
    this.value = null;
    this.currency = 'INR';
    this.source = '';
    this.tags = '';
    this.notes = '';
    this.birthday = null;
    this.anniversary = null;
  }

  onClose(): void {
    this.resetForm();
    this.close.emit();
  }
}
