import { Routes } from '@angular/router';
import { AuthGuard } from './guards/auth.guard';
import { SUPER_ADMIN_GUARD } from './guards/role.guard';

export const routes: Routes = [
  {
    path: 'auth',
    loadChildren: () =>
      import('./features/auth/auth.routes').then((m) => m.AUTH_ROUTES),
  },
  {
    path: 'client',
    canActivate: [AuthGuard],
    loadChildren: () =>
      import('./modules/client/client.routes').then((m) => m.CLIENT_ROUTES),
  },
  {
    path: 'admin',
    loadComponent: () =>
      import('./modules/admin/shell/admin-shell/admin-shell.component').then(
        (m) => m.AdminShellComponent,
      ),
    canActivate: [AuthGuard, SUPER_ADMIN_GUARD],
    loadChildren: () =>
      import('./modules/admin/admin.routes').then((m) => m.ADMIN_ROUTES),
  },
  {
    path: 'review/:articleId',
    loadComponent: () =>
      import('./features/blog/review-portal/review-portal.component').then(
        (m) => m.ReviewPortalComponent,
      ),
  },
  {
    path: 'chat',
    loadComponent: () =>
      import('./modules/lead-generation/lead-generation.component').then(
        (m) => m.LeadGenerationComponent,
      ),
  },
  {
    path: '**',
    redirectTo: 'admin/dashboard',
  },
];
