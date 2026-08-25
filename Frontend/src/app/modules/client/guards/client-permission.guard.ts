import { Injectable, inject } from '@angular/core';
import { CanActivate, ActivatedRouteSnapshot, Router } from '@angular/router';
import { AuthService } from '../../../services/auth.service';
import { ClientPermissionService } from '../services/client-permission.service';

@Injectable({
  providedIn: 'root',
})
export class ClientPermissionGuard implements CanActivate {
  private authService = inject(AuthService);
  private permissionService = inject(ClientPermissionService);
  private router = inject(Router);

  canActivate(route: ActivatedRouteSnapshot): boolean {
    const requiredPermission = route.data['permission'] as string;
    if (!requiredPermission) {
      return true;
    }

    const user = this.authService.getCurrentUser();
    const permissions = user?.permissions || [];
    console.log(requiredPermission);

    if (this.permissionService.hasPermission(permissions, requiredPermission)) {
      return true;
    }

    // Redirect to dashboard if unauthorized and not already on the dashboard route
    if (route.routeConfig?.path !== 'dashboard') {
      this.router.navigate(['/client/dashboard']);
    }
    return false;
  }
}
