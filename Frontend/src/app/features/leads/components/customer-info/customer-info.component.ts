import { Component, Input, inject } from '@angular/core';
import { SharedModule } from '../../../../shared/shared.module';
import { CustomerService } from '../../../../services/customer.service';
import { InboxService } from '../../../../services/inbox.service';
import { ToastService } from '../../../../shared/services/toast.service';
import { ContactInfo } from '../../../../models/inbox.models';

@Component({
  selector: 'app-customer-info',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './customer-info.component.html',
})
export class CustomerInfoComponent {
  @Input() lead!: any;

  private customerService = inject(CustomerService);
  private inboxService = inject(InboxService);
  private toastService = inject(ToastService);

  converting = false;
  revealingContact = false;
  isContactRevealed = false;
  revealedContact: ContactInfo | null = null;
  originalPhone = '';
  originalEmail = '';

  getInitials(name: string): string {
    if (name) {
      return name
        .split(' ')
        .map((n) => n[0])
        .join('')
        .toUpperCase()
        .slice(0, 2);
    }
    return '';
  }

  toggleRevealContact(): void {
    if (!this.lead?.id) return;

    if (this.isContactRevealed) {
      // Toggle back to masked view
      this.isContactRevealed = false;
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

  convertToCustomer(): void {
    if (!this.lead?.id) return;
    this.converting = true;
    this.customerService.convertLead({ lead_id: this.lead.id }).subscribe({
      next: () => {
        this.converting = false;
        this.toastService.success(
          `${this.lead.name} has been successfully promoted to a Customer.`,
          'Lead Converted',
        );
        this.lead.leadStatus = 'converted'; // update state
      },
      error: (err) => {
        this.converting = false;
        this.toastService.error(
          err?.error?.detail || 'Failed to convert lead to customer.',
          'Conversion Failed',
        );
      },
    });
  }
}
