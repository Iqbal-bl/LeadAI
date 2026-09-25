import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from '../services/auth.service';
import { inject } from '@angular/core';

export const SUPER_ADMIN_GUARD: CanActivateFn = (route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  console.log('super admin guard');
  
  if (auth.isSuperAdmin()) {
    return true;
  }

  router.navigate(['/client/dashboard']);
  return false;
};
