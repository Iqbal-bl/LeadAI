import {
  Component,
  Input,
  OnInit,
  OnChanges,
  SimpleChanges,
  inject,
} from '@angular/core';
import { SharedModule } from '../../../../shared/shared.module';
import { CustomerService } from '../../../../services/customer.service';
import { InboxService } from '../../../../services/inbox.service';
import { ToastService } from '../../../../shared/services/toast.service';
import { AuthService } from '../../../../services/auth.service';
import { ContactInfo } from '../../../../models/inbox.models';

@Component({
  selector: 'app-customer-info',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './customer-info.component.html',
})
export class CustomerInfoComponent implements OnInit, OnChanges {
  @Input() lead!: any;

  private customerService = inject(CustomerService);
  private inboxService = inject(InboxService);
  private toastService = inject(ToastService);
  private authService = inject(AuthService);

  converting = false;
  revealingContact = false;
  isContactRevealed = false;
  canRevealContact = false;
  revealedContact: ContactInfo | null = null;
  originalPhone = '';
  originalEmail = '';
  leadInitials = '';

  ngOnInit(): void {
    this.authService.currentUser$.subscribe((user) => {
      this.canRevealContact = this.authService.hasPermission('lead.reveal_pii');
    });
    this.computeLeadInitials();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['lead']) {
      this.computeLeadInitials();
    }
  }

  get leadScore(): number {
    return this.lead?.leadScore ?? this.lead?.lead_score ?? this.lead?.score ?? 0;
  }

  private computeLeadInitials(): void {
    const name = this.lead?.name || '';
    if (name) {
      this.leadInitials = name
        .split(' ')
        .map((n: string) => n[0])
        .join('')
        .toUpperCase()
        .slice(0, 2);
    } else {
      this.leadInitials = 'CU';
    }
  }

  toggleRevealContact(): void {
    if (!this.lead?.id) return;

    if (this.isContactRevealed) {
      // Toggle back to masked view - clear revealed contact from component memory
      this.isContactRevealed = false;
      this.revealedContact = null;
      if (this.originalPhone) {
        this.lead.phone = this.originalPhone;
      }
      if (this.originalEmail) {
        this.lead.email = this.originalEmail;
      }
      return;
    }

    // Save initial masked copies
    if (!this.originalPhone) {
      this.originalPhone = this.lead.phone || '';
    }
    if (!this.originalEmail) {
      this.originalEmail = this.lead.email || '';
    }

    this.revealingContact = true;
    this.inboxService.getLeadContact(this.lead.id).subscribe({
      next: (contact: ContactInfo) => {
        this.revealingContact = false;
        this.revealedContact = contact;
        this.isContactRevealed = true;

        if (contact.phone) {
          this.lead.phone = contact.phone;
        }
        if (contact.email) {
          this.lead.email = contact.email;
        }
        if (contact.whatsapp) {
          this.lead.whatsapp = contact.whatsapp;
        }
        if (contact.instagram) {
          this.lead.instagram = contact.instagram;
        }
        if (contact.linkedin) {
          this.lead.linkedin = contact.linkedin;
        }
        if (contact.display_name) {
          this.lead.display_name = contact.display_name;
        }
        this.toastService.success(
          contact.warning ||
            'Customer contact details revealed. This action has been logged.',
          'Contact Details Revealed',
        );
      },
      error: (err) => {
        this.revealingContact = false;
        this.isContactRevealed = false;
        const errorDetail =
          err?.error?.detail ||
          err?.message ||
          'You do not have administrative permission to reveal customer contact details.';
        this.toastService.error(errorDetail, 'Access Denied');
      },
    });
  }

  copyToClipboard(value: string | null | undefined, label: string): void {
    if (!value || value === 'N/A') return;
    navigator.clipboard.writeText(value).then(() => {
      this.toastService.success(`${label} copied to clipboard`, 'Copied');
    });
  }

  showConvertDialog = false;
  convertPayload: {
    conversation_id: string;
    lead_id: string;
    owner_email: string;
    stage: string;
    value: number | null;
    notes: string;
  } = {
    conversation_id: '',
    lead_id: '',
    owner_email: '',
    stage: 'customer',
    value: null,
    notes: '',
  };

  stageOptions = [
    { label: 'Customer', value: 'customer' },
    { label: 'Opportunity', value: 'opportunity' },
  ];

  openConvertDialog(): void {
    if (!this.lead) return;
    const convId = String(this.lead.id || this.lead.conversation_id || '');

    // Extract numeric budget value if present
    let numericValue: number | null = null;
    if (this.lead.budget) {
      const match = String(this.lead.budget).match(/[\d,.]+/);
      if (match) {
        const val = parseFloat(match[0].replace(/,/g, ''));
        if (!isNaN(val)) numericValue = val;
      }
    }

    const initialEmail =
      this.lead.assigned_user_email ||
      (this.lead.assignedTo && this.lead.assignedTo.includes('@')
        ? this.lead.assignedTo
        : '');

    this.convertPayload = {
      conversation_id: convId,
      lead_id: convId,
      owner_email: initialEmail,
      stage: 'customer',
      value: numericValue,
      notes: this.lead.summary
        ? `Summary: ${this.lead.summary.slice(0, 150)}...`
        : '',
    };
    this.showConvertDialog = true;
  }

  submitConvert(): void {
    if (!this.convertPayload.conversation_id) return;
    this.converting = true;

    const emailVal = this.convertPayload.owner_email?.trim();
    const validEmail = emailVal && emailVal.includes('@') ? emailVal : null;

    const payload = {
      conversation_id: this.convertPayload.conversation_id,
      lead_id: this.convertPayload.lead_id,
      owner_email: validEmail,
      stage: this.convertPayload.stage || 'customer',
      value:
        this.convertPayload.value != null
          ? Number(this.convertPayload.value)
          : null,
      notes: this.convertPayload.notes
        ? this.convertPayload.notes.trim()
        : null,
    };

    this.customerService.convertLead(payload).subscribe({
      next: () => {
        this.converting = false;
        this.showConvertDialog = false;
        this.toastService.success(
          `${this.lead.name || 'Lead'} has been successfully promoted to a Customer.`,
          'Lead Converted',
        );
        this.lead.leadStatus = 'converted';
        this.lead.status = 'CONVERTED';
      },
      error: (err) => {
        this.converting = false;
        this.toastService.error(
          err?.error?.detail ||
            err?.message ||
            'Failed to convert lead to customer.',
          'Conversion Failed',
        );
      },
    });
  }
}
