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
  isRevealed = false;
  revealedPhone: string | null = null;
  revealedEmail: string | null = null;
  revealedWhatsApp: string | null = null;
  revealedSocials: any[] = [];
  revealLoading = false;

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private customerService: CustomerService,
    private authService: AuthService,
    private messageService: MessageService,
  ) { }

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
        this.isRevealed = true;
        this.revealedPhone = res.phone;
        this.revealedEmail = res.email;
        this.revealedWhatsApp = res.whatsapp ?? null;
        this.revealedSocials = res.social_identities ?? [];
        this.revealLoading = false;
        this.messageService.add({
          severity: 'info',
          summary: 'Contact Revealed',
          detail: 'This action has been audit-logged.',
          life: 3000,
        });
      },
      error: (err) => {
        this.revealLoading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: err?.error?.detail || 'Failed to reveal contact information.',
        });
      },
    });
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

  goBack(): void {
    this.router.navigate(['/client/customers']);
  }
}
