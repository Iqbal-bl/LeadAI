import { Component, OnInit, inject } from '@angular/core';
import { Router } from '@angular/router';
import { SharedModule } from '../../../../shared/shared.module';
import {
  ChannelWizardComponent,
  ChannelPlatformOption,
  ALL_PLATFORMS,
} from '../../../../features/channels/channel-wizard/channel-wizard.component';
import { OnboardingService, OnboardingStep } from '../../../../services/onboarding.service';
import { BillingService } from '../../../../services/billing.service';
import { ChannelService } from '../../../../services/channel.service';
import { KbService } from '../../../../services/kb.service';
import { ToastService } from '../../../../shared/services/toast.service';
import { AuthService } from '../../../../services/auth.service';
import { Channel } from '../../../../models/channel.models';

interface LanguageOption {
  code: string;
  name: string;
  flag: string;
}

@Component({
  selector: 'app-onboarding',
  standalone: true,
  imports: [SharedModule, ChannelWizardComponent],
  templateUrl: './onboarding.component.html',
  styleUrl: './onboarding.component.scss',
})
export class OnboardingComponent implements OnInit {
  private router = inject(Router);
  private onboardingService = inject(OnboardingService);
  private billingService = inject(BillingService);
  private channelService = inject(ChannelService);
  private kbService = inject(KbService);
  private toastService = inject(ToastService);
  private authService = inject(AuthService);

  // Stepper state
  currentStep: OnboardingStep = 'channels';
  isCompleted = false;
  isLoading = false;

  // Language state
  languages: LanguageOption[] = [
    { code: 'en', name: 'English', flag: '🇬🇧' },
    { code: 'es', name: 'Español', flag: '🇪🇸' },
    { code: 'fr', name: 'Français', flag: '🇫🇷' },
    { code: 'de', name: 'Deutsch', flag: '🇩🇪' },
  ];
  selectedLanguage: LanguageOption = this.languages[0];
  showLanguageDropdown = false;

  // Channels state
  showWizard = false;
  channels: Channel[] = [];
  availablePlatforms: ChannelPlatformOption[] = [];
  activeBundleChannels: string[] = [];
  planName = '';
  hasConnectedChannels = false;
  channelSkipped = false;
  expandedPlatformId: string | null = null;

  // Knowledge base state
  activeKbMethod: 'drive' | 'dropbox' | 'upload' | 'editor' | null = null;
  hasAddedDocs = false;
  kbSkipped = false;
  currentUser: any = null;
  recentDocs: Array<{
    id?: string;
    title: string;
    type: string;
    date: string;
    status: string;
    charCount?: number;
    chunkCount?: number;
  }> = [];

  // Google Drive Link form
  driveTitle = '';
  driveUrl = '';
  driveNotes = '';
  isSavingDrive = false;

  // Dropbox Link form
  dropboxTitle = '';
  dropboxUrl = '';
  dropboxNotes = '';
  isSavingDropbox = false;

  // Editor form
  editorTitle = '';
  editorContent = '';
  editorTags = '';
  isSavingEditor = false;

  // Upload state
  isUploading = false;

  // Support Dialog
  showSupportDialog = false;

  ngOnInit(): void {
    this.isLoading = true;
    this.currentUser = this.authService.getCurrentUser();

    // 1. Fetch backend onboarding state as single source of truth
    this.onboardingService.fetchBackendState().subscribe({
      next: (bState) => {
        this.isLoading = false;
        if (bState.is_completed) {
          this.isCompleted = true;
        }
        if (bState.current_step === 'knowledge_base') {
          this.currentStep = 'knowledge-base';
        }
        this.hasConnectedChannels = bState.channel_connected;
        this.channelSkipped = bState.channel_skipped;
        this.hasAddedDocs = bState.kb_added;
        this.kbSkipped = bState.kb_skipped;

        this.loadSubscriptionAndChannels();
        this.loadExistingDocuments();
      },
      error: () => {
        // Fallback to local state if backend is temporarily unreachable
        this.isLoading = false;
        const local = this.onboardingService.getState();
        if (local) {
          if (local.currentStep === 'knowledge-base') {
            this.currentStep = 'knowledge-base';
          }
          this.isCompleted = !!local.completed;
          this.hasConnectedChannels = !!local.channelConnected;
          this.channelSkipped = !!local.channelSkipped;
          this.hasAddedDocs = !!local.kbAdded;
          this.kbSkipped = !!local.kbSkipped;
        }
        this.loadSubscriptionAndChannels();
        this.loadExistingDocuments();
      },
    });
  }

  loadSubscriptionAndChannels(): void {
    this.billingService.getCurrentPlan().subscribe({
      next: (summary) => {
        this.planName = summary?.active_recharge?.plan_name_snapshot || 'Active Subscription';
        this.activeBundleChannels = (
          summary?.active_recharge?.active_channels || []
        ).map((c) => c.toLowerCase());

        this.channelService.getChannels().subscribe({
          next: (chList) => {
            this.channels = chList || [];
            this.hasConnectedChannels = this.channels.some((c) => c.is_active !== false);
            this.filterAvailablePlatforms();
          },
          error: () => {
            this.channels = [];
            this.filterAvailablePlatforms();
          },
        });
      },
      error: () => {
        this.filterAvailablePlatforms();
      },
    });
  }

  filterAvailablePlatforms(): void {
    this.availablePlatforms = ALL_PLATFORMS.filter(
      (p: Omit<ChannelPlatformOption, 'loading'>) => {
        if (this.activeBundleChannels.length > 0) {
          return this.activeBundleChannels.includes(p.id.toLowerCase());
        }
        return true;
      }
    ).map((p: Omit<ChannelPlatformOption, 'loading'>) => {
      const isConnected = this.channels.some(
        (c) => c.channel?.toLowerCase() === p.id.toLowerCase() && c.is_active !== false
      );
      return {
        ...p,
        loading: false,
        isConnected,
      };
    });

    // Auto expand first connected platform or first platform
    if (!this.expandedPlatformId && this.availablePlatforms.length > 0) {
      const connected = this.availablePlatforms.find((p) => p.isConnected);
      this.expandedPlatformId = connected ? connected.id : this.availablePlatforms[0].id;
    }
  }

  loadExistingDocuments(): void {
    this.kbService.getDocuments().subscribe({
      next: (docs) => {
        if (docs && docs.length > 0) {
          this.hasAddedDocs = true;
          this.recentDocs = docs.map((d) => ({
            id: d.id,
            title: d.title || d.file_name || 'Untitled Document',
            type:
              d.source_type === 'google_drive'
                ? 'Google Drive'
                : d.source_type === 'dropbox'
                  ? 'Dropbox'
                  : d.content_type?.toUpperCase().replace('APPLICATION/', '') || 'Document',
            date: d.created_at ? d.created_at.split('T')[0] : 'Recently',
            status: d.status || 'Indexed',
            charCount: d.char_count,
            chunkCount: d.chunk_count,
          }));
        }
      },
      error: () => {},
    });
  }

  removeDoc(docItem: any, event: Event): void {
    event.stopPropagation();
    if (docItem.id) {
      this.kbService.deleteDocument(docItem.id).subscribe({
        next: () => {
          this.recentDocs = this.recentDocs.filter((d) => d !== docItem);
          this.hasAddedDocs = this.recentDocs.length > 0;
          this.toastService.info(`"${docItem.title}" removed from knowledge base.`);
        },
        error: (err) => {
          this.toastService.error(err?.error?.detail || 'Failed to remove document.');
        },
      });
    } else {
      this.recentDocs = this.recentDocs.filter((d) => d !== docItem);
      this.hasAddedDocs = this.recentDocs.length > 0;
    }
  }

  get progressPercentage(): number {
    if (this.currentStep === 'channels') {
      return this.hasConnectedChannels ? 50 : 35;
    }
    if (this.currentStep === 'knowledge-base') {
      return this.hasAddedDocs ? 90 : 70;
    }
    return 100;
  }

  togglePlatformExpand(id: string): void {
    this.expandedPlatformId = this.expandedPlatformId === id ? null : id;
  }

  getConnectedAccountsForPlatform(platformId: string): Channel[] {
    return this.channels.filter(
      (c) => c.channel?.toLowerCase() === platformId.toLowerCase() && c.is_active !== false
    );
  }

  isAddingNewDoc: { [key: string]: boolean } = {};

  getDocsForType(type: 'drive' | 'dropbox' | 'upload' | 'editor'): Array<any> {
    if (type === 'drive') {
      return this.recentDocs.filter((d) => d.type.toLowerCase().includes('google'));
    }
    if (type === 'dropbox') {
      return this.recentDocs.filter((d) => d.type.toLowerCase().includes('dropbox'));
    }
    if (type === 'editor') {
      return this.recentDocs.filter(
        (d) =>
          d.type.toLowerCase().includes('text') ||
          d.type.toLowerCase().includes('manual') ||
          d.type.toLowerCase().includes('editor')
      );
    }
    // upload
    return this.recentDocs.filter(
      (d) =>
        !d.type.toLowerCase().includes('google') &&
        !d.type.toLowerCase().includes('dropbox') &&
        !d.type.toLowerCase().includes('text') &&
        !d.type.toLowerCase().includes('manual') &&
        !d.type.toLowerCase().includes('editor')
    );
  }

  toggleAddDocForm(type: string): void {
    this.isAddingNewDoc[type] = !this.isAddingNewDoc[type];
  }

  selectLanguage(lang: LanguageOption): void {
    this.selectedLanguage = lang;
    this.showLanguageDropdown = false;
  }

  // --- Channel Wizard Actions ---

  openChannelWizard(): void {
    this.showWizard = true;
  }

  onWizardComplete(): void {
    this.showWizard = false;
    this.hasConnectedChannels = true;
    this.toastService.success('Channel connected successfully!', 'Connected');
    this.loadSubscriptionAndChannels();

    // Sync to backend
    this.onboardingService.saveStepToBackend('channels', 'complete').subscribe({
      next: () => {},
      error: () => this.onboardingService.markChannelDone(true, false),
    });
  }

  onWizardClose(): void {
    this.showWizard = false;
    this.loadSubscriptionAndChannels();
  }

  continueFromChannels(): void {
    this.onboardingService.saveStepToBackend('channels', 'complete').subscribe({
      next: () => {
        this.currentStep = 'knowledge-base';
        window.scrollTo({ top: 0, behavior: 'smooth' });
      },
      error: () => {
        this.onboardingService.markChannelDone(this.hasConnectedChannels, false);
        this.currentStep = 'knowledge-base';
        window.scrollTo({ top: 0, behavior: 'smooth' });
      },
    });
  }

  skipChannels(): void {
    this.channelSkipped = true;
    this.onboardingService.saveStepToBackend('channels', 'skip').subscribe({
      next: () => {
        this.currentStep = 'knowledge-base';
        window.scrollTo({ top: 0, behavior: 'smooth' });
      },
      error: () => {
        this.onboardingService.markChannelDone(this.hasConnectedChannels, true);
        this.currentStep = 'knowledge-base';
        window.scrollTo({ top: 0, behavior: 'smooth' });
      },
    });
  }

  // --- Knowledge Base Actions ---

  toggleKbMethod(method: 'drive' | 'dropbox' | 'upload' | 'editor'): void {
    this.activeKbMethod = this.activeKbMethod === method ? null : method;
  }

  saveGoogleDrive(): void {
    if (!this.driveTitle.trim() || !this.driveUrl.trim()) {
      this.toastService.warn('Please provide a document title and Google Drive link.', 'Missing Fields');
      return;
    }

    if (!this.isValidUrl(this.driveUrl)) {
      this.toastService.error('Please enter a valid URL (e.g. https://drive.google.com/...)', 'Invalid URL');
      return;
    }

    this.isSavingDrive = true;
    this.kbService
      .importCloudLink({
        title: this.driveTitle.trim(),
        url: this.driveUrl.trim(),
        notes: this.driveNotes.trim() || undefined,
        tags: 'google_drive,cloud_link,onboarding',
      })
      .subscribe({
        next: (doc) => {
          this.isSavingDrive = false;
          this.hasAddedDocs = true;
          this.recentDocs.unshift({
            id: doc.id,
            title: doc.title || this.driveTitle,
            type: 'Google Drive',
            date: 'Just now',
            status: 'Indexed',
            charCount: doc.char_count,
            chunkCount: doc.chunk_count,
          });
          this.driveTitle = '';
          this.driveUrl = '';
          this.driveNotes = '';
          this.activeKbMethod = null;
          this.toastService.success('Google Drive file downloaded and indexed successfully!', 'Saved');
        },
        error: (err) => {
          this.isSavingDrive = false;
          this.toastService.error(
            err?.error?.detail || 'Failed to download Google Drive document. Ensure the file sharing is set to "Anyone with the link can view".',
            'Download Error'
          );
        },
      });
  }

  saveDropbox(): void {
    if (!this.dropboxTitle.trim() || !this.dropboxUrl.trim()) {
      this.toastService.warn('Please provide a document title and Dropbox link.', 'Missing Fields');
      return;
    }

    if (!this.isValidUrl(this.dropboxUrl)) {
      this.toastService.error('Please enter a valid URL (e.g. https://www.dropbox.com/...)', 'Invalid URL');
      return;
    }

    this.isSavingDropbox = true;
    this.kbService
      .importCloudLink({
        title: this.dropboxTitle.trim(),
        url: this.dropboxUrl.trim(),
        notes: this.dropboxNotes.trim() || undefined,
        tags: 'dropbox,cloud_link,onboarding',
      })
      .subscribe({
        next: (doc) => {
          this.isSavingDropbox = false;
          this.hasAddedDocs = true;
          this.recentDocs.unshift({
            id: doc.id,
            title: doc.title || this.dropboxTitle,
            type: 'Dropbox',
            date: 'Just now',
            status: 'Indexed',
            charCount: doc.char_count,
            chunkCount: doc.chunk_count,
          });
          this.dropboxTitle = '';
          this.dropboxUrl = '';
          this.dropboxNotes = '';
          this.activeKbMethod = null;
          this.toastService.success('Dropbox file downloaded and indexed successfully!', 'Saved');
        },
        error: (err) => {
          this.isSavingDropbox = false;
          this.toastService.error(
            err?.error?.detail || 'Failed to download Dropbox document. Please ensure the link is public and accessible.',
            'Download Error'
          );
        },
      });
  }

  saveEditorKnowledge(): void {
    if (!this.editorTitle.trim() || !this.editorContent.trim()) {
      this.toastService.warn('Please provide both a title and content for this knowledge entry.', 'Missing Fields');
      return;
    }

    this.isSavingEditor = true;
    this.kbService
      .createText({
        title: this.editorTitle.trim(),
        content: this.editorContent.trim(),
        tags: this.editorTags.trim() || 'custom_entry,onboarding',
      })
      .subscribe({
        next: (doc) => {
          this.isSavingEditor = false;
          this.hasAddedDocs = true;
          this.recentDocs.unshift({
            id: doc.id,
            title: doc.title || this.editorTitle,
            type: 'Editor Entry',
            date: 'Just now',
            status: 'Indexed',
            charCount: doc.char_count,
            chunkCount: doc.chunk_count,
          });
          this.editorTitle = '';
          this.editorContent = '';
          this.editorTags = '';
          this.activeKbMethod = null;
          this.toastService.success('Knowledge entry saved and indexed!', 'Saved');
        },
        error: (err) => {
          this.isSavingEditor = false;
          this.toastService.error(
            err?.error?.detail || 'Failed to save entry. Please try again.',
            'Error'
          );
        },
      });
  }

  onFileUpload(event: any): void {
    const files: File[] = event.files;
    if (!files || files.length === 0) return;

    this.isUploading = true;
    let completedCount = 0;
    const total = files.length;

    files.forEach((file) => {
      this.kbService.uploadDocument(file, 'onboarding').subscribe({
        next: (doc) => {
          completedCount++;
          this.hasAddedDocs = true;
          this.recentDocs.unshift({
            id: doc.id,
            title: doc.file_name || doc.title || file.name,
            type: doc.content_type?.toUpperCase().replace('APPLICATION/', '') || 'File',
            date: 'Just now',
            status: 'Indexed',
            charCount: doc.char_count,
            chunkCount: doc.chunk_count,
          });
          if (completedCount === total) {
            this.isUploading = false;
            this.activeKbMethod = null;
            this.toastService.success(`Uploaded and indexed ${total} document(s)!`, 'Upload Complete');
          }
        },
        error: (err) => {
          completedCount++;
          if (completedCount === total) {
            this.isUploading = false;
          }
          this.toastService.error(
            err?.error?.detail || `Failed to upload "${file.name}".`,
            'Upload Error'
          );
        },
      });
    });
  }

  // --- Step Navigation & Dashboard Completion ---

  continueToDashboard(): void {
    this.onboardingService.saveStepToBackend('knowledge_base', 'complete').subscribe({
      next: () => {
        this.toastService.success('Welcome to LeadAI! Your workspace is ready.', 'Onboarding Completed');
        this.router.navigate(['/client/dashboard']);
      },
      error: () => {
        this.onboardingService.markKnowledgeBaseDone(this.hasAddedDocs, false);
        this.toastService.success('Welcome to LeadAI! Your workspace is ready.', 'Onboarding Completed');
        this.router.navigate(['/client/dashboard']);
      },
    });
  }

  skipKnowledgeBase(): void {
    this.kbSkipped = true;
    this.onboardingService.saveStepToBackend('knowledge_base', 'skip').subscribe({
      next: () => {
        this.toastService.info('You can add your knowledge base anytime from the sidebar.', 'Skipped for now');
        this.router.navigate(['/client/dashboard']);
      },
      error: () => {
        this.onboardingService.markKnowledgeBaseDone(this.hasAddedDocs, true);
        this.toastService.info('You can add your knowledge base anytime from the sidebar.', 'Skipped for now');
        this.router.navigate(['/client/dashboard']);
      },
    });
  }

  backToChannels(): void {
    this.currentStep = 'channels';
    this.onboardingService.setStep('channels');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  private isValidUrl(url: string): boolean {
    try {
      const parsed = new URL(url.trim());
      return parsed.protocol === 'http:' || parsed.protocol === 'https:';
    } catch {
      return false;
    }
  }
}
