import {
  Component,
  EventEmitter,
  Output,
  Input,
  HostListener,
  ViewChild,
  ElementRef,
  ChangeDetectorRef,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { ButtonModule } from 'primeng/button';
import { FormsModule } from '@angular/forms';
import { ContentDraftService, GenerateDraftResponse } from '../../services/content-draft.service';

@Component({
  selector: 'app-post-editor',
  standalone: true,
  imports: [CommonModule, ButtonModule, FormsModule],
  templateUrl: './post-editor.html',
  styleUrl: './post-editor.scss',
})
export class PostEditor {
  /* ==========================================================
     Create Menu State
  ========================================================== */

  showCreateMenu = false;

  /* ==========================================================
     Post Content Input / Output Sync
  ========================================================== */

  private _postContent = '';

  @Input()
  set postContent(value: string) {
    if (value !== undefined && value !== null) {
      this._postContent = value;
      this.cdr.markForCheck();
    }
  }

  get postContent(): string {
    return this._postContent;
  }

  @Output()
  postContentChange = new EventEmitter<string>();

  @Output()
  contentChange = new EventEmitter<string>();

  @Output()
  aiContentGenerated = new EventEmitter<string>();

  /* ==========================================================
     AI Generator State
  ========================================================== */

  showAiPrompt = false;
  aiPrompt = '';
  isGenerating = false;

  @ViewChild('aiPromptInput')
  aiPromptInput?: ElementRef<HTMLTextAreaElement>;

  @ViewChild('postContentInput')
  postContentInput?: ElementRef<HTMLTextAreaElement>;

  maxCharacters = 3000;

  /* ==========================================================
     Reset Version From Parent
  ========================================================== */

  private _resetVersion = 0;

  @Input()
  set resetVersion(value: number) {
    if (value !== this._resetVersion) {
      this._resetVersion = value;
      this._postContent = '';
      this.aiPrompt = '';
      this.showAiPrompt = false;
      this.isGenerating = false;

      setTimeout(() => {
        if (this.postContentInput) {
          this.postContentInput.nativeElement.scrollTop = 0;
        }
      });

      this.contentChange.emit('');
      this.postContentChange.emit('');
      this.cdr.markForCheck();
    }
  }

  get resetVersion(): number {
    return this._resetVersion;
  }

  constructor(
    private readonly contentDraftService: ContentDraftService,
    private readonly cdr: ChangeDetectorRef
  ) {
    console.log('🔥 PostEditor CREATED');
  }

  /* ==========================================================
     Create Action Dropdown
  ========================================================== */

  toggleCreateMenu(event: Event): void {
    event.stopPropagation();
    this.showCreateMenu = !this.showCreateMenu;
  }

  @HostListener('document:click')
  closeCreateMenu(): void {
    this.showCreateMenu = false;
  }

  /* ==========================================================
     Content Change Event
  ========================================================== */

  onContentChange(): void {
    this.contentChange.emit(this._postContent);
    this.postContentChange.emit(this._postContent);
  }

  /* ==========================================================
     AI Post Generator Toggle & Draft
  ========================================================== */

  toggleAiPrompt(event?: Event): void {
    if (event) {
      event.stopPropagation();
    }
    this.showAiPrompt = !this.showAiPrompt;

    if (this.showAiPrompt) {
      setTimeout(() => {
        this.aiPromptInput?.nativeElement?.focus();
      });
    } else {
      this.aiPrompt = '';
    }
  }

  closeAiPrompt(): void {
    this.showAiPrompt = false;
    this.aiPrompt = '';
    this.cdr.markForCheck();
  }

  generateAiPost(): void {
    this.generateAiDraft();
  }

  generateAiDraft(): void {
    const prompt = this.aiPrompt.trim();
    if (!prompt) return;

    this.isGenerating = true;

    this.contentDraftService.generatePostDraft(prompt, 1).subscribe({
      next: (response: GenerateDraftResponse) => {
        console.log('🤖 PostEditor FULL DRAFT RESPONSE:', response);

        const generatedText = this.extractGeneratedContent(response);

        console.log('🤖 PostEditor extracted content:', generatedText);

        if (!generatedText) {
          console.error('❌ Backend returned empty content:', response);
          this.isGenerating = false;
          return;
        }

        this._postContent = generatedText;
        this.cdr.markForCheck();
        this.cdr.detectChanges();

        setTimeout(() => {
          if (this.postContentInput) {
            this.postContentInput.nativeElement.scrollTop = 0;
          }
        });

        this.contentChange.emit(this._postContent);
        this.postContentChange.emit(this._postContent);
        this.aiContentGenerated.emit(this._postContent);

        this.isGenerating = false;
        this.showAiPrompt = false;
        this.aiPrompt = '';
      },
      error: (error: any) => {
        console.error('❌ Draft generation failed:', error);
        this.isGenerating = false;
        this.showAiPrompt = true;
      },
    });
  }

  private extractGeneratedContent(response: any): string {
    if (!response) return '';
    if (typeof response === 'string') return response.trim();

    const directKeys = [
      'draft',
      'content',
      'generatedContent',
      'generated_content',
      'generated_text',
      'text',
      'post_text',
      'postText',
      'output',
      'message',
      'response',
    ];

    for (const key of directKeys) {
      if (typeof response[key] === 'string' && response[key].trim()) {
        return response[key].trim();
      }
    }

    if (response.data && typeof response.data === 'object') {
      for (const key of directKeys) {
        if (typeof response.data[key] === 'string' && response.data[key].trim()) {
          return response.data[key].trim();
        }
      }
      if (typeof response.data === 'string' && response.data.trim()) {
        return response.data.trim();
      }
    }

    if (response.result && typeof response.result === 'object') {
      for (const key of directKeys) {
        if (typeof response.result[key] === 'string' && response.result[key].trim()) {
          return response.result[key].trim();
        }
      }
      if (typeof response.result === 'string' && response.result.trim()) {
        return response.result.trim();
      }
    }

    for (const key of Object.keys(response)) {
      if (typeof response[key] === 'string' && response[key].trim() && key !== 'id' && key !== 'status') {
        return response[key].trim();
      }
    }

    return '';
  }

  /* ==========================================================
     Emoji & Quick Action Snippets
  ========================================================== */

  onEmojiClick(): void {
    this.insertSnippetAtCursor('😊 ');
  }

  onAddLink(): void {
    this.showCreateMenu = false;
    this.insertSnippetAtCursor(' https://leadai.com ');
  }

  onAddLocation(): void {
    this.showCreateMenu = false;
    this.insertSnippetAtCursor(' 📍 San Francisco, CA ');
  }

  onCreatePoll(): void {
    this.showCreateMenu = false;
    this.insertSnippetAtCursor('\n📊 Quick Poll:\n1. Option A\n2. Option B\n3. Option C');
  }

  private insertSnippetAtCursor(snippet: string): void {
    if (!this.postContentInput) {
      this._postContent += snippet;
      this.onContentChange();
      return;
    }

    const textarea = this.postContentInput.nativeElement;
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;

    this._postContent =
      this._postContent.substring(0, start) +
      snippet +
      this._postContent.substring(end);

    this.onContentChange();
    this.cdr.markForCheck();

    setTimeout(() => {
      textarea.focus();
      textarea.selectionStart = textarea.selectionEnd = start + snippet.length;
    });
  }
}
