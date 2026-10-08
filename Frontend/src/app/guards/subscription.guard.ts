import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { of } from 'rxjs';
import { catchError, map, switchMap } from 'rxjs/operators';
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
  if (authService.isPlatformAdmin()) {
    return true;
  }

  // 2. Check /access/me and fallback to /billing/current-plan
  return authService.getAccessMe().pipe(
    switchMap((user) => {
      if (
        authService.isSuperAdmin() ||
        authService.isPlatformAdmin() ||
        user.has_active_subscription
      ) {
        return of(true);
      }
      return billingService.getCurrentPlan().pipe(
        map((summary) => {
          const hasPlan = !!(
            summary?.active_recharge &&
            (summary.active_recharge.status === 'active' ||
              summary.active_recharge.status === 'exhausted')
          );
          if (hasPlan) {
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
    catchError(() => {
      return billingService.getCurrentPlan().pipe(
        map((summary) => {
          const hasPlan = !!(
            summary?.active_recharge &&
            (summary.active_recharge.status === 'active' ||
              summary.active_recharge.status === 'exhausted')
          );
          if (hasPlan) {
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
