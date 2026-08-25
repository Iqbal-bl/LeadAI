import { Component, OnInit } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { SharedModule } from '../../../shared/shared.module';
import { CustomerService } from '../../../services/customer.service';
import { AuthService } from '../../../services/auth.service';
import { Customer, CustomerRevealResponse } from '../../../models/customer.models';
import { MessageService } from 'primeng/api';

@Component({
  selector: 'app-customer-detail',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './customer-detail.component.html',
  styleUrl: './customer-detail.component.scss',
})
export class CustomerDetailComponent implements OnInit {
  customer: Customer | null = null;
  loading = true;

  // Reveal state
  revealedPhone: string | null = null;
  revealedEmail: string | null = null;
  revealLoading = false;

  // Message form
  showMessageForm = false;
  messageChannel: 'whatsapp' | 'sms' | 'email' | 'voice' = 'whatsapp';
  messageText = '';
  sendingMessage = false;

  channelOptions: { label: string; value: 'whatsapp' | 'sms' | 'email' | 'voice'; icon: string; consentKey: string }[] = [
    { label: 'WhatsApp', value: 'whatsapp', icon: 'pi pi-whatsapp', consentKey: 'whatsapp_opt_in' },
    { label: 'SMS', value: 'sms', icon: 'pi pi-mobile', consentKey: 'sms_opt_in' },
    { label: 'Email', value: 'email', icon: 'pi pi-envelope', consentKey: 'email_opt_in' },
    { label: 'Voice', value: 'voice', icon: 'pi pi-phone', consentKey: 'voice_opt_in' },
  ];

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private customerService: CustomerService,
    private authService: AuthService,
    private messageService: MessageService,
  ) {}

  ngOnInit(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) {
      this.loadCustomer(id);
    }
  }

  loadCustomer(id: string): void {
    this.loading = true;
    this.customerService.getCustomer(id).subscribe({
      next: (customer) => {
        this.customer = customer;
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      },
    });
  }

  revealContact(): void {
    if (!this.customer) return;
    this.revealLoading = true;
    this.customerService.revealContact(this.customer.id).subscribe({
      next: (res: CustomerRevealResponse) => {
        this.revealedPhone = res.phone;
        this.revealedEmail = res.email;
        this.revealLoading = false;
        this.messageService.add({
          severity: 'info',
          summary: 'Contact Revealed',
          detail: 'This action has been audit-logged.',
          life: 3000,
        });
      },
      error: () => {
        this.revealLoading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Failed to reveal contact information.',
        });
      },
    });
  }

  hasConsent(channel: string): boolean {
    if (!this.customer?.consent) return false;
    if (this.customer.consent.do_not_disturb) return false;
    const map: Record<string, keyof typeof this.customer.consent> = {
      whatsapp: 'whatsapp_opt_in',
      sms: 'sms_opt_in',
      email: 'email_opt_in',
      voice: 'voice_opt_in',
    };
    return !!this.customer.consent[map[channel]];
  }

  sendMessage(): void {
    if (!this.customer || !this.messageText.trim()) return;

    this.sendingMessage = true;
    this.customerService.sendMessage(this.customer.id, {
      channel: this.messageChannel,
      message: this.messageText,
    }).subscribe({
      next: () => {
        this.sendingMessage = false;
        this.showMessageForm = false;
        this.messageText = '';
        this.messageService.add({
          severity: 'success',
          summary: 'Message Sent',
          detail: `Message sent via ${this.messageChannel}.`,
        });
      },
      error: (err) => {
        this.sendingMessage = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Send Failed',
          detail: err.error?.detail || 'Failed to send message.',
        });
      },
    });
  }

  goBack(): void {
    this.router.navigate(['/client/customers']);
  }
}
