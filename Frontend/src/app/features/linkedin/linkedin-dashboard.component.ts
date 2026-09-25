import { Component, OnInit, OnDestroy } from '@angular/core';
import { SharedModule } from '../../shared/shared.module';
import { LinkedinService } from '../../services/linkedin.service';
import {
  LinkedInStatus,
  LinkedInProfile,
  LinkedInCredentialsPayload,
  LinkedInInvitationItem,
  LinkedInConversation,
  LinkedInMessage,
} from '../../models/linkedin.models';
import { MessageService } from 'primeng/api';
import { ConfirmationService } from '../../shared/services/confirmation.service';

@Component({
  selector: 'app-linkedin-dashboard',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './linkedin-dashboard.component.html',
  styleUrl: './linkedin-dashboard.component.scss',
})
export class LinkedinDashboardComponent implements OnInit, OnDestroy {
  // Tab State
  activeTab: string | number = 'connection';

  // LinkedIn OAuth Status
  status: LinkedInStatus | null = null;
  statusLoading = false;
  oauthLoading = false;
  private pollingInterval: any = null;
  private messageListener: any = null;

  // Bot Session Credentials (Cookie or Email & Password)
  authMode: 'cookie' | 'credentials' = 'cookie';
  credentialsForm: LinkedInCredentialsPayload = {
    cookie_li_at: '',
    username: '',
    password: '',
  };
  savingCredentials = false;
  showCredentialsSuccess = false;

  // Auto-Accept & Automation Settings
  autoAcceptEnabled = false;
  welcomeMessage =
    'Hi {name},\n\nThanks for connecting! Looking forward to staying in touch and exploring potential collaborations.';
  savingSettings = false;
  syncingInvitations = false;

  // Received Invitations (Accepting)
  invitations: LinkedInInvitationItem[] = [];
  loadingInvitations = false;
  acceptingAll = false;

  // AI Boolean Keyword Search
  aiPrompt = 'Senior React & Node.js Developers in Bengaluru';
  generatedKeywords = '';
  isGeneratingKeywords = false;
  searchLimit = 10;
  isSearchingProfiles = false;
  profiles: LinkedInProfile[] = [];
  selectAllChecked = false;

  // Outreach & Invitations
  invitationMessage =
    'Hi {name},\n\nI came across your profile and was really impressed by your background. Would love to connect and stay in touch!';
  isSendingInvitations = false;
  invitationResults: Record<string, { success: boolean; message: string }> | null = null;
  showResultsModal = false;

  // Direct Messaging & InMail
  conversations: LinkedInConversation[] = [];
  selectedConversation: LinkedInConversation | null = null;
  messages: LinkedInMessage[] = [];
  loadingConversations = false;
  loadingMessages = false;
  sendingMessage = false;
  replyMessageText = '';
  syncingMessages = false;
  searchConversationText = '';

  // Credentials Form State
  showAdvancedCookie = false;

  constructor(
    private linkedinService: LinkedinService,
    private messageService: MessageService,
    private confirmationService: ConfirmationService
  ) {}

  ngOnInit(): void {
    this.loadStatus();
    this.setupOAuthMessageListener();
  }

  ngOnDestroy(): void {
    this.clearPolling();
    if (this.messageListener) {
      window.removeEventListener('message', this.messageListener);
    }
  }

  // --- OAuth 2.0 Connection ---
  loadStatus(): void {
    this.statusLoading = true;
    this.linkedinService.getStatus().subscribe({
      next: (res) => {
        this.status = res;
        this.statusLoading = false;
        if (res) {
          this.autoAcceptEnabled = !!res['auto_accept'];
          if (res['welcome_message']) {
            this.welcomeMessage = res['welcome_message'];
          }
          if (res.connected && res['has_cookie_credentials']) {
            this.loadInvitations();
            this.loadConversations();
          }
        }
      },
      error: () => {
        this.status = null;
        this.statusLoading = false;
      },
    });
  }

  private setupOAuthMessageListener(): void {
    this.messageListener = (event: MessageEvent) => {
      if (!event.data) return;

      if (event.data.type === 'LINKEDIN_OAUTH_SUCCESS') {
        this.oauthLoading = false;
        this.clearPolling();
        this.loadStatus();
        this.messageService.add({
          severity: 'success',
          summary: 'LinkedIn Connected',
          detail: 'OAuth authorization completed. You can now use LinkedIn posting and automation.',
        });
      }
    };
    window.addEventListener('message', this.messageListener);
  }

  connectOAuth(): void {
    this.oauthLoading = true;
    this.linkedinService.getConnectUrl().subscribe({
      next: (res) => {
        if (res?.authorize_url) {
          const width = 600;
          const height = 700;
          const left = window.screen.width / 2 - width / 2;
          const top = window.screen.height / 2 - height / 2;
          const popup = window.open(
            res.authorize_url,
            'linkedin-oauth',
            `width=${width},height=${height},left=${left},top=${top},scrollbars=yes,status=yes`
          );

          this.clearPolling();
          this.pollingInterval = setInterval(() => {
            this.linkedinService.getStatus().subscribe({
              next: (status) => {
                if (status?.connected) {
                  this.clearPolling();
                  this.oauthLoading = false;
                  this.status = status;
                  if (popup && !popup.closed) {
                    popup.close();
                  }
                  this.messageService.add({
                    severity: 'success',
                    summary: 'Connected to LinkedIn',
                    detail: `Account linked successfully (${status.person_urn || 'Profile'}).`,
                  });
                  this.loadStatus();
                }
              },
            });
          }, 3000);
        } else {
          this.oauthLoading = false;
        }
      },
      error: (err) => {
        this.oauthLoading = false;
        this.messageService.add({
          severity: 'error',
          summary: 'OAuth Failed',
          detail: err?.error?.detail || err?.message || 'Could not initiate LinkedIn connection.',
        });
      },
    });
  }

  disconnectProfile(): void {
    this.confirmationService.confirm({
      message: 'Are you sure you want to disconnect this LinkedIn profile and clear bot sessions?',
      header: 'Disconnect LinkedIn',
      icon: 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: 'p-button-danger',
      accept: () => {
        this.linkedinService.disconnect().subscribe({
          next: () => {
            this.messageService.add({
              severity: 'success',
              summary: 'Disconnected',
              detail: 'LinkedIn profile disconnected successfully.',
            });
            this.status = { connected: false };
            this.profiles = [];
            this.invitations = [];
            this.invitationResults = null;
          },
          error: (err) => {
            this.messageService.add({
              severity: 'error',
              summary: 'Error',
              detail: err?.error?.detail || 'Failed to disconnect profile.',
            });
          },
        });
      },
    });
  }

  private clearPolling(): void {
    if (this.pollingInterval) {
      clearInterval(this.pollingInterval);
      this.pollingInterval = null;
    }
  }

  // --- Bot Automation Credentials ---
  saveBotCredentials(): void {
    const hasCookie = !!this.credentialsForm.cookie_li_at?.trim();
    const hasCreds = !!this.credentialsForm.username?.trim() && !!this.credentialsForm.password?.trim();

    if (!hasCookie && !hasCreds) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Missing Credentials',
        detail: 'Please enter your LinkedIn email and password.',
      });
      return;
    }

    const payload: LinkedInCredentialsPayload = {
      cookie_li_at: this.credentialsForm.cookie_li_at?.trim() || null,
      username: this.credentialsForm.username?.trim() || null,
      password: this.credentialsForm.password?.trim() || null,
    };

    this.savingCredentials = true;
    this.linkedinService.saveCredentials(payload).subscribe({
      next: () => {
        this.savingCredentials = false;
        this.showCredentialsSuccess = true;
        this.messageService.add({
          severity: 'success',
          summary: 'Credentials Saved',
          detail: 'LinkedIn session credentials configured successfully.',
        });
        this.loadStatus();
        this.loadConversations();
        this.loadInvitations();
      },
      error: (err) => {
        this.savingCredentials = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Failed to Save Credentials',
          detail: err?.error?.detail || 'Error saving LinkedIn credentials.',
        });
      },
    });
  }

  disconnectBotCredentials(): void {
    this.confirmationService.confirm({
      message: 'Are you sure you want to remove your personal LinkedIn session token/credentials?',
      header: 'Remove Session Credentials',
      icon: 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: 'p-button-danger',
      accept: () => {
        this.linkedinService.disconnectCredentials().subscribe({
          next: () => {
            this.credentialsForm = { cookie_li_at: '', username: '', password: '' };
            this.showCredentialsSuccess = false;
            this.conversations = [];
            this.selectedConversation = null;
            this.messages = [];
            this.invitations = [];
            this.messageService.add({
              severity: 'success',
              summary: 'Credentials Removed',
              detail: 'Personal LinkedIn session and credentials removed successfully.',
            });
            this.loadStatus();
          },
          error: (err) => {
            this.messageService.add({
              severity: 'error',
              summary: 'Error',
              detail: err?.error?.detail || 'Failed to remove credentials.',
            });
          },
        });
      },
    });
  }


  // --- Auto-Accept & Automation Settings ---
  saveAutomationSettings(): void {
    this.savingSettings = true;
    this.linkedinService
      .saveSettings({
        auto_accept: this.autoAcceptEnabled,
        welcome_message: this.welcomeMessage.trim() || null,
      })
      .subscribe({
        next: () => {
          this.savingSettings = false;
          this.messageService.add({
            severity: 'success',
            summary: 'Settings Saved',
            detail: 'LinkedIn auto-accept and welcome messaging rules updated.',
          });
          this.loadStatus();
        },
        error: (err) => {
          this.savingSettings = false;
          this.messageService.add({
            severity: 'error',
            summary: 'Save Failed',
            detail: err?.error?.detail || 'Failed to save automation settings.',
          });
        },
      });
  }

  triggerSync(): void {
    this.syncingInvitations = true;
    this.linkedinService.syncInvitations().subscribe({
      next: (res) => {
        this.syncingInvitations = false;
        this.messageService.add({
          severity: 'info',
          summary: 'Sync Queued',
          detail: res.message || 'Background sync of connection requests started.',
        });
        setTimeout(() => this.loadInvitations(), 3000);
      },
      error: (err) => {
        this.syncingInvitations = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Sync Failed',
          detail: err?.error?.detail || 'Could not queue background sync.',
        });
      },
    });
  }

  // --- Received Invitations Management ---
  loadInvitations(): void {
    if (!this.status?.connected || !this.status?.['has_cookie_credentials']) {
      return;
    }
    this.loadingInvitations = true;
    this.linkedinService.getInvitations(50).subscribe({
      next: (res) => {
        this.loadingInvitations = false;
        this.invitations = res.invitations || [];
      },
      error: (err) => {
        this.loadingInvitations = false;
        this.messageService.add({
          severity: 'warn',
          summary: 'Could Not Load Invitations',
          detail:
            err?.error?.detail ||
            'Unable to fetch pending invitations. Please check your bot credentials.',
        });
      },
    });
  }

  acceptInvitation(item: LinkedInInvitationItem): void {
    item.processing = true;
    this.linkedinService
      .replyInvitation({
        invitation_urn: item.invitation_urn,
        shared_secret: item.shared_secret,
        action: 'accept',
        sender_name: item.name,
        sender_urn: item.sender_urn,
        public_id: item.public_id,
      })
      .subscribe({
        next: () => {
          item.processing = false;
          this.invitations = this.invitations.filter(
            (i) => i.invitation_urn !== item.invitation_urn
          );
          this.messageService.add({
            severity: 'success',
            summary: 'Invitation Accepted',
            detail: `Accepted connection request from ${item.name}. Lead created in CRM.`,
          });
        },
        error: (err) => {
          item.processing = false;
          this.messageService.add({
            severity: 'error',
            summary: 'Accept Failed',
            detail: err?.error?.detail || 'Failed to accept LinkedIn invitation.',
          });
        },
      });
  }

  rejectInvitation(item: LinkedInInvitationItem): void {
    this.confirmationService.confirm({
      message: `Are you sure you want to decline the connection request from ${item.name}?`,
      header: 'Decline Invitation',
      icon: 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: 'p-button-danger',
      accept: () => {
        item.processing = true;
        this.linkedinService
          .replyInvitation({
            invitation_urn: item.invitation_urn,
            shared_secret: item.shared_secret,
            action: 'reject',
          })
          .subscribe({
            next: () => {
              item.processing = false;
              this.invitations = this.invitations.filter(
                (i) => i.invitation_urn !== item.invitation_urn
              );
              this.messageService.add({
                severity: 'info',
                summary: 'Invitation Declined',
                detail: `Declined connection request from ${item.name}.`,
              });
            },
            error: (err) => {
              item.processing = false;
              this.messageService.add({
                severity: 'error',
                summary: 'Decline Failed',
                detail: err?.error?.detail || 'Failed to decline invitation.',
              });
            },
          });
      },
    });
  }

  acceptAllInvitations(): void {
    if (this.invitations.length === 0) return;

    this.confirmationService.confirm({
      message: `Accept all ${this.invitations.length} pending received connection requests and add them as leads?`,
      header: 'Accept All Invitations',
      icon: 'pi pi-check-circle',
      acceptButtonStyleClass: 'p-button-success',
      accept: () => {
        this.acceptingAll = true;
        this.linkedinService.acceptAllInvitations().subscribe({
          next: (res) => {
            this.acceptingAll = false;
            this.messageService.add({
              severity: 'success',
              summary: 'Batch Acceptance Completed',
              detail: `Processed ${res.processed || 0} invitations, accepted ${res.accepted || 0}.`,
            });
            this.loadInvitations();
          },
          error: (err) => {
            this.acceptingAll = false;
            this.messageService.add({
              severity: 'error',
              summary: 'Batch Accept Failed',
              detail: err?.error?.detail || 'Failed to process batch invitations.',
            });
          },
        });
      },
    });
  }

  // --- AI Keyword Generation ---
  generateKeywords(): void {
    if (!this.aiPrompt.trim()) return;

    this.isGeneratingKeywords = true;
    this.linkedinService.generateKeywords(this.aiPrompt.trim()).subscribe({
      next: (res) => {
        this.isGeneratingKeywords = false;
        this.generatedKeywords = res.keywords || '';
        this.messageService.add({
          severity: 'success',
          summary: 'Keywords Generated',
          detail: 'Boolean search string built with AI.',
        });
      },
      error: (err) => {
        this.isGeneratingKeywords = false;
        this.messageService.add({
          severity: 'error',
          summary: 'AI Generation Failed',
          detail: err?.error?.detail || 'Could not generate boolean keywords.',
        });
      },
    });
  }

  // --- Candidate Search ---
  searchCandidates(): void {
    const query = this.generatedKeywords.trim() || this.aiPrompt.trim();
    if (!query) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Search Query Required',
        detail: 'Please enter or generate search keywords first.',
      });
      return;
    }

    this.isSearchingProfiles = true;
    this.selectAllChecked = false;
    this.linkedinService.searchProfiles(query, this.searchLimit).subscribe({
      next: (res) => {
        this.isSearchingProfiles = false;
        this.profiles = (res.profiles || []).map((p) => ({
          ...p,
          selected: false,
        }));
        this.messageService.add({
          severity: 'info',
          summary: 'Search Completed',
          detail: `Found ${this.profiles.length} profiles matching query.`,
        });
      },
      error: (err) => {
        this.isSearchingProfiles = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Search Failed',
          detail:
            err?.error?.detail ||
            'Failed to search candidate profiles. Verify bot session credentials.',
        });
      },
    });
  }

  toggleSelectAll(): void {
    this.selectAllChecked = !this.selectAllChecked;
    this.profiles.forEach((p) => (p.selected = this.selectAllChecked));
  }

  onProfileSelectChange(): void {
    this.selectAllChecked =
      this.profiles.length > 0 && this.profiles.every((p) => p.selected);
  }

  getSelectedCount(): number {
    return this.profiles.filter((p) => p.selected).length;
  }

  getSelectedProfiles(): LinkedInProfile[] {
    return this.profiles.filter((p) => p.selected);
  }

  // --- Send Invitations ---
  sendInvitations(): void {
    const selected = this.getSelectedProfiles();
    if (selected.length === 0) {
      this.messageService.add({
        severity: 'warn',
        summary: 'No Profiles Selected',
        detail: 'Please select at least one candidate profile from search results.',
      });
      return;
    }

    this.isSendingInvitations = true;
    this.invitationResults = null;

    const payload = {
      profiles: selected.map((p) => ({
        public_id: p.public_id,
        urn_id: p.urn_id,
        name: p.name,
      })),
      message: this.invitationMessage.trim(),
    };

    this.linkedinService.sendInvitations(payload).subscribe({
      next: (res) => {
        this.isSendingInvitations = false;
        this.invitationResults = res.results || {};
        this.showResultsModal = true;
        this.messageService.add({
          severity: 'success',
          summary: 'Invitations Dispatched',
          detail: `Processed ${Object.keys(this.invitationResults).length} invitation requests.`,
        });
      },
      error: (err) => {
        this.isSendingInvitations = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Invitation Error',
          detail: err?.error?.detail || 'Failed to dispatch connection requests.',
        });
      },
    });
  }

  insertTag(tag: string, target: 'outreach' | 'welcome' = 'outreach'): void {
    if (target === 'welcome') {
      this.welcomeMessage = (this.welcomeMessage || '') + ` {${tag}}`;
    } else {
      this.invitationMessage = (this.invitationMessage || '') + ` {${tag}}`;
    }
  }

  // --- Direct Messaging & InMail Methods ---
  loadConversations(): void {
    if (!this.status?.connected || !this.status?.['has_cookie_credentials']) {
      return;
    }
    this.loadingConversations = true;
    this.linkedinService.getConversations(30).subscribe({
      next: (res) => {
        this.loadingConversations = false;
        this.conversations = res.conversations || [];
        if (this.conversations.length > 0) {
          if (!this.selectedConversation) {
            this.selectConversation(this.conversations[0]);
          } else {
            const found = this.conversations.find(
              (c) =>
                (c.conversation_id && c.conversation_id === this.selectedConversation?.conversation_id) ||
                (c.conversation_urn && c.conversation_urn === this.selectedConversation?.conversation_urn)
            );
            if (found) {
              this.selectedConversation = found;
            }
          }
        }
      },
      error: (err) => {
        this.loadingConversations = false;
        this.messageService.add({
          severity: 'warn',
          summary: 'Could Not Load Messages',
          detail: err?.error?.detail || 'Unable to retrieve LinkedIn messages.',
        });
      },
    });
  }

  selectConversation(conv: LinkedInConversation): void {
    this.selectedConversation = conv;
    this.messages = [];
    this.loadingMessages = true;
    const convId = conv.conversation_id || conv.conversation_urn;
    this.linkedinService.getConversationMessages(convId).subscribe({
      next: (res) => {
        this.loadingMessages = false;
        this.messages = res.messages || [];
        this.scrollToBottom();
      },
      error: (err) => {
        this.loadingMessages = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Message Fetch Failed',
          detail: err?.error?.detail || 'Could not load conversation thread messages.',
        });
      },
    });
  }

  sendDirectReply(): void {
    if (!this.selectedConversation || !this.replyMessageText?.trim()) return;
    const text = this.replyMessageText.trim();
    const convId =
      this.selectedConversation.conversation_id || this.selectedConversation.conversation_urn;
    this.sendingMessage = true;

    this.linkedinService.sendMessage(convId, text).subscribe({
      next: () => {
        this.sendingMessage = false;
        this.replyMessageText = '';
        this.messages.push({
          text,
          sender_name: 'You',
          is_self: true,
          created_at: Date.now(),
        });
        if (this.selectedConversation) {
          this.selectedConversation.last_message = text;
          this.selectedConversation.last_activity_at = Date.now();
        }
        this.scrollToBottom();
        this.messageService.add({
          severity: 'success',
          summary: 'Message Sent',
          detail: `Reply delivered to ${this.selectedConversation?.contact_name || 'contact'}.`,
        });
      },
      error: (err) => {
        this.sendingMessage = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Send Failed',
          detail: err?.error?.detail || 'Failed to dispatch reply message.',
        });
      },
    });
  }

  triggerMessageSync(): void {
    this.syncingMessages = true;
    this.linkedinService.syncMessages().subscribe({
      next: (res) => {
        this.syncingMessages = false;
        this.messageService.add({
          severity: 'success',
          summary: 'Messages Synced',
          detail: `Synced ${res.synced_conversations || 0} conversations and ${res.synced_messages || 0} messages to CRM.`,
        });
        this.loadConversations();
      },
      error: (err) => {
        this.syncingMessages = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Sync Failed',
          detail: err?.error?.detail || 'Failed to sync LinkedIn messages.',
        });
      },
    });
  }

  getFilteredConversations(): LinkedInConversation[] {
    if (!this.searchConversationText?.trim()) {
      return this.conversations;
    }
    const q = this.searchConversationText.toLowerCase().trim();
    return this.conversations.filter(
      (c) =>
        (c.contact_name && c.contact_name.toLowerCase().includes(q)) ||
        (c.contact_headline && c.contact_headline.toLowerCase().includes(q)) ||
        (c.last_message && c.last_message.toLowerCase().includes(q))
    );
  }

  getUnreadConversationsCount(): number {
    return this.conversations.filter(
      (c) => !c.is_read || (c.unread_count && c.unread_count > 0)
    ).length;
  }

  private scrollToBottom(): void {
    setTimeout(() => {
      const chatContainer = document.getElementById('linkedin-chat-messages-container');
      if (chatContainer) {
        chatContainer.scrollTop = chatContainer.scrollHeight;
      }
    }, 60);
  }
}

