import { Component, OnInit, inject } from '@angular/core';
import { MessageService } from 'primeng/api';
import { ConfirmationService } from '../../../shared/services/confirmation.service';
import { ScriptService } from '../../../services/script.service';
import { Script, ScriptPreview, PromptTemplate } from '../../../models/script.models';
import { SharedModule } from '../../../shared/shared.module';
import { ScriptListComponent } from './components/script-list/script-list.component';
import { ScriptWorkspaceComponent } from './components/script-workspace/script-workspace.component';
import { PromptGridComponent } from './components/prompt-grid/prompt-grid.component';

export interface PromptItem {
  key: string;
  content: string;
  is_customised: boolean;
  updated_at?: string | null;
  title: string;
  description: string;
}

const PROMPT_META: Record<string, { title: string; description: string }> = {
  greeting: {
    title: 'Welcome Greeting',
    description: 'The initial welcome message sent to customer when session starts.'
  },
  sales: {
    title: 'Sales Representative Context',
    description: 'Core brand identity, tone parameters, and primary product descriptions.'
  },
  qualification: {
    title: 'Lead Qualification (BANT)',
    description: 'Specific qualification requirements for budget, authority, needs, and timeline.'
  },
  escalation: {
    title: 'Human Escalation Criteria',
    description: 'Direct rules defining when the AI must hand off conversation to a human agent.'
  },
  voice: {
    title: 'Voice Channel Assistant',
    description: 'Acoustic turn-taking controls, text-to-speech pronunciation, and voice parameters.'
  }
};

@Component({
  selector: 'app-prompt-editor',
  standalone: true,
  imports: [SharedModule, ScriptListComponent, ScriptWorkspaceComponent, PromptGridComponent],
  templateUrl: './prompt-editor.component.html',
  styleUrl: './prompt-editor.component.scss'
})
export class PromptEditorComponent implements OnInit {
  // Service Injections
  private scriptService = inject(ScriptService);
  private messageService = inject(MessageService);
  private confirmationService = inject(ConfirmationService);

  // Tab State
  activeTab: 'scripts' | 'prompts' = 'scripts';
  listCollapsed = false;

  // --- SCRIPTS TAB STATE ---
  scripts: Script[] = [];
  selectedScript: (Script & { script_xml?: string; sections?: any[]; rendered_prompt?: string }) | null = null;
  scriptLoading = false;
  scriptXmlEditorContent = '';
  
  // Script Preview
  previewChannel: 'chat' | 'voice' = 'chat';
  scriptPreview: ScriptPreview | null = null;
  previewLoading = false;

  // Create/Edit Settings Modal
  showScriptModal = false;
  isEditingScript = false;
  scriptForm: {
    id?: string;
    name: string;
    description: string;
    channel: 'all' | 'chat' | 'voice';
    language: string;
    voice_gender: 'male' | 'female';
    voice_speaker: string;
    multi_stt: boolean;
  } = {
    name: '',
    description: '',
    channel: 'all',
    language: 'en-IN',
    voice_gender: 'female',
    voice_speaker: 'Priya',
    multi_stt: false
  };

  // Import Modal State
  showImportModal = false;
  importableFiles: string[] = [];
  selectedImportFile = '';
  importLoading = false;

  // Speaker configuration dropdowns
  voiceSpeakers: Record<'male' | 'female', { label: string; value: string }[]> = {
    male: [
      { label: 'Prabhat (Hindi/English)', value: 'Prabhat' },
      { label: 'Rohan (English - IN)', value: 'Rohan' },
      { label: 'David (English - US)', value: 'David' }
    ],
    female: [
      { label: 'Priya (Hindi/English - Recommended)', value: 'Priya' },
      { label: 'Kavita (Hindi)', value: 'Kavita' },
      { label: 'Emily (English - US)', value: 'Emily' }
    ]
  };

  languages = [
    { label: 'English (India) - en-IN', value: 'en-IN' },
    { label: 'English (US) - en-US', value: 'en-US' },
    { label: 'Hindi (India) - hi-IN', value: 'hi-IN' }
  ];

  channels = [
    { label: 'All Channels', value: 'all' },
    { label: 'Chat Only', value: 'chat' },
    { label: 'Voice Only', value: 'voice' }
  ];

  // --- PROMPTS TAB STATE ---
  prompts: PromptItem[] = [];
  selectedPrompt: PromptItem | null = null;
  showPromptEditModal = false;
  promptEditContent = '';
  promptsLoading = false;
  promptSaving = false;

  ngOnInit(): void {
    this.loadScripts();
    this.loadPrompts();
  }

  // ==========================================
  // SCRIPTS MANAGEMENT
  // ==========================================
  loadScripts(selectId?: string): void {
    this.scriptLoading = true;
    this.scriptService.getScripts(undefined, true).subscribe({
      next: (data) => {
        this.scripts = data;
        if (this.scripts.length > 0) {
          if (selectId) {
            const found = this.scripts.find(s => s.id === selectId);
            if (found) {
              this.selectScript(found);
              return;
            }
          }
          // Select default script first, otherwise the first one
          const defaultScript = this.scripts.find(s => s.is_default) || this.scripts[0];
          this.selectScript(defaultScript);
        } else {
          this.selectedScript = null;
          this.scriptXmlEditorContent = '';
          this.scriptPreview = null;
        }
        this.scriptLoading = false;
      },
      error: (err) => {
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Failed to load conversation scripts.'
        });
        this.scriptLoading = false;
      }
    });
  }

  selectScript(script: Script): void {
    this.scriptLoading = true;
    this.scriptService.getScript(script.id!).subscribe({
      next: (data) => {
        this.selectedScript = data;
        this.scriptXmlEditorContent = data.script_xml || '';
        this.loadScriptPreview(script.id!, this.previewChannel);
        this.scriptLoading = false;
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: `Failed to load details for script ${script.name}.`
        });
        this.scriptLoading = false;
      }
    });
  }

  loadScriptPreview(id: string, channel: 'chat' | 'voice'): void {
    this.previewLoading = true;
    this.scriptService.previewScript(id, channel).subscribe({
      next: (preview) => {
        this.scriptPreview = preview;
        this.previewLoading = false;
      },
      error: () => {
        this.scriptPreview = null;
        this.previewLoading = false;
      }
    });
  }

  onPreviewChannelChange(channel: 'chat' | 'voice'): void {
    this.previewChannel = channel;
    if (this.selectedScript?.id) {
      this.loadScriptPreview(this.selectedScript.id, channel);
    }
  }

  saveScriptXml(): void {
    if (!this.selectedScript?.id) return;
    this.scriptLoading = true;
    this.scriptService.updateScript(this.selectedScript.id, { script_xml: this.scriptXmlEditorContent }).subscribe({
      next: (updated) => {
        this.messageService.add({
          severity: 'success',
          summary: 'Saved',
          detail: 'Script XML saved successfully.'
        });
        if (this.selectedScript) {
          this.selectedScript.script_xml = this.scriptXmlEditorContent;
        }
        this.loadScriptPreview(this.selectedScript!.id!, this.previewChannel);
        this.scriptLoading = false;
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Save Failed',
          detail: 'Failed to update XML schema on server.'
        });
        this.scriptLoading = false;
      }
    });
  }

  openCreateScriptModal(): void {
    this.isEditingScript = false;
    this.scriptForm = {
      name: '',
      description: '',
      channel: 'all',
      language: 'en-IN',
      voice_gender: 'female',
      voice_speaker: 'Priya',
      multi_stt: false
    };
    this.showScriptModal = true;
  }

  openEditScriptSettingsModal(script: Script, event: Event): void {
    event.stopPropagation();
    this.isEditingScript = true;
    this.scriptForm = {
      id: script.id,
      name: script.name,
      description: script.description || '',
      channel: script.channel,
      language: script.language,
      voice_gender: script.voice_gender || 'female',
      voice_speaker: script.voice_speaker || 'Priya',
      multi_stt: script.multi_stt || false
    };
    this.showScriptModal = true;
  }

  submitScriptForm(): void {
    if (this.isEditingScript && this.scriptForm.id) {
      this.scriptService.updateScript(this.scriptForm.id, this.scriptForm).subscribe({
        next: (updated) => {
          this.messageService.add({
            severity: 'success',
            summary: 'Updated',
            detail: 'Script configuration updated.'
          });
          this.showScriptModal = false;
          this.loadScripts(updated.id);
        },
        error: () => {
          this.messageService.add({
            severity: 'error',
            summary: 'Error',
            detail: 'Failed to update script settings.'
          });
        }
      });
    } else {
      // Create new script payload (inject placeholder XML template)
      const payload: Script = {
        ...this.scriptForm,
        script_xml: `<script>\n  <section name="greeting">\n    Hello! Welcome to our company. How can I assist you today?\n  </section>\n  <section name="qualification">\n    To help you best, could you tell me a bit about your budget and timeline?\n  </section>\n  <section name="sales">\n    Our product provides live AI qualification and qualification scoreboards directly integrated.\n  </section>\n</script>`
      };
      this.scriptService.createScript(payload).subscribe({
        next: (created) => {
          this.messageService.add({
            severity: 'success',
            summary: 'Created',
            detail: 'New script schema initialized.'
          });
          this.showScriptModal = false;
          this.loadScripts(created.id);
        },
        error: () => {
          this.messageService.add({
            severity: 'error',
            summary: 'Error',
            detail: 'Failed to initialize script schema.'
          });
        }
      });
    }
  }

  setDefaultScript(script: Script, event: Event): void {
    event.stopPropagation();
    this.scriptService.setDefaultScript(script.id!).subscribe({
      next: () => {
        this.messageService.add({
          severity: 'success',
          summary: 'Default Changed',
          detail: `${script.name} is now the default active script.`
        });
        this.loadScripts(script.id);
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Failed to set script as default.'
        });
      }
    });
  }

  deleteScript(script: Script, event: Event): void {
    event.stopPropagation();
    this.confirmationService.confirm({
      message: `Are you sure you want to permanently delete the script "${script.name}"? This action cannot be undone.`,
      header: 'Delete Script',
      icon: 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: 'p-button-danger',
      accept: () => {
        this.scriptService.deleteScript(script.id!).subscribe({
          next: () => {
            this.messageService.add({
              severity: 'success',
              summary: 'Deleted',
              detail: 'Script deleted successfully.'
            });
            if (this.selectedScript?.id === script.id) {
              this.selectedScript = null;
            }
            this.loadScripts();
          },
          error: () => {
            this.messageService.add({
              severity: 'error',
              summary: 'Error',
              detail: 'Failed to delete script.'
            });
          }
        });
      }
    });
  }

  openImportScriptModal(): void {
    this.importLoading = true;
    this.scriptService.getImportableScripts().subscribe({
      next: (res: any) => {
        this.importableFiles = res.files || (Array.isArray(res) ? res.map((f: any) => f.filename || f) : []);
        this.selectedImportFile = this.importableFiles[0] || '';
        this.showImportModal = true;
        this.importLoading = false;
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Failed to fetch importable XML files from disk.'
        });
        this.importLoading = false;
      }
    });
  }

  importScript(): void {
    if (!this.selectedImportFile) return;
    this.importLoading = true;
    this.scriptService.importScript(this.selectedImportFile).subscribe({
      next: (created) => {
        this.messageService.add({
          severity: 'success',
          summary: 'Imported',
          detail: 'Script file imported into database.'
        });
        this.showImportModal = false;
        this.loadScripts(created.id);
        this.importLoading = false;
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Failed to parse and import the XML file.'
        });
        this.importLoading = false;
      }
    });
  }

  // ==========================================
  // PROMPTS (5 SYSTEM CARDS) MANAGEMENT
  // ==========================================
  loadPrompts(): void {
    this.promptsLoading = true;
    this.scriptService.getPrompts().subscribe({
      next: (data) => {
        this.prompts = data.map((p: any) => ({
          key: p.key,
          content: p.content || p.value || '',
          is_customised: p.is_customised,
          updated_at: p.updated_at,
          title: PROMPT_META[p.key]?.title || p.key.toUpperCase(),
          description: PROMPT_META[p.key]?.description || 'System prompt template.'
        }));
        this.promptsLoading = false;
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Failed to load system prompts.'
        });
        this.promptsLoading = false;
      }
    });
  }

  openEditPromptModal(prompt: PromptItem): void {
    this.selectedPrompt = prompt;
    this.promptEditContent = prompt.content;
    this.showPromptEditModal = true;
  }

  savePrompt(): void {
    if (!this.selectedPrompt) return;
    this.promptSaving = true;
    this.scriptService.updatePrompt(this.selectedPrompt.key, this.promptEditContent).subscribe({
      next: () => {
        this.messageService.add({
          severity: 'success',
          summary: 'Saved',
          detail: `${this.selectedPrompt?.title} prompt updated.`
        });
        this.showPromptEditModal = false;
        this.loadPrompts();
        this.promptSaving = false;
      },
      error: () => {
        this.messageService.add({
          severity: 'error',
          summary: 'Error',
          detail: 'Failed to update prompt text.'
        });
        this.promptSaving = false;
      }
    });
  }

  resetPrompt(prompt: PromptItem, event: Event): void {
    event.stopPropagation();
    this.confirmationService.confirm({
      message: `Are you sure you want to reset "${prompt.title}" to its system default settings? Any custom text will be deleted.`,
      header: 'Reset Prompt Template',
      icon: 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: 'p-button-warning',
      accept: () => {
        this.scriptService.resetPrompt(prompt.key).subscribe({
          next: () => {
            this.messageService.add({
              severity: 'success',
              summary: 'Reset Successful',
              detail: `${prompt.title} has been reverted to system default.`
            });
            this.loadPrompts();
          },
          error: () => {
            this.messageService.add({
              severity: 'error',
              summary: 'Error',
              detail: 'Failed to reset prompt.'
            });
          }
        });
      }
    });
  }
}
