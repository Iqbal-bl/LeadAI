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

  channelOptions: {
    label: string;
    value: 'whatsapp' | 'sms' | 'email' | 'voice';
    icon: string;
    key: 'opt_in_whatsapp' | 'opt_in_sms' | 'opt_in_email' | 'opt_in_call';
  }[] = [
    { label: 'WhatsApp', value: 'whatsapp', icon: 'pi pi-whatsapp', key: 'opt_in_whatsapp' },
    { label: 'SMS', value: 'sms', icon: 'pi pi-mobile', key: 'opt_in_sms' },
    { label: 'Email', value: 'email', icon: 'pi pi-envelope', key: 'opt_in_email' },
    { label: 'Call', value: 'voice', icon: 'pi pi-phone', key: 'opt_in_call' },
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
    if (!this.customer) return false;
    if (this.customer.do_not_disturb) return false;
    if (channel === 'whatsapp') return Boolean(this.customer.opt_in_whatsapp);
    if (channel === 'sms') return Boolean(this.customer.opt_in_sms);
    if (channel === 'email') return Boolean(this.customer.opt_in_email);
    if (channel === 'voice' || channel === 'call') return Boolean(this.customer.opt_in_call);
    return false;
  }

  getStageSeverity(stage: string | null | undefined): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
    const s = (stage || '').toLowerCase();
    const map: Record<string, 'success' | 'info' | 'warn' | 'danger' | 'secondary'> = {
      opportunity: 'info',
      new: 'info',
      active: 'success',
      customer: 'success',
      vip: 'warn',
      churned: 'danger',
    };
    return map[s] || 'secondary';
  }

  getSourceIcon(source: string | null | undefined): string {
    const s = (source || '').toLowerCase();
    const icons: Record<string, string> = {
      instagram: 'pi pi-instagram',
      facebook: 'pi pi-facebook',
      messenger: 'pi pi-comments',
      whatsapp: 'pi pi-whatsapp',
      linkedin: 'pi pi-linkedin',
      voice: 'pi pi-phone',
      call: 'pi pi-phone',
      web: 'pi pi-globe',
      email: 'pi pi-envelope',
      sms: 'pi pi-mobile',
    };
    return icons[s] || 'pi pi-share-alt';
  }

  formatCurrency(val: number | null | undefined, curr: string | null | undefined): string {
    const amount = Number(val ?? 0).toFixed(2);
    const currency = curr || 'INR';
    if (currency === 'INR') {
      return `₹${amount}`;
    }
    return `${amount} ${currency}`;
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
