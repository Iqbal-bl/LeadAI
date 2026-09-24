import { SidebarSection } from '../../../services/layout.service';

export const ClientNavigationalMenu: SidebarSection[] = [
  {
    title: 'Applications',
    items: [
      {
        label: 'Dashboard',
        icon: 'pi pi-th-large',
        routerLink: '/client/dashboard',
        permission: 'analytics.read',
      },
      {
        label: 'Leads',
        icon: 'pi pi-users',
        routerLink: '/client/leads',
        permission: 'lead.read.all',
      },
      {
        label: 'Customers',
        icon: 'pi pi-id-card',
        routerLink: '/client/customers',
        permission: 'customer.read',
      },
      {
        label: 'Team',
        icon: 'pi pi-users',
        routerLink: '/client/team',
        permission: 'role.read',
      },
    ],
  },
  {
    title: 'Social Media & Content',
    items: [
      {
        label: 'Blog & Content Studio',
        icon: 'pi pi-book',
        routerLink: '/client/blog',
        permission: 'campaign.read',
      },
      {
        label: 'Social Media Analytics',
        icon: 'pi pi-chart-pie',
        routerLink: '/client/social-analytics',
        permission: 'analytics.read',
      },
      {
        label: 'Create a Post',
        icon: 'pi pi-send',
        routerLink: '/client/create-post',
        permission: 'campaign.manage',
      },
    ],
  },
  {
    title: 'Channels & Integrations',
    items: [
      {
        label: 'Channels',
        icon: 'pi pi-link',
        routerLink: '/client/channels',
        permission: 'channel.read',
      },
    ],
  },
  {
    title: 'Audiences',
    items: [
      {
        label: 'Contact Lists',
        icon: 'pi pi-list',
        routerLink: '/client/contact-lists',
        permission: 'campaign.manage',
      },
    ],
  },
  {
    title: 'Outreach & Campaigns',
    items: [
      {
        label: 'Campaigns',
        icon: 'pi pi-megaphone',
        routerLink: '/client/campaigns',
        permission: 'campaign.read',
      },
      {
        label: 'LinkedIn Automation',
        icon: 'pi pi-linkedin',
        routerLink: '/client/linkedin',
        permission: 'channel.read',
      },
    ],
  },
  {
    title: 'AI Workflows',
    items: [
      {
        label: 'Knowledge Base',
        icon: 'pi pi-book',
        routerLink: '/client/knowledge-base',
        permission: 'kb.read',
      },
      {
        label: 'Prompts',
        icon: 'pi pi-file-edit',
        routerLink: '/client/prompts',
        permission: 'prompt.read',
      },
    ],
  },
  {
    title: 'Files',
    items: [
      {
        label: 'Documents',
        icon: 'pi pi-folder-open',
        routerLink: '/client/documents',
        permission: 'file.read',
      },
    ],
  },
  {
    title: 'Management',
    items: [
      {
        label: 'Billing & Recharges',
        icon: 'pi pi-credit-card',
        routerLink: '/client/billing',
      },
      {
        label: 'Settings',
        icon: 'pi pi-cog',
        routerLink: '/client/settings',
        permission: 'settings.manage',
      },
    ],
  },
];
