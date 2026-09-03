import { Routes } from '@angular/router';

export const ADMIN_ROUTES: Routes = [
  { path: '', redirectTo: 'dashboard', pathMatch: 'full' },
  {
    path: 'dashboard',
    loadComponent: () =>
      import('./pages/admin-dashboard/admin-dashboard.component').then(
        (m) => m.AdminDashboardComponent,
      ),
  },
  {
    path: 'knowledge-base',
    loadComponent: () =>
      import('../client/pages/knowledge-base/knowledge-base.component').then(
        (m) => m.KnowledgeBaseComponent,
      ),
  },
  {
    path: 'prompts',
    loadComponent: () =>
      import('../../features/prompts/prompt-editor/prompt-editor.component').then(
        (m) => m.PromptEditorComponent,
      ),
  },
  {
    path: 'leads',
    children: [
      {
        path: '',
        loadComponent: () =>
          import('../../features/leads/lead-list/lead-list.component').then(
            (m) => m.LeadListComponent,
          ),
      },
      {
        path: 'detail/:id',
        loadComponent: () =>
          import('../../features/leads/lead-detail/lead-detail.component').then(
            (m) => m.LeadDetailComponent,
          ),
      },
    ],
  },
  {
    path: 'conversations',
    loadComponent: () =>
      import('../../features/conversations/conversations.component').then(
        (m) => m.ConversationsComponent,
      ),
  },
  {
    path: 'role-management',
    children: [
      {
        path: 'list',
        loadComponent: () =>
          import('./pages/role-management/roles-assigned/roles-assigned.component').then(
            (m) => m.RolesAssignedComponent,
          ),
      },
      {
        path: 'permissions',
        loadComponent: () =>
          import('./pages/role-management/permissions-matrix/permissions-matrix.component').then(
            (m) => m.PermissionsMatrixComponent,
          ),
      },
      {
        path: 'role-permissions',
        loadComponent: () =>
          import('./pages/role-management/role-permissions/role-permissions.component').then(
            (m) => m.RolePermissionsComponent,
          ),
      },
      {
        path: '',
        redirectTo: 'list',
        pathMatch: 'full',
      },
    ],
  },
  {
    path: 'clients',
    children: [
      {
        path: 'list',
        loadComponent: () =>
          import('./pages/client-management/client-list/client-list.component').then(
            (m) => m.ClientListComponent,
          ),
      },
      {
        path: 'detail/:id',
        loadComponent: () =>
          import('./pages/client-management/client-detail/client-detail.component').then(
            (m) => m.ClientDetailComponent,
          ),
      },
      {
        path: 'create',
        loadComponent: () =>
          import('./pages/client-management/create-update-client/create-update-client.component').then(
            (m) => m.CreateUpdateClientComponent,
          ),
      },
      {
        path: 'edit/:company_id',
        loadComponent: () =>
          import('./pages/client-management/create-update-client/create-update-client.component').then(
            (m) => m.CreateUpdateClientComponent,
          ),
      },
      {
        path: 'update/:clientId',
        loadComponent: () =>
          import('./pages/client-management/create-update-client/create-update-client.component').then(
            (m) => m.CreateUpdateClientComponent,
          ),
      },
      {
        path: ':id',
        loadComponent: () =>
          import('./pages/client-management/client-detail/client-detail.component').then(
            (m) => m.ClientDetailComponent,
          ),
      },
      {
        path: '',
        redirectTo: 'list',
        pathMatch: 'full',
      },
    ],
  },
  {
    path: 'billing',
    loadComponent: () =>
      import('../../features/billing/billing-dashboard/billing-dashboard.component').then(
        (m) => m.BillingDashboardComponent,
      ),
  },
  {
    path: 'plan-management',
    loadComponent: () =>
      import('../../features/admin/plan-management/plan-management.component').then(
        (m) => m.PlanManagementComponent,
      ),
  },
];

