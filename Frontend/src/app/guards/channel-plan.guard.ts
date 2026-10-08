import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from '../services/auth.service';

/**
 * ChannelPlanGuard prevents access to channel/feature routes (e.g. /client/linkedin, /client/blog)
 * unless the company's active plan includes that channel/feature add-on.
 * Platform and Super Admins bypass this check.
 */
export const ChannelPlanGuard: CanActivateFn = (route, state) => {
  const authService = inject(AuthService);
  const router = inject(Router);

  if (authService.isSuperAdmin() || authService.isPlatformAdmin()) {
    return true;
  }

  const requiredChannel = (route.data?.['requiredChannel'] || route.data?.['requiredFeature'] || '') as string;
  if (!requiredChannel) {
    return true;
  }

  if (authService.hasChannel(requiredChannel)) {
    return true;
  }

  // Redirect to billing dashboard so company admin can add the required channel add-on
  router.navigate(['/client/billing']);
  return false;
};
