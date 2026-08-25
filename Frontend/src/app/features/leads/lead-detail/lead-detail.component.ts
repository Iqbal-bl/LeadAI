import { Component, OnInit, OnDestroy } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { ToastService } from '../../../shared/services/toast.service';
import { LeadService } from '../../../services/lead.service';
import { VoiceService } from '../../../services/voice.service';
import { AiSummary } from '../../../models/lead.models';
import { CallTranscript } from '../../../models/voice.models';
import { InboxService } from '../../../services/inbox.service';
import { RoleManagementService } from '../../../services/role-management.service';
import { AssignableUser } from '../../../models/auth.models';

import { SharedModule } from '../../../shared/shared.module';
import { CustomerInfoComponent } from '../components/customer-info/customer-info.component';
import { AiSummaryComponent } from '../components/ai-summary/ai-summary.component';
import { CallingPanelComponent } from '../components/calling-panel/calling-panel.component';
import { WorkerAssignmentComponent } from '../components/worker-assignment/worker-assignment.component';
import { LeadStatusBadgeComponent } from '../components/lead-status-badge/lead-status-badge.component';
import { LeadConversationsComponent } from '../components/lead-conversations/lead-conversations.component';

@Component({
  selector: 'app-lead-detail',
  standalone: true,
  imports: [
    SharedModule,
    CustomerInfoComponent,
    AiSummaryComponent,
    CallingPanelComponent,
    WorkerAssignmentComponent,
    LeadStatusBadgeComponent,
    LeadConversationsComponent,
  ],
  templateUrl: './lead-detail.component.html',
  styleUrl: './lead-detail.component.scss',
})
export class LeadDetailComponent implements OnInit, OnDestroy {
  lead: any = {};
  aiSummary!: AiSummary;
  conversations: any[] = [];
  assignableUsers: AssignableUser[] = [];

  sendingReply = false;
  private wsMsgSub?: Subscription;

  // Transcript Preview State
  transcriptVisible = false;
  transcriptLoading = false;
  activeTranscript: CallTranscript | null = null;

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private inboxService: InboxService,
    private leadService: LeadService,
    private voiceService: VoiceService,
    private toastService: ToastService,
    private roleManagementService: RoleManagementService,
  ) {}

  ngOnInit(): void {
    const id = this.route.snapshot.paramMap.get('id') || '';
    this.loadLeadDetail(id);
    this.setupWebsocket(id);
    this.loadAssignableUsers();
  }

  ngOnDestroy(): void {
    if (this.wsMsgSub) {
      this.wsMsgSub.unsubscribe();
    }
    this.leadService.disconnectConversation();
  }

  loadAssignableUsers(): void {
    this.roleManagementService.getAssignableUsers().subscribe({
      next: (users) => {
        this.assignableUsers = users || [];
      },
      error: (err) => {
        console.warn('Failed to load assignable users:', err);
      },
    });
  }

  assignAgent(email: string | null): void {
    if (!this.lead?.id) return;
    this.inboxService.assignLead(this.lead.id, email).subscribe({
      next: () => {
        this.toastService.success(
          email
            ? `Conversation assigned to ${email}`
            : 'Conversation unassigned',
          'Agent Assigned',
        );
        this.loadLeadDetail(this.lead.id);
      },
      error: (err) => {
        this.toastService.error(
          err?.message || 'Failed to update assignment.',
          'Assignment Failed',
        );
      },
    });
  }

  setupWebsocket(id: string): void {
    // Delegate WebSocket connection to global LeadService
    this.leadService.connectConversation(id);

    this.wsMsgSub = this.leadService.conversationMessages$.subscribe({
      next: (data: any) => {
        console.log('Conversation WS event received:', data);

        if (data && (data.type === 'status' || data.type === 'call_status')) {
          const callStatus = data.status;
          let systemText = '';
          switch (callStatus) {
            case 'initiated':
              systemText = 'Call initiated...';
              break;
            case 'ringing':
              systemText = 'Ringing...';
              break;
            case 'in-progress':
            case 'answered':
              systemText = 'Call connected (in progress)';
              break;
            case 'completed':
              systemText = 'Call completed';
              break;
            case 'no-answer':
              systemText = 'Call unanswered';
              break;
            case 'busy':
              systemText = 'Line busy';
              break;
            case 'failed':
              systemText = 'Call failed';
              break;
          }

          if (systemText) {
            this.conversations = [
              ...this.conversations,
              {
                id: 'status-' + new Date().getTime(),
                leadId: Number(id) || 1,
                leadName: this.lead?.name || 'Customer',
                type: 'System',
                status: 'Completed',
                startTime: data.timestamp || new Date().toISOString(),
                duration: '0:00',
                summary: systemText,
                confidence: 1.0,
                agent: 'System',
                callSid: data.call_sid || null,
                sender: 'system',
              },
            ];
          }
          return;
        }

        let msgPayload = data;
        if (data && data.type === 'message' && data.data) {
          msgPayload = data.data;
        } else if (data && data.message && typeof data.message === 'object') {
          msgPayload = data.message;
        }

        if (msgPayload) {
          const text =
            msgPayload.text || msgPayload.content || msgPayload.message;
          const msgId = msgPayload.id;

          if (text) {
            const rawSender = msgPayload.type || msgPayload.sender || 'ai';
            const mappedType =
              rawSender === 'customer' ||
              rawSender === 'user' ||
              rawSender === 'Human'
                ? 'Human'
                : 'AI';
            const mappedAgent =
              rawSender === 'agent' || rawSender === 'Agent' ? 'Agent' : 'AI';
            const startTime =
              msgPayload.timestamp ||
              msgPayload.created_at ||
              new Date().toISOString();
            const callSid = msgPayload.call_sid || msgPayload.callSid || null;

            // Check if a message with the same ID already exists to update it (live call transcription)
            const existingIdx = msgId
              ? this.conversations.findIndex((c) => c.id === msgId)
              : -1;

            if (existingIdx > -1) {
              // Update in place
              const updated = [...this.conversations];
              updated[existingIdx] = {
                ...updated[existingIdx],
                summary: text,
                startTime: startTime,
                type: mappedType,
                agent: mappedAgent,
                callSid: callSid,
                sender: rawSender,
              };
              this.conversations = updated;
            } else {
              // Check for duplicate messages in the current view
              const isDuplicate = this.conversations.some(
                (c) =>
                  c.summary === text &&
                  Math.abs(
                    new Date(c.startTime).getTime() -
                      new Date(startTime).getTime(),
                  ) < 5000,
              );

              if (!isDuplicate) {
                this.conversations = [
                  ...this.conversations,
                  {
                    id: msgId || this.conversations.length + 1,
                    leadId: Number(id) || 1,
                    leadName: this.lead?.name || 'Customer',
                    type: mappedType,
                    status: 'Completed',
                    startTime: startTime,
                    duration: '0:00',
                    summary: text,
                    confidence: msgPayload.confidence || 1.0,
                    agent: mappedAgent,
                    callSid: callSid,
                    sender: rawSender,
                  },
                ];
              }
            }
          }
        }
      },
      error: (err: any) => {
        console.warn('Conversation WS error:', err);
      },
    });
  }

  // ── Transcript Preview ──

  isCallMessage(msg: any): boolean {
    return !!msg.callSid;
  }

  openTranscript(msg: any): void {
    if (!msg.callSid) return;
    this.transcriptLoading = true;
    this.transcriptVisible = true;
    this.activeTranscript = null;

    this.voiceService.getCallTranscript(msg.callSid).subscribe({
      next: (transcript) => {
        this.activeTranscript = transcript;
        this.transcriptLoading = false;
      },
      error: (err) => {
        this.transcriptLoading = false;
        this.toastService.error(
          err?.error?.detail || 'Could not load transcript.',
          'Transcript Error',
        );
      },
    });
  }

  closeTranscript(): void {
    this.transcriptVisible = false;
    this.activeTranscript = null;
  }

  formatDuration(seconds: number): string {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}m ${secs.toString().padStart(2, '0')}s`;
  }

  // ── Reply ──

  sendReplyMessage(messageText: string): void {
    if (!messageText.trim() || !this.lead?.id) return;
    this.sendingReply = true;
    const currentMsg = messageText.trim();
    this.inboxService.replyLead(this.lead.id, currentMsg).subscribe({
      next: (res: any) => {
        this.sendingReply = false;
        this.toastService.success(
          'Your message was sent successfully.',
          'Reply Sent',
        );

        // Add to the message list locally to show prompt feedback immediately
        const isAlreadyAdded = this.conversations.some(
          (c) => c.summary === currentMsg,
        );
        if (!isAlreadyAdded) {
          this.conversations = [
            ...this.conversations,
            {
              id: this.conversations.length + 1,
              leadId: this.lead.id,
              leadName: this.lead.name,
              type: 'AI', // Set as staff/agent response
              status: 'Completed',
              startTime: new Date().toISOString(),
              duration: '0:00',
              summary: currentMsg,
              confidence: 1.0,
              agent: 'Agent',
              callSid: null,
            },
          ];
        }
        this.loadLeadDetail(this.lead.id);
      },
      error: (err: any) => {
        this.sendingReply = false;
        this.toastService.error(
          err?.message || 'Please try again.',
          'Failed to Send Reply',
        );
      },
    });
  }

  loadLeadDetail(id: string): void {
    this.inboxService.getLeadDetail(id).subscribe({
      next: (detail) => {
        const score = detail.lead?.score || 0;
        this.lead = {
          id: detail.id,
          name: detail.customer_name || detail.customer_ref,
          email: 'N/A',
          phone: detail.customer_phone_masked,
          company: detail.client_id || 'N/A',
          address: 'N/A',
          industry: detail.lead?.product || 'N/A',
          tags: detail.lead?.interest ? [detail.lead.interest] : [],
          leadScore: score,
          priority: score > 75 ? 'High' : score > 45 ? 'Medium' : 'Low',
          status:
            detail.status === 'needs_human'
              ? 'Assigned'
              : detail.status === 'open'
                ? 'New'
                : 'Closed',
          source: detail.channel || 'web',
          channel: detail.channel || 'web',
          assignedTo: detail.assigned_user_email || 'Unassigned',
          createdAt: detail.created_at,
          updatedAt: detail.last_message_at,
          avatar: '',
          leadStatus: detail.lead?.status || '',
        };

        this.aiSummary = {
          conversationSummary: detail.summary || 'No summary available.',
          highlights: [detail.next_step || 'No next steps suggested.'],
          keyRequirements: detail.lead?.timeline ? [detail.lead.timeline] : [],
          painPoints: [],
          budget: detail.lead?.budget || 'N/A',
          timeline: detail.lead?.timeline || 'N/A',
          decisionMaker: 'Yes',
          buyingIntent: score > 75 ? 'High' : score > 40 ? 'Medium' : 'Low',
        };

        this.conversations = (detail.messages || []).map(
          (m: any, idx: number) => ({
            id: idx + 1,
            leadId: Number(detail.id) || 1,
            leadName: this.lead.name,
            type: m.sender === 'customer' ? 'Human' : 'AI',
            status: 'Completed',
            startTime: m.created_at || detail.created_at,
            duration: '0:00',
            summary: m.content || m.message,
            confidence: m.confidence || 1.0,
            agent: m.sender === 'agent' ? 'Agent' : 'AI',
            callSid: m.call_sid || null,
            sender: m.sender,
          }),
        );
      },
      error: () => {},
    });
  }

  goBack(): void {
    this.router.navigate(['/client/leads']);
  }
}
