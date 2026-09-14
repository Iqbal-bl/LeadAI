import { SidebarSection } from '../../../services/layout.service';

export const AdminNavigationalMenu: SidebarSection[] = [
  {
    title: 'Applications',
    items: [
      {
        label: 'Dashboard',
        icon: 'pi pi-users',
        routerLink: '/admin/dashboard',
      },
    ],
  },
  //   {
  //     title: 'AI Workflows',
  //     items: [
  //       {
  //         label: 'AI Assistant',
  //         icon: 'pi pi-microchip-ai',
  //         routerLink: '/admin/ai-assistant',
  //       },
  //       {
  //         label: 'Knowledge Base',
  //         icon: 'pi pi-book',
  //         routerLink: '/admin/knowledge-base',
  //       },
  //       {
  //         label: 'Prompts',
  //         icon: 'pi pi-book',
  //         routerLink: '/admin/prompts',
  //       },
  //     ],
  //   },
  {
    title: 'Client Management',
    items: [
      {
        label: 'Clients List',
        icon: 'pi pi-users',
        routerLink: '/admin/clients/list',
      },
      {
        label: 'Create Client',
        icon: 'pi pi-user-plus',
        routerLink: '/admin/clients/create',
      },
      // { label: 'Companies', icon: 'pi pi-cog', routerLink: '/admin/companies' },
    ],
  },
  {
    title: 'Role Management',
    items: [
      {
        label: 'Role Permissions',
        icon: 'pi pi-key',
        routerLink: '/admin/role-management/role-permissions',
      },
      {
        label: 'Permissions Matrix',
        icon: 'pi pi-table',
        routerLink: '/admin/role-management/permissions',
      },
      {
        label: 'Role Client Assignments',
        icon: 'pi pi-users',
        routerLink: '/admin/role-management/list',
      },
    ],
  },
  {
    title: 'Billing & Monetization',
    items: [
      {
        label: 'Plan Management',
        icon: 'pi pi-key',
        routerLink: '/admin/plan-management',
      },
      {
        label: 'Client Billing Summaries',
        icon: 'pi pi-credit-card',
        routerLink: '/admin/billing',
      },
    ],
  },
];
