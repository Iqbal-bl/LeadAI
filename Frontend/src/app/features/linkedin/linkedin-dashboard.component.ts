import { Component, OnInit, OnDestroy } from '@angular/core';
import { Subscription } from 'rxjs';
import { SharedModule } from '../../shared/shared.module';
import { LinkedinService } from '../../services/linkedin.service';
import {
  LinkedInStatus,
  LinkedInAccount,
  LinkedInProfile,
  LinkedInCredentialsPayload,
  LinkedInInvitationItem,
  LinkedInConversation,
  LinkedInMessage,
  LinkedInSocialComment,
  LinkedInCommentSettings,
  LinkedInAutoConnectSettings,
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
  private chatPollingInterval: any = null;
  private activeMessageSub?: Subscription;

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
  autoDmLeadsEnabled = true;
  welcomeMessage =
    'Hi {name},\n\nThanks for connecting! Looking forward to staying in touch and exploring potential collaborations.';
  savingSettings = false;
  syncingInvitations = false;

  // Received Invitations (Accepting)
  invitations: LinkedInInvitationItem[] = [];
  loadingInvitations = false;
  acceptingAll = false;

  // Auto-Pilot Outreach & Randomized Connection Scheduler
  autoConnectSettings: LinkedInAutoConnectSettings = {
    enabled: false,
    runs_per_day: 3,
    profiles_per_run: 5,
    target_prompt: 'Senior React & Node.js Developers in Bengaluru',
    target_keywords: '',
    custom_message: 'Hi {firstName}, I came across your profile and was really impressed by your background. Would love to connect!',
    active_hours_start: 9,
    active_hours_end: 19,
    last_run_at: null,
    next_run_at: null,
    total_sent_today: 0,
    total_sent_all_time: 0,
    last_run_status: null,
    last_run_detail: null,
  };
  loadingAutoConnect = false;
  savingAutoConnect = false;
  triggeringAutoConnect = false;
  isGeneratingAutoKeywords = false;

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
  invitationResults: Record<
    string,
    { success: boolean; message: string }
  > | null = null;
  showResultsModal = false;

  // Direct Messaging & InMail
  conversations: LinkedInConversation[] = [];
  selectedConversation: LinkedInConversation | null = null;
  messages: LinkedInMessage[] = [];
  loadingConversations = false;
  loadingMessages = false;
  syncingThreadMessages = false;
  loadingPreviousMessages = false;
  hasNoEarlierMessages = false;
  sendingMessage = false;
  replyMessageText = '';
  syncingMessages = false;
  searchConversationText = '';

  // Cached / State Properties (eliminating template function executions)
  selectedCount = 0;
  selectedProfiles: LinkedInProfile[] = [];
  unreadConversationsCount = 0;

  get connectedAccounts(): LinkedInAccount[] {
    return this.status?.accounts || [];
  }
  filteredConversations: LinkedInConversation[] = [];
  canSendReply = false;

  // Comments & AI Replies Automation State
  comments: LinkedInSocialComment[] = [];
  loadingComments = false;
  syncingComments = false;
  commentStatusFilter: string = 'all'; // 'all' | 'pending_review' | 'replied' | 'auto_replied' | 'leads'
  commentSearchText: string = '';
  selectedCommentSentiment: string = 'all';
  showCommentSettingsModal = false;
  savingCommentSettings = false;
  filteredComments: LinkedInSocialComment[] = [];
  pendingReviewCommentsCount = 0;
  capturedLeadsCount = 0;
  commentSettings: LinkedInCommentSettings = {
    is_auto_reply_enabled: false,
    require_approval_for_questions: true,
    reply_tone: 'thought_leadership',
    custom_instructions: '',
    signature_text: '',
    auto_capture_leads: true,
    min_lead_intent_threshold: 0.6,
    exclude_keywords: ['scam', 'spam', 'refund', 'fake', 'terrible', 'complaint'],
  };

  // Credentials Form State
  showAdvancedCookie = false;

  constructor(
    private linkedinService: LinkedinService,
    private messageService: MessageService,
    private confirmationService: ConfirmationService,
  ) {}

  ngOnInit(): void {
    this.loadStatus();
    this.setupOAuthMessageListener();
  }

  ngOnDestroy(): void {
    this.clearPolling();
    this.stopChatPolling();
    if (this.activeMessageSub) {
      this.activeMessageSub.unsubscribe();
      this.activeMessageSub = undefined;
    }
    if (this.messageListener) {
      window.removeEventListener('message', this.messageListener);
    }
  }

  onTabChange(tab: any): void {
    this.activeTab = tab;
    if (tab !== 'messages') {
      this.stopChatPolling();
      if (this.activeMessageSub) {
        this.activeMessageSub.unsubscribe();
        this.activeMessageSub = undefined;
        this.loadingMessages = false;
        this.syncingThreadMessages = false;
      }
    } else {
      if (this.conversations.length === 0) {
        this.loadConversations();
      } else if (this.selectedConversation) {
        const hasLoadedMessages = this.messages && this.messages.length > 0;
        this.selectConversation(this.selectedConversation, hasLoadedMessages);
      }
    }
    if (tab === 'comments' && this.comments.length === 0 && !this.loadingComments) {
      this.loadComments();
    }
    if (tab === 'search') {
      this.loadAutoConnectSettings();
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
          this.autoDmLeadsEnabled = res['auto_dm_leads'] !== false;
          if (res['welcome_message']) {
            this.welcomeMessage = res['welcome_message'];
          }
          if (res.connected && res['has_cookie_credentials']) {
            this.loadInvitations();
            this.loadConversations();
            this.loadCommentSettings();
            this.loadComments();
            this.loadAutoConnectSettings();
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
          detail:
            'OAuth authorization completed. You can now use LinkedIn posting and automation.',
        });
      }
    };
    window.addEventListener('message', this.messageListener);
  }

  connectOAuth(forceLogin: boolean = false): void {
    this.oauthLoading = true;
    const initialCount = this.status?.accounts?.length || 0;
    const promptParam = (this.status?.connected || forceLogin || initialCount > 0) ? 'login' : undefined;

    this.linkedinService.getConnectUrl(promptParam).subscribe({
      next: (res) => {
        if (res?.authorize_url) {
          const width = 600;
          const height = 700;
          const left = window.screen.width / 2 - width / 2;
          const top = window.screen.height / 2 - height / 2;
          const popup = window.open(
            res.authorize_url,
            'linkedin-oauth',
            `width=${width},height=${height},left=${left},top=${top},scrollbars=yes,status=yes`,
          );

          this.clearPolling();
          this.pollingInterval = setInterval(() => {
            this.linkedinService.getStatus().subscribe({
              next: (status) => {
                const currentCount = status?.accounts?.length || 0;
                if (status?.connected && (currentCount > initialCount || (initialCount === 0 && status.connected))) {
                  this.clearPolling();
                  this.oauthLoading = false;
                  this.status = status;
                  if (popup && !popup.closed) {
                    popup.close();
                  }
                  const newestAccount = status.accounts?.[status.accounts.length - 1];
                  this.messageService.add({
                    severity: 'success',
                    summary: 'LinkedIn Account Linked',
                    detail: `Account "${newestAccount?.name || status.person_urn || 'Profile'}" added successfully.`,
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
          detail:
            err?.error?.detail ||
            err?.message ||
            'Could not initiate LinkedIn connection.',
        });
      },
    });
  }

  disconnectProfile(): void {
    this.confirmationService.confirm({
      message:
        'Are you sure you want to disconnect this LinkedIn profile and clear bot sessions?',
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
            this.comments = [];
            this.filteredComments = [];
            this.conversations = [];
            this.selectedConversation = null;
            this.messages = [];
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

  disconnectSpecificAccount(account: LinkedInAccount): void {
    this.confirmationService.confirm({
      message: `Are you sure you want to disconnect LinkedIn account "${account.name || account.person_urn}"?`,
      header: 'Disconnect Account',
      icon: 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: 'p-button-danger',
      accept: () => {
        this.linkedinService.disconnectAccount(account.id).subscribe({
          next: () => {
            this.messageService.add({
              severity: 'success',
              summary: 'Account Disconnected',
              detail: `Disconnected ${account.name || 'account'} successfully.`,
            });
            this.loadStatus();
          },
          error: (err) => {
            this.messageService.add({
              severity: 'error',
              summary: 'Error',
              detail: err?.error?.detail || 'Failed to disconnect account.',
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
    const hasCreds =
      !!this.credentialsForm.username?.trim() &&
      !!this.credentialsForm.password?.trim();

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
        this.credentialsForm.password = '';
        this.messageService.add({
          severity: 'success',
          summary: 'Credentials Saved',
          detail: 'LinkedIn session credentials configured successfully.',
        });
        this.loadStatus();
        this.loadConversations();
        this.loadInvitations();
        this.loadComments();
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
      message:
        'Are you sure you want to remove your personal LinkedIn session token/credentials?',
      header: 'Remove Session Credentials',
      icon: 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: 'p-button-danger',
      accept: () => {
        this.linkedinService.disconnectCredentials().subscribe({
          next: () => {
            this.credentialsForm = {
              cookie_li_at: '',
              username: '',
              password: '',
            };
            this.showCredentialsSuccess = false;
            this.conversations = [];
            this.selectedConversation = null;
            this.messages = [];
            this.invitations = [];
            this.comments = [];
            this.filteredComments = [];
            this.messageService.add({
              severity: 'success',
              summary: 'Credentials Removed',
              detail:
                'Personal LinkedIn session and credentials removed successfully.',
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
        auto_dm_leads: this.autoDmLeadsEnabled,
      })
      .subscribe({
        next: () => {
          this.savingSettings = false;
          this.messageService.add({
            severity: 'success',
            summary: 'Settings Saved',
            detail: 'LinkedIn automation rules and CRM lead settings updated.',
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
          detail:
            res.message || 'Background sync of connection requests started.',
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
            (i) => i.invitation_urn !== item.invitation_urn,
          );
          this.messageService.add({
            severity: 'success',
            summary: 'Invitation Accepted',
            detail: `Accepted connection request from ${item.name}. Captured as a Lead in the Leads pipeline.`,
          });
        },
        error: (err) => {
          item.processing = false;
          this.messageService.add({
            severity: 'error',
            summary: 'Accept Failed',
            detail:
              err?.error?.detail || 'Failed to accept LinkedIn invitation.',
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
                (i) => i.invitation_urn !== item.invitation_urn,
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
              detail:
                err?.error?.detail || 'Failed to process batch invitations.',
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

  // --- Auto-Pilot Scheduler ---
  loadAutoConnectSettings(): void {
    this.loadingAutoConnect = true;
    this.linkedinService.getAutoConnectSettings().subscribe({
      next: (res) => {
        this.loadingAutoConnect = false;
        if (res?.settings) {
          this.autoConnectSettings = {
            ...this.autoConnectSettings,
            ...res.settings,
          };
          if (!this.autoConnectSettings.custom_message && this.autoConnectSettings.custom_message !== '') {
            this.autoConnectSettings.custom_message =
              'Hi {firstName}, I came across your profile and was really impressed by your background. Would love to connect!';
          }
        }
      },
      error: (err) => {
        this.loadingAutoConnect = false;
        console.error('Failed to load auto-connect settings:', err);
      },
    });
  }

  saveAutoConnectSettings(): void {
    if (
      this.autoConnectSettings.enabled &&
      !this.autoConnectSettings.target_keywords?.trim() &&
      !this.autoConnectSettings.target_prompt?.trim()
    ) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Target Criteria Required',
        detail:
          'Please specify a target candidate description or boolean search keywords before enabling Auto-Pilot.',
      });
      return;
    }

    this.savingAutoConnect = true;
    this.linkedinService
      .saveAutoConnectSettings(this.autoConnectSettings)
      .subscribe({
        next: (res) => {
          this.savingAutoConnect = false;
          if (res?.settings) {
            this.autoConnectSettings = {
              ...this.autoConnectSettings,
              ...res.settings,
            };
          }
          this.messageService.add({
            severity: 'success',
            summary: 'Auto-Pilot Schedule Saved',
            detail: this.autoConnectSettings.enabled
              ? `Auto-Pilot is ACTIVE: ${this.autoConnectSettings.runs_per_day} randomized runs/day scheduled.`
              : 'Auto-Pilot schedule settings saved (Paused).',
          });
        },
        error: (err) => {
          this.savingAutoConnect = false;
          this.messageService.add({
            severity: 'error',
            summary: 'Save Failed',
            detail:
              err?.error?.detail ||
              'Could not save Auto-Pilot schedule settings.',
          });
        },
      });
  }

  triggerAutoConnectNow(): void {
    if (
      !this.autoConnectSettings.target_keywords?.trim() &&
      !this.autoConnectSettings.target_prompt?.trim()
    ) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Target Criteria Missing',
        detail:
          'Please provide target keywords or a prompt before triggering a run.',
      });
      return;
    }

    this.triggeringAutoConnect = true;
    this.linkedinService.triggerAutoConnectNow().subscribe({
      next: (res) => {
        this.triggeringAutoConnect = false;
        this.messageService.add({
          severity: 'success',
          summary: 'Auto-Pilot Run Dispatched',
          detail:
            'Candidate search and connection dispatch started in background.',
        });
        setTimeout(() => this.loadAutoConnectSettings(), 4000);
      },
      error: (err) => {
        this.triggeringAutoConnect = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Dispatch Failed',
          detail:
            err?.error?.detail ||
            'Could not trigger immediate auto-connect run.',
        });
      },
    });
  }

  generateAutoKeywords(): void {
    if (!this.autoConnectSettings.target_prompt?.trim()) return;

    this.isGeneratingAutoKeywords = true;
    this.linkedinService
      .generateKeywords(this.autoConnectSettings.target_prompt.trim())
      .subscribe({
        next: (res) => {
          this.isGeneratingAutoKeywords = false;
          this.autoConnectSettings.target_keywords = res.keywords || '';
          this.messageService.add({
            severity: 'success',
            summary: 'Keywords Generated',
            detail: 'Auto-Pilot boolean search keywords updated.',
          });
        },
        error: (err) => {
          this.isGeneratingAutoKeywords = false;
          this.messageService.add({
            severity: 'error',
            summary: 'Keyword Generation Failed',
            detail:
              err?.error?.detail || 'Could not generate boolean search keywords.',
          });
        },
      });
  }

  applyAutoConnectPreset(templateText: string): void {
    this.autoConnectSettings.custom_message = templateText;
  }

  copyAutoKeywordsToSearch(): void {
    if (this.autoConnectSettings.target_keywords) {
      this.generatedKeywords = this.autoConnectSettings.target_keywords;
    }
    if (this.autoConnectSettings.target_prompt) {
      this.aiPrompt = this.autoConnectSettings.target_prompt;
    }
    this.messageService.add({
      severity: 'info',
      summary: 'Copied to Search Bar',
      detail: 'Target keywords copied to manual search controls above.',
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
        this.updateSelectedState();
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
    this.updateSelectedState();
  }

  onProfileSelectChange(): void {
    this.updateSelectedState();
  }

  selectSingleProfile(profile: LinkedInProfile): void {
    profile.selected = true;
    this.updateSelectedState();
    this.activeTab = 'invitations';
  }

  deselectProfile(profile: LinkedInProfile): void {
    profile.selected = false;
    this.updateSelectedState();
  }

  updateSelectedState(): void {
    this.selectedProfiles = this.profiles.filter((p) => p.selected);
    this.selectedCount = this.selectedProfiles.length;
    this.selectAllChecked =
      this.profiles.length > 0 && this.selectedCount === this.profiles.length;
  }

  getSelectedCount(): number {
    return this.selectedCount;
  }

  getSelectedProfiles(): LinkedInProfile[] {
    return this.selectedProfiles;
  }

  // --- Send Invitations ---
  sendInvitations(): void {
    const selected = this.getSelectedProfiles();
    if (selected.length === 0) {
      this.messageService.add({
        severity: 'warn',
        summary: 'No Profiles Selected',
        detail:
          'Please select at least one candidate profile from search results.',
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
          detail:
            err?.error?.detail || 'Failed to dispatch connection requests.',
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
        this.updateUnreadCount();
        this.applyConversationFilter();
        if (this.conversations.length > 0) {
          if (!this.selectedConversation) {
            this.selectedConversation = this.conversations[0];
            if (this.activeTab === 'messages') {
              this.selectConversation(this.conversations[0]);
            }
          } else {
            const found = this.conversations.find(
              (c) =>
                (c.conversation_id &&
                  c.conversation_id ===
                    this.selectedConversation?.conversation_id) ||
                (c.conversation_urn &&
                  c.conversation_urn ===
                    this.selectedConversation?.conversation_urn),
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

  selectConversation(conv: LinkedInConversation, isBackgroundRefresh = false): void {
    if (this.activeTab !== 'messages') {
      this.stopChatPolling();
      return;
    }
    this.selectedConversation = conv;
    if (!conv.is_read || (conv.unread_count && conv.unread_count > 0)) {
      conv.is_read = true;
      conv.unread_count = 0;
      this.updateUnreadCount();
    }
    if (!isBackgroundRefresh) {
      this.messages = [];
      this.loadingMessages = true;
      this.syncingThreadMessages = false;
      this.hasNoEarlierMessages = false;
    } else {
      this.syncingThreadMessages = true;
    }
    if (this.activeMessageSub) {
      this.activeMessageSub.unsubscribe();
      this.activeMessageSub = undefined;
    }
    const convId = conv.conversation_id || conv.conversation_urn;
    this.activeMessageSub = this.linkedinService.getConversationMessages(convId).subscribe({
      next: (res) => {
        this.activeMessageSub = undefined;
        this.loadingMessages = false;
        this.syncingThreadMessages = false;
        if (this.activeTab !== 'messages') {
          this.stopChatPolling();
          return;
        }
        const incoming = res.messages || [];
        if (!isBackgroundRefresh || incoming.length !== this.messages.length) {
          const hadMessages = this.messages.length > 0;
          this.messages = incoming;
          if (!hadMessages || incoming.length > this.messages.length) {
            this.scrollToBottom();
          }
          if (incoming.length > 0) {
            conv.last_message = incoming[incoming.length - 1].text;
          }
        }
        if (!isBackgroundRefresh && this.activeTab === 'messages') {
          this.startChatPolling();
          // Quick follow-up sync after 2.5s to capture remaining streamed messages without waiting 15s
          setTimeout(() => {
            if (this.activeTab === 'messages' && this.selectedConversation === conv) {
              this.selectConversation(conv, true);
            }
          }, 2500);
        }
      },
      error: (err) => {
        this.activeMessageSub = undefined;
        this.loadingMessages = false;
        this.syncingThreadMessages = false;
        if (!isBackgroundRefresh && this.activeTab === 'messages') {
          this.messageService.add({
            severity: 'error',
            summary: 'Message Fetch Failed',
            detail:
              err?.error?.detail ||
              'Could not load conversation thread messages.',
          });
        }
      },
    });
  }

  private startChatPolling(): void {
    this.stopChatPolling();
    if (this.activeTab !== 'messages') return;
    this.chatPollingInterval = setInterval(() => {
      // Pause polling if tab is not messages or browser window is hidden/minimized
      if (this.activeTab !== 'messages' || typeof document !== 'undefined' && document.hidden) {
        if (this.activeTab !== 'messages') this.stopChatPolling();
        return;
      }
      if (
        this.selectedConversation &&
        !this.loadingMessages &&
        !this.syncingThreadMessages &&
        !this.sendingMessage
      ) {
        this.selectConversation(this.selectedConversation, true);
      }
    }, 20000);
  }

  private stopChatPolling(): void {
    if (this.chatPollingInterval) {
      clearInterval(this.chatPollingInterval);
      this.chatPollingInterval = null;
    }
  }

  sendDirectReply(): void {
    if (!this.selectedConversation || !this.replyMessageText?.trim()) return;
    const text = this.replyMessageText.trim();
    const convId =
      this.selectedConversation.conversation_id ||
      this.selectedConversation.conversation_urn;
    this.sendingMessage = true;
    this.canSendReply = false;

    this.linkedinService.sendMessage(convId, text).subscribe({
      next: () => {
        this.sendingMessage = false;
        this.replyMessageText = '';
        this.canSendReply = false;
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
        this.onReplyTextChange(this.replyMessageText);
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
          detail: `Synced ${res.synced_conversations || 0} conversations and ${res.synced_messages || 0} messages to Leads pipeline.`,
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

  applyConversationFilter(): void {
    if (!this.searchConversationText?.trim()) {
      this.filteredConversations = this.conversations;
      return;
    }
    const q = this.searchConversationText.toLowerCase().trim();
    this.filteredConversations = this.conversations.filter(
      (c) =>
        (c.contact_name && c.contact_name.toLowerCase().includes(q)) ||
        (c.contact_headline && c.contact_headline.toLowerCase().includes(q)) ||
        (c.last_message && c.last_message.toLowerCase().includes(q)),
    );
  }

  updateUnreadCount(): void {
    this.unreadConversationsCount = this.conversations.filter(
      (c) => !c.is_read || (c.unread_count && c.unread_count > 0),
    ).length;
  }

  onReplyTextChange(text: string): void {
    this.replyMessageText = text;
    this.canSendReply =
      !this.sendingMessage && !!text && text.trim().length > 0;
  }

  getFilteredConversations(): LinkedInConversation[] {
    return this.filteredConversations;
  }

  getUnreadConversationsCount(): number {
    return this.unreadConversationsCount;
  }

  getLeadIntentLabel(intent?: string): string {
    if (!intent) return 'CRM Lead';
    if (intent === 'demo_request') return 'Demo Inquiry';
    if (intent === 'pricing_inquiry') return 'Pricing Inquiry';
    if (intent === 'consultation_request') return 'Consultation Request';
    return 'CRM Lead';
  }

  loadPreviousMessages(): void {
    if (
      !this.selectedConversation ||
      this.loadingPreviousMessages ||
      this.loadingMessages ||
      this.hasNoEarlierMessages
    ) {
      return;
    }
    this.loadingPreviousMessages = true;
    const conv = this.selectedConversation;
    const convId = conv.conversation_id || conv.conversation_urn;
    this.linkedinService.getConversationMessages(convId, true).subscribe({
      next: (res) => {
        this.loadingPreviousMessages = false;
        const incoming = res.messages || [];
        if (incoming.length > this.messages.length) {
          const chatContainer = document.getElementById(
            'linkedin-chat-messages-container',
          );
          const oldScrollHeight = chatContainer ? chatContainer.scrollHeight : 0;
          this.messages = incoming;
          setTimeout(() => {
            if (chatContainer) {
              chatContainer.scrollTop =
                chatContainer.scrollHeight - oldScrollHeight;
            }
          }, 60);
        } else {
          // All earlier messages are already loaded
          this.hasNoEarlierMessages = true;
        }
      },
      error: () => {
        this.loadingPreviousMessages = false;
      },
    });
  }

  private scrollToBottom(): void {
    const doScroll = () => {
      const anchor = document.getElementById('chat-bottom-anchor');
      if (anchor) {
        anchor.scrollIntoView({ behavior: 'auto', block: 'end' });
      }
      const chatContainer = document.getElementById(
        'linkedin-chat-messages-container',
      );
      if (chatContainer) {
        chatContainer.scrollTop = chatContainer.scrollHeight;
      }
    };
    setTimeout(doScroll, 50);
    setTimeout(doScroll, 180);
    setTimeout(doScroll, 400);
  }

  // =========================================================================
  // Comments & AI Replies Automation Methods
  // =========================================================================

  loadCommentSettings(): void {
    this.linkedinService.getCommentSettings().subscribe({
      next: (res) => {
        if (res) {
          this.commentSettings = {
            ...this.commentSettings,
            ...res,
          };
        }
      },
      error: (err) => {
        console.debug('Could not load comment settings:', err);
      },
    });
  }

  saveCommentSettings(): void {
    this.savingCommentSettings = true;
    this.linkedinService.updateCommentSettings(this.commentSettings).subscribe({
      next: (res) => {
        this.savingCommentSettings = false;
        this.showCommentSettingsModal = false;
        this.messageService.add({
          severity: 'success',
          summary: 'Settings Saved',
          detail: 'LinkedIn comment automation rules have been updated.',
        });
      },
      error: (err) => {
        this.savingCommentSettings = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Save Failed',
          detail: err?.error?.detail || 'Failed to update comment settings.',
        });
      },
    });
  }

  loadComments(): void {
    if (!this.status?.connected || !this.status?.['has_cookie_credentials']) {
      return;
    }
    this.loadingComments = true;
    this.linkedinService.getComments({ limit: 50 }).subscribe({
      next: (res) => {
        this.loadingComments = false;
        this.comments = (res.comments || []).map((c: LinkedInSocialComment) => ({
          ...c,
          isEditing: false,
          draftReply: c.suggested_reply || '',
          customInstruction: '',
          isGenerating: false,
          isReplying: false,
          isCapturingLead: false,
        }));
        this.applyCommentFilter();
      },
      error: (err) => {
        this.loadingComments = false;
        this.messageService.add({
          severity: 'warn',
          summary: 'Could Not Load Comments',
          detail: err?.error?.detail || 'Unable to retrieve LinkedIn post comments.',
        });
      },
    });
  }

  syncLinkedInComments(): void {
    this.syncingComments = true;
    this.linkedinService.syncComments().subscribe({
      next: (res) => {
        this.syncingComments = false;
        this.messageService.add({
          severity: 'success',
          summary: 'Comments Synced',
          detail: res.message || 'Scanned recent posts and updated comment queue.',
        });
        this.loadComments();
      },
      error: (err) => {
        this.syncingComments = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Sync Failed',
          detail: err?.error?.detail || 'Failed to scan LinkedIn post comments.',
        });
      },
    });
  }

  approveAndSendReply(comment: LinkedInSocialComment, customText?: string): void {
    const textToSend = customText !== undefined ? customText : (comment.draftReply || comment.suggested_reply || '');
    if (!textToSend.trim()) {
      this.messageService.add({
        severity: 'warn',
        summary: 'Reply Text Required',
        detail: 'Please enter a reply message before posting.',
      });
      return;
    }

    comment.isReplying = true;
    this.linkedinService.postCommentReply(comment.id, textToSend.trim()).subscribe({
      next: (res) => {
        comment.isReplying = false;
        comment.isEditing = false;
        comment.status = 'replied';
        comment.reply_text = textToSend.trim();
        comment.replied_at = Date.now();
        comment.replied_by = 'operator';
        this.applyCommentFilter();

        this.messageService.add({
          severity: 'success',
          summary: 'Reply Published',
          detail: `Comment reply sent to ${comment.author_name}.`,
        });
      },
      error: (err) => {
        comment.isReplying = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Reply Failed',
          detail: err?.error?.detail || 'Could not post comment reply to LinkedIn.',
        });
      },
    });
  }

  startEditingReply(comment: LinkedInSocialComment): void {
    comment.isEditing = true;
    comment.draftReply = comment.draftReply || comment.suggested_reply || '';
  }

  cancelEditingReply(comment: LinkedInSocialComment): void {
    comment.isEditing = false;
    comment.draftReply = comment.suggested_reply || '';
  }

  regenerateReply(comment: LinkedInSocialComment): void {
    comment.isGenerating = true;
    this.linkedinService.generateCommentReply(comment.id, comment.customInstruction).subscribe({
      next: (res) => {
        comment.isGenerating = false;
        comment.suggested_reply = res.suggested_reply;
        comment.suggested_reply_rationale = res.rationale;
        comment.sentiment = res.sentiment as any;
        comment.intent_score = res.intent_score;
        comment.is_lead_candidate = res.is_lead_candidate;
        comment.draftReply = res.suggested_reply;
        comment.customInstruction = '';
        this.applyCommentFilter();

        this.messageService.add({
          severity: 'info',
          summary: 'AI Reply Formulated',
          detail: 'Generated a new context-grounded reply proposal.',
        });
      },
      error: (err) => {
        comment.isGenerating = false;
        this.messageService.add({
          severity: 'error',
          summary: 'AI Generation Failed',
          detail: err?.error?.detail || 'Failed to regenerate comment reply.',
        });
      },
    });
  }

  ignoreComment(comment: LinkedInSocialComment): void {
    this.confirmationService.confirm({
      message: `Are you sure you want to dismiss the comment from "${comment.author_name}"?`,
      header: 'Ignore Comment',
      icon: 'pi pi-exclamation-triangle',
      acceptLabel: 'Ignore',
      rejectLabel: 'Cancel',
      accept: () => {
        this.linkedinService.ignoreComment(comment.id).subscribe({
          next: () => {
            comment.status = 'ignored';
            this.applyCommentFilter();
            this.messageService.add({
              severity: 'info',
              summary: 'Comment Ignored',
              detail: 'Comment removed from pending review queue.',
            });
          },
          error: (err) => {
            this.messageService.add({
              severity: 'error',
              summary: 'Action Failed',
              detail: err?.error?.detail || 'Failed to ignore comment.',
            });
          },
        });
      },
    });
  }

  captureCommentLead(comment: LinkedInSocialComment): void {
    comment.isCapturingLead = true;
    this.linkedinService.captureCommentLead(comment.id).subscribe({
      next: (res) => {
        comment.isCapturingLead = false;
        comment.customer_id = res.customer_id;
        comment.is_lead_candidate = true;
        this.applyCommentFilter();
        this.messageService.add({
          severity: 'success',
          summary: 'Lead Captured',
          detail: `${res.display_name || comment.author_name} captured as a Lead in the Leads pipeline.`,
        });
      },
      error: (err) => {
        comment.isCapturingLead = false;
        this.messageService.add({
          severity: 'error',
          summary: 'Capture Failed',
          detail: err?.error?.detail || 'Could not convert commenter to lead.',
        });
      },
    });
  }

  applyCommentFilter(): void {
    this.filteredComments = this.comments.filter((c) => {
      // Status filter
      if (this.commentStatusFilter === 'pending_review' && c.status !== 'pending_review') {
        return false;
      }
      if (this.commentStatusFilter === 'replied' && c.status !== 'replied' && c.status !== 'auto_replied') {
        return false;
      }
      if (this.commentStatusFilter === 'leads' && !c.is_lead_candidate && !c.customer_id) {
        return false;
      }
      if (this.commentStatusFilter === 'auto_replied' && c.status !== 'auto_replied') {
        return false;
      }

      // Sentiment filter
      if (this.selectedCommentSentiment !== 'all' && c.sentiment !== this.selectedCommentSentiment) {
        return false;
      }

      // Search text filter
      if (this.commentSearchText.trim()) {
        const q = this.commentSearchText.toLowerCase().trim();
        const matchesAuthor = c.author_name.toLowerCase().includes(q);
        const matchesText = c.comment_text.toLowerCase().includes(q);
        const matchesPost = (c.post_title || '').toLowerCase().includes(q) || (c.post_snippet || '').toLowerCase().includes(q);
        const matchesHeadline = (c.author_headline || '').toLowerCase().includes(q);
        return matchesAuthor || matchesText || matchesPost || matchesHeadline;
      }

      return true;
    });
    this.updateCommentCounts();
  }

  updateCommentCounts(): void {
    this.pendingReviewCommentsCount = this.comments.filter((c) => c.status === 'pending_review').length;
    this.capturedLeadsCount = this.comments.filter((c) => c.is_lead_candidate || c.customer_id).length;
  }

  getFilteredComments(): LinkedInSocialComment[] {
    return this.filteredComments;
  }

  getPendingReviewCommentsCount(): number {
    return this.pendingReviewCommentsCount;
  }

  getCapturedLeadsCount(): number {
    return this.capturedLeadsCount;
  }
}


