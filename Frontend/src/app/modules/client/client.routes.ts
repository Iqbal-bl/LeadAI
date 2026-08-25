import { Routes } from '@angular/router';
import { ClientPermissionGuard } from './guards/client-permission.guard';

export const CLIENT_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./shell/client-shell.component').then(
        (c) => c.ClientShellComponent,
      ),
    children: [
      {
        path: 'dashboard',
        // canActivate: [ClientPermissionGuard],
        // data: { permission: 'analytics.read' },
        loadComponent: () =>
          import('./pages/client-dashboard/client-dashboard.component').then(
            (m) => m.ClientDashboardComponent,
          ),
      },
      // {
      //   path: 'companies',
      //   loadComponent: () =>
      //     import('../../features/companies/companies-list/companies-list.component').then(
      //       (m) => m.CompaniesListComponent,
      //     ),
      // },
      {
        path: 'leads',
        canActivate: [ClientPermissionGuard],
        data: { permission: 'lead.read.all' },
        children: [
          {
            path: '',
            loadComponent: () =>
              import('../../features/leads/lead-list/lead-list.component').then(
                (m) => m.LeadListComponent,
              ),
          },
          {
            path: ':id',
            loadComponent: () =>
              import('../../features/leads/lead-detail/lead-detail.component').then(
                (m) => m.LeadDetailComponent,
              ),
          },
        ],
      },
      {
        path: 'conversations',
        canActivate: [ClientPermissionGuard],
        data: { permission: 'lead.reply' },
        loadComponent: () =>
          import('../../features/conversations/conversations.component').then(
            (m) => m.ConversationsComponent,
          ),
      },
      // {
      //   path: 'ai-assistant',
      //   loadComponent: () =>
      //     import('../../features/ai-assistant/ai-assistant.component').then(
      //       (m) => m.AiAssistantComponent,
      //     ),
      // },
      {
        path: 'analytics',
        canActivate: [ClientPermissionGuard],
        data: { permission: 'analytics.read' },
        loadComponent: () =>
          import('../../features/analytics/analytics.component').then(
            (m) => m.AnalyticsComponent,
          ),
      },
      {
        path: 'team',
        canActivate: [ClientPermissionGuard],
        data: { permission: 'role.read' },
        loadComponent: () =>
          import('./pages/team-management/team-list/team-list.component').then(
            (m) => m.TeamListComponent,
          ),
      },
      {
        path: 'knowledge-base',
        canActivate: [ClientPermissionGuard],
        data: { permission: 'kb.read' },
        loadComponent: () =>
          import('./pages/knowledge-base/knowledge-base.component').then(
            (m) => m.KnowledgeBaseComponent,
          ),
      },
      {
        path: 'prompts',
        canActivate: [ClientPermissionGuard],
        data: { permission: 'prompt.read' },
        loadComponent: () =>
          import('../../features/prompts/prompt-editor/prompt-editor.component').then(
            (m) => m.PromptEditorComponent,
          ),
      },
      {
        path: 'settings',
        canActivate: [ClientPermissionGuard],
        data: { permission: 'settings.manage' },
        loadComponent: () =>
          import('../../features/settings/settings.component').then(
            (m) => m.SettingsComponent,
          ),
      },
      {
        path: 'channels',
        canActivate: [ClientPermissionGuard],
        data: { permission: 'channel.read' },
        loadComponent: () =>
          import('../../features/channels/channel-list/channel-list.component').then(
            (m) => m.ChannelListComponent,
          ),
      },
      {
        path: 'linkedin',
        canActivate: [ClientPermissionGuard],
        data: { permission: 'channel.read' },
        loadComponent: () =>
          import('../../features/linkedin/linkedin-dashboard.component').then(
            (m) => m.LinkedinDashboardComponent,
          ),
      },
      {
        path: 'contact-lists',
        canActivate: [ClientPermissionGuard],
        data: { permission: 'campaign.manage' },
        loadComponent: () =>
          import('../../features/contact-lists/contact-list-list/contact-list-list.component').then(
            (m) => m.ContactListListComponent,
          ),
      },
      {
        path: 'campaigns',
        canActivate: [ClientPermissionGuard],
        data: { permission: 'campaign.read' },
        children: [
          {
            path: '',
            loadComponent: () =>
              import('../../features/campaigns/campaign-list/campaign-list.component').then(
                (m) => m.CampaignListComponent,
              ),
          },
          {
            path: ':id',
            loadComponent: () =>
              import('../../features/campaigns/campaign-detail/campaign-detail.component').then(
                (m) => m.CampaignDetailComponent,
              ),
          },
        ],
      },
      {
        path: 'customers',
        canActivate: [ClientPermissionGuard],
        data: { permission: 'customer.read' },
        children: [
          {
            path: '',
            loadComponent: () =>
              import('../../features/customers/customer-list/customer-list.component').then(
                (m) => m.CustomerListComponent,
              ),
          },
          {
            path: ':id',
            loadComponent: () =>
              import('../../features/customers/customer-detail/customer-detail.component').then(
                (m) => m.CustomerDetailComponent,
              ),
          },
        ],
      },
      {
        path: 'create-post',
        loadComponent: () =>
          import('../../features/campaigns/pages/composer/composer').then(
            (m) => m.ComposerComponent,
          ),
      },
      {
        path: 'composer',
        redirectTo: 'create-post',
        pathMatch: 'full',
      },
      {
        path: 'social-analytics',
        loadComponent: () =>
          import('../../features/campaigns/pages/analytics-layout/analytics-layout').then(
            (m) => m.AnalyticsLayoutComponent,
          ),
      },
      {
        path: '',
        redirectTo: 'dashboard',
        pathMatch: 'full',
      },
    ],
  },
];
