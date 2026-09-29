import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule, ReactiveFormsModule } from '@angular/forms';
import { RouterModule, Router } from '@angular/router';
import { Subscription } from 'rxjs';

import { AuthService } from '../../services/auth.service';
import { BillingService } from '../../services/billing.service';
import { ToastService } from '../../shared/services/toast.service';
import { UserMe, UserProfileUpdatePayload } from '../../models/auth.models';
import { BillingSummary } from '../../models/billing.models';

// PrimeNG Modules
import { ButtonModule } from 'primeng/button';
import { InputTextModule } from 'primeng/inputtext';
import { TagModule } from 'primeng/tag';
import { AvatarModule } from 'primeng/avatar';
import { SkeletonModule } from 'primeng/skeleton';
import { ProgressBarModule } from 'primeng/progressbar';
import { TooltipModule } from 'primeng/tooltip';
import { DividerModule } from 'primeng/divider';

/**
 * ProfileComponent provides a self-service hub for any authenticated user.
 * 
 * Responsibilities:
 * 1. Displays personal identity, assigned roles, company context, and permissions.
 * 2. Provides an interactive form allowing users to update their profile (Full Name, Phone, Timezone).
 * 3. Shows the current active company plan, AutoPay mandate status, remaining minutes, and active channels.
 */
@Component({
  selector: 'app-profile',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ReactiveFormsModule,
    RouterModule,
    ButtonModule,
    InputTextModule,
    TagModule,
    AvatarModule,
    SkeletonModule,
    ProgressBarModule,
    TooltipModule,
    DividerModule,
  ],
  templateUrl: './profile.component.html',
  styleUrl: './profile.component.scss',
})
export class ProfileComponent implements OnInit, OnDestroy {
  private authService = inject(AuthService);
  private billingService = inject(BillingService);
  private toastService = inject(ToastService);
  private router = inject(Router);

  private subscriptions = new Subscription();

  /** Current logged-in user profile */
  public currentUser: UserMe | null = null;

  /** Active company billing and recharge summary */
  public billingSummary: BillingSummary | null = null;

  /** State flags */
  public isLoadingProfile: boolean = true;
  public isLoadingBilling: boolean = true;
  public isSavingProfile: boolean = false;

  /** Form state model */
  public form = {
    fullName: '',
    email: '',
    phone: '',
    timezone: 'IST (UTC+05:30)',
  };

  /** Local storage key for persistent client preferences */
  private readonly PROFILE_PREFS_KEY = 'leadai_profile_prefs';

  ngOnInit(): void {
    this.loadUserProfile();
    this.loadCompanyBillingSummary();
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }

  /**
   * Loads the current user's profile and initialises form fields.
   */
  private loadUserProfile(): void {
    this.isLoadingProfile = true;
    this.subscriptions.add(
      this.authService.currentUser$.subscribe({
        next: (user) => {
          if (user) {
            this.currentUser = user;
            this.form.fullName = user.full_name || '';
            this.form.email = user.email || '';
            this.restoreLocalPreferences(user.email);
            this.isLoadingProfile = false;
          }
        },
        error: (error) => {
          this.isLoadingProfile = false;
          this.toastService.error(
            error?.error?.detail || 'Failed to load user profile.',
            'Profile Error'
          );
        },
      })
    );

    // Refresh from API to ensure freshest data
    this.subscriptions.add(
      this.authService.getAccessMe().subscribe({
        error: (error) => {
          console.error('Failed to refresh user profile:', error);
        },
      })
    );
  }

  /**
   * Loads the active company plan, remaining minutes, and channel entitlements.
   */
  private loadCompanyBillingSummary(): void {
    this.isLoadingBilling = true;
    this.subscriptions.add(
      this.billingService.getCurrentPlan().subscribe({
        next: (summary) => {
          this.billingSummary = summary;
          this.isLoadingBilling = false;
        },
        error: (error) => {
          this.isLoadingBilling = false;
          // Non-fatal if company has no billing record yet
          console.warn('Unable to load company billing summary:', error);
        },
      })
    );
  }

  /**
   * Restores client-side preferences (such as phone and timezone) from localStorage.
   *
   * @param email User email used as key namespace
   */
  private restoreLocalPreferences(email: string): void {
    try {
      const stored = localStorage.getItem(`${this.PROFILE_PREFS_KEY}_${email.toLowerCase()}`);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (parsed.phone) this.form.phone = parsed.phone;
        if (parsed.timezone) this.form.timezone = parsed.timezone;
      }
    } catch {
      // Ignore parse errors
    }
  }

  /**
   * Persists client-side preferences in localStorage.
   *
   * @param email User email
   */
  private saveLocalPreferences(email: string): void {
    try {
      localStorage.setItem(
        `${this.PROFILE_PREFS_KEY}_${email.toLowerCase()}`,
        JSON.stringify({
          phone: this.form.phone,
          timezone: this.form.timezone,
        })
      );
    } catch {
      // Ignore quota errors
    }
  }

  /**
   * Submits self-service profile updates to the backend API.
   */
  public onSaveProfile(): void {
    if (!this.form.fullName.trim()) {
      this.toastService.warn('Please enter a valid full name.', 'Validation');
      return;
    }

    this.isSavingProfile = true;
    const payload: UserProfileUpdatePayload = {
      full_name: this.form.fullName.trim(),
      phone: this.form.phone.trim(),
      timezone: this.form.timezone,
    };

    this.subscriptions.add(
      this.authService.updateProfile(payload).subscribe({
        next: (updatedUser) => {
          this.isSavingProfile = false;
          this.currentUser = updatedUser;
          if (updatedUser.email) {
            this.saveLocalPreferences(updatedUser.email);
          }
          this.toastService.success(
            'Your profile details have been successfully updated.',
            'Profile Saved'
          );
        },
        error: (error) => {
          this.isSavingProfile = false;
          this.toastService.error(
            error?.error?.detail || 'Failed to update profile.',
            'Update Failed'
          );
        },
      })
    );
  }

  /**
   * Navigates to the usage / billing hub.
   */
  public navigateToUsage(): void {
    const isClient = this.router.url.startsWith('/client');
    this.router.navigate([isClient ? '/client/usage' : '/admin/billing']);
  }

  /**
   * Generates initials from the user's full name.
   */
  public getInitials(name?: string | null): string {
    if (!name) return 'U';
    const parts = name.trim().split(/\s+/);
    if (parts.length === 1) return parts[0].substring(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  /**
   * Computes human-readable severity for the role badge.
   */
  public getRoleSeverity(role?: string): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
    if (!role) return 'secondary';
    const r = role.toLowerCase();
    if (r.includes('admin')) return 'danger';
    if (r.includes('manager')) return 'warn';
    if (r.includes('operator')) return 'info';
    return 'success';
  }

  /**
   * Formats role title for display.
   */
  public formatRole(role?: string): string {
    if (!role) return 'Standard User';
    return role.replace(/_/g, ' ').toUpperCase();
  }

  /**
   * Checks whether a specific channel is active in the company plan.
   */
  public isChannelActive(channel: string): boolean {
    const active = this.billingSummary?.active_recharge?.active_channels || [];
    return active.map((c) => c.toLowerCase()).includes(channel.toLowerCase());
  }

  /**
   * Returns remaining minutes percentage relative to purchased minutes.
   */
  public getMinutesPercentage(): number {
    const active = this.billingSummary?.active_recharge;
    if (!active || !active.purchased_minutes || active.purchased_minutes <= 0) {
      return 0;
    }
    const pct = (active.remaining_minutes / active.purchased_minutes) * 100;
    return Math.min(100, Math.max(0, Math.round(pct)));
  }
}
