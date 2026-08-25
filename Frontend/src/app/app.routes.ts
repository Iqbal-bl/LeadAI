import { Routes } from '@angular/router';
import { AuthGuard } from './guards/auth.guard';

export const routes: Routes = [
  {
    path: 'auth',
    children: [
      {
        path: 'callback',
        loadComponent: () =>
          import('./features/auth/callback/callback.component').then(
            (m) => m.CallbackComponent,
          ),
      },
    ],
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
    canActivate: [AuthGuard],
    loadChildren: () =>
      import('./modules/admin/admin.routes').then((m) => m.ADMIN_ROUTES),
  },
  {
    path: 'chat',
    loadComponent: () =>
      import('./modules/lead-generation/lead-generation.component').then(
        (m) => m.LeadGenerationComponent,
      ),
  },
  {
    path: 'review/:id',
    loadComponent: () =>
      import(
        './modules/blog/pages/article-reviewer/article-reviewer.component'
      ).then((m) => m.ArticleReviewerComponent),
  },
  {
    path: 'blog',
    canActivate: [AuthGuard],
    loadChildren: () =>
      import('./modules/blog/blog.module').then((m) => m.BlogModule),
  },
  {
    path: '**',
    redirectTo: 'admin/dashboard',
  },
];
