import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { of } from 'rxjs';
import { catchError, map } from 'rxjs/operators';
import { AuthService } from '../services/auth.service';
import { BillingService } from '../services/billing.service';

/**
 * SubscriptionGuard ensures that company-scoped users cannot access the LeadAI application
 * unless their company has an active paid subscription plan.
 *
 * Enforcement Rules:
 * 1. Platform / Super Admins bypass subscription checks (they manage the entire platform).
 * 2. If the company user has an active subscription, access is granted.
 * 3. If the company has NOT selected or purchased a plan:
 *    - Redirects directly to /plans (Plans & Pricing component)
 *    - Access to all client application views is blocked for both the company admin
 *      and its associated team members until a subscription is bought.
 */
export const SubscriptionGuard: CanActivateFn = (route, state) => {
  const authService = inject(AuthService);
  const billingService = inject(BillingService);
  const router = inject(Router);

  // 1. Super admin / Platform admin bypass
  if (authService.isSuperAdmin() || authService.isPlatformAdmin()) {
    return true;
  }

  const currentUser = authService.getCurrentUser();

  // 2. If user state is already in memory with subscription flag
  if (currentUser && currentUser.has_active_subscription !== undefined) {
    if (currentUser.has_active_subscription) {
      return true;
    }
    router.navigate(['/plans']);
    return false;
  }

  // 3. Otherwise fetch /access/me or fallback to /billing/current-plan
  return authService.getAccessMe().pipe(
    map((user) => {
      if (authService.isSuperAdmin() || authService.isPlatformAdmin()) {
        return true;
      }
      if (user.has_active_subscription) {
        return true;
      }
      router.navigate(['/plans']);
      return false;
    }),
    catchError(() => {
      // Fallback check against billing/current-plan
      return billingService.getCurrentPlan().pipe(
        map((summary) => {
          if (summary.active_recharge && summary.active_recharge.status === 'active') {
            return true;
          }
          router.navigate(['/plans']);
          return false;
        }),
        catchError(() => {
          router.navigate(['/plans']);
          return of(false);
        }),
      );
    }),
  );
};
