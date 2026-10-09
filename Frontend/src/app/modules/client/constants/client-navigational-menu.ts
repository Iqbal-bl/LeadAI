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
    title: 'Leads',
    items: [
      {
        label: 'Leads List',
        icon: 'pi pi-users',
        routerLink: '/client/leads/list',
        permission: 'lead.read.all',
      },
      {
        label: 'Lead Batches',
        icon: 'pi pi-users',
        routerLink: '/client/leads/batches',
        permission: 'lead.read.all',
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
    title: 'Outreach & Campaigns',
    items: [
      {
        label: 'BroadCasts',
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
        label: 'Products',
        icon: 'pi pi-box',
        routerLink: '/client/products',
        permission: 'kb.read',
      },
      {
        label: 'Prompts',
        icon: 'pi pi-file-edit',
        routerLink: '/client/prompts',
        permission: 'prompt.read',
      },
      {
        label: 'AI Token Usage',
        icon: 'pi pi-chart-bar',
        routerLink: '/client/ai-usage',
        permission: 'analytics.read',
      },
    ],
  },
  {
    title: 'Management',
    items: [
      {
        label: 'Usage & Invoices',
        icon: 'pi pi-receipt',
        routerLink: '/client/usage',
      },
      {
        label: 'Profile',
        icon: 'pi pi-user',
        routerLink: '/client/profile',
        permission: '',
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
