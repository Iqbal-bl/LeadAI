export interface Notification {
  id: number;
  type: 'ai-alert' | 'new-lead' | 'missed-call' | 'worker-activity' | 'kb-update';
  title: string;
  message: string;
  time: string;
  read: boolean;
  icon: string;
  severity: 'info' | 'success' | 'warn' | 'danger';
}
