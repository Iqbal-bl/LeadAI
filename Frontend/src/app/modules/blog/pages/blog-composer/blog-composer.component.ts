import {
  Component,
  OnInit,
  OnDestroy,
  AfterViewChecked,
  ElementRef,
  ViewChild,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ToastModule } from 'primeng/toast';
import { DialogModule } from 'primeng/dialog';
import { ButtonModule } from 'primeng/button';
import { TagModule } from 'primeng/tag';
import { AvatarModule } from 'primeng/avatar';
import { TooltipModule } from 'primeng/tooltip';
import { MessageService } from 'primeng/api';
import { Subscription } from 'rxjs';
import { ActivatedRoute, Router } from '@angular/router';
import {
  BlogPost,
  BlogPromptRequest,
  BlogTone,
  BlogLength,
  BlogType,
  BlogApprovalStatus,
  BlogReviewNote,
} from '../../models/blog.model';
import { BlogService } from '../../services/blog.service';
import {
  BLOG_TONE_OPTIONS,
  BLOG_LENGTH_PRESETS,
  DEFAULT_BLOG_PROMPT_FORM,
} from '../../constants/blog.constants';

@Component({
  selector: 'app-blog-composer',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ToastModule,
    DialogModule,
    ButtonModule,
    TagModule,
    AvatarModule,
    TooltipModule,
  ],
  providers: [MessageService],
  templateUrl: './blog-composer.component.html',
  styleUrls: ['./blog-composer.component.scss'],
})
export class BlogComposerComponent implements OnInit, AfterViewChecked, OnDestroy {
  private blogService = inject(BlogService);
  private messageService = inject(MessageService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private sub = new Subscription();

  @ViewChild('editorArea') editorArea!: ElementRef<HTMLDivElement>;

  // Flag: set to true when post content needs to be synced to the contenteditable DOM
  private pendingSyncContent = false;

  // Current Blog Post state
  post: BlogPost = this.blogService.createInitialDraft();
  showPreviewModal: boolean = false;

  // Prompt Form State
  promptForm: BlogPromptRequest = { ...DEFAULT_BLOG_PROMPT_FORM };

  keywordInput = '';
  tagInput = '';

  // UI state
  activeTab: 'write' | 'seo' | 'history' = 'write';
  previewDevice: 'desktop' | 'tablet' | 'mobile' = 'desktop';
  viewMode: 'split' | 'editor' | 'preview' = 'split';
  isGenerating = false;
  isSaving = false;
  isSubmittingApproval = false;

  // Tone Options
  tones: { label: string; value: BlogTone }[] = [...BLOG_TONE_OPTIONS];

  // Target Length Presets
  lengthPresets: { label: string; value: BlogLength; words: number }[] = [...BLOG_LENGTH_PRESETS];

  ngOnInit(): void {
    this.sub.add(
      this.blogService.isGenerating$.subscribe((gen) => {
        this.isGenerating = gen;
      })
    );

    this.sub.add(
      this.route.queryParams.subscribe((params) => {
        const id = params['id'];
        if (id) {
          this.loadExistingPost(id);
        } else {
          // No id param → fresh editor
          this.resetDraft();
        }
      })
    );
  }

  loadExistingPost(id: string): void {
    this.blogService.getBlogPostById(id).subscribe((p) => {
      if (p) {
        this.post = { ...p };
        this.promptForm.topic = p.title || '';
        this.promptForm.keywords = [...(p.tags || [])];
        // Make sure we're on the write tab, then flag for content sync
        this.activeTab = 'write';
        this.pendingSyncContent = true;
      } else {
        this.resetDraft();
      }
    });
  }

  ngAfterViewChecked(): void {
    // Once the contenteditable DOM element is available, sync content and clear flag
    if (this.pendingSyncContent && this.editorArea?.nativeElement) {
      this.pendingSyncContent = false;
      // Use setTimeout to avoid ExpressionChangedAfterItHasBeenCheckedError
      setTimeout(() => {
        this.syncEditorContent();
      });
    }
  }

  switchTab(tab: 'write' | 'seo' | 'history'): void {
    this.activeTab = tab;
    if (tab === 'write') {
      setTimeout(() => {
        this.syncEditorContent();
      }, 0);
    }
  }

  ngOnDestroy(): void {
    this.sub.unsubscribe();
  }

  /* ==========================================================
     Blog Type & Form Controls
  ========================================================== */

  setBlogType(type: BlogType): void {
    this.promptForm.blog_type = type;
    if (type === 'text_only') {
      this.promptForm.include_images = false;
      this.promptForm.num_images = 0;
      this.post.coverImage = '';
    } else {
      this.promptForm.include_images = true;
      if (!this.promptForm.num_images || this.promptForm.num_images < 1) {
        this.promptForm.num_images = 1;
      }
    }
  }

  selectLengthPreset(preset: { label: string; value: BlogLength; words: number }): void {
    this.promptForm.target_length = preset.value;
    this.promptForm.length = preset.value;
    this.promptForm.target_words = preset.words;
  }

  addPromptKeyword(): void {
    const val = this.keywordInput.trim();
    if (val && !this.promptForm.keywords.includes(val)) {
      this.promptForm.keywords.push(val);
      this.keywordInput = '';
    }
  }

  removePromptKeyword(index: number): void {
    this.promptForm.keywords.splice(index, 1);
  }

  /* ==========================================================
     AI Generation
  ========================================================== */

  generateWithAi(): void {
    if (!this.promptForm.topic.trim()) {
      this.messageService.add({
        key: 'blog-toast',
        severity: 'warn',
        summary: 'Topic Required',
        detail: 'Please enter a blog topic or headline.',
      });
      return;
    }

    if (this.promptForm.blog_type === 'text_and_image') {
      this.promptForm.include_images = true;
      if (!this.promptForm.num_images || this.promptForm.num_images < 1) {
        this.promptForm.num_images = 1;
      }
    } else {
      this.promptForm.include_images = false;
      this.promptForm.num_images = 0;
    }

    this.promptForm.targetAudience = this.promptForm.target_audience;
    this.promptForm.length = this.promptForm.target_length as BlogLength;

    this.isGenerating = true;
    this.blogService.generateBlogWithAi(this.promptForm).subscribe({
      next: (generatedPost) => {
        this.post = { ...generatedPost };
        this.activeTab = 'write';
        this.pendingSyncContent = true;
        this.isGenerating = false;
        this.messageService.add({
          key: 'blog-toast',
          severity: 'success',
          summary: 'Blog Generated',
          detail: `Generated "${this.post.title}" (${this.post.wordCount || 0} words).`,
        });
      },
      error: (err) => {
        this.isGenerating = false;
        this.messageService.add({
          key: 'blog-toast',
          severity: 'error',
          summary: 'Generation Notice',
          detail: err?.message || 'Generated draft locally.',
        });
      },
    });
  }

  /* ==========================================================
     Editor Formatting
  ========================================================== */

  syncEditorContent(): void {
    if (this.editorArea && this.editorArea.nativeElement) {
      this.editorArea.nativeElement.innerHTML = this.post.content || '<p><br></p>';
    }
    this.updateStats();
  }

  onEditorInput(): void {
    if (this.editorArea && this.editorArea.nativeElement) {
      this.post.content = this.editorArea.nativeElement.innerHTML;
      this.updateStats();
    }
  }

  private getEditorSelection(): { sel: Selection | null; range: Range | null } {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) {
      return { sel: null, range: null };
    }
    const range = sel.getRangeAt(0);
    if (this.editorArea && this.editorArea.nativeElement && !this.editorArea.nativeElement.contains(range.commonAncestorContainer)) {
      return { sel, range: null };
    }
    return { sel, range };
  }

  insertHtmlAtCursor(html: string): void {
    const { sel, range } = this.getEditorSelection();
    if (range) {
      range.deleteContents();
      const temp = document.createElement('div');
      temp.innerHTML = html;
      const frag = document.createDocumentFragment();
      let node: ChildNode | null;
      let lastNode: ChildNode | null = null;
      while ((node = temp.firstChild)) {
        lastNode = frag.appendChild(node);
      }
      range.insertNode(frag);
      if (lastNode && sel) {
        range.setStartAfter(lastNode);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
      }
    } else if (this.editorArea && this.editorArea.nativeElement) {
      this.editorArea.nativeElement.innerHTML += html;
    }
    this.onEditorInput();
  }

  wrapSelectionWithTag(tag: string): void {
    const { sel, range } = this.getEditorSelection();
    if (range && !range.collapsed) {
      const selectedContent = range.extractContents();
      const el = document.createElement(tag);
      el.appendChild(selectedContent);
      range.insertNode(el);
      if (sel) {
        sel.selectAllChildren(el);
      }
    } else if (range && range.collapsed) {
      const el = document.createElement(tag);
      el.textContent = tag.startsWith('h') ? 'Heading Text' : 'Text';
      range.insertNode(el);
      if (sel) {
        sel.selectAllChildren(el);
      }
    } else if (this.editorArea && this.editorArea.nativeElement) {
      this.editorArea.nativeElement.innerHTML += `<${tag}>Text</${tag}>`;
    }
    this.onEditorInput();
  }

  formatHeading(level: string): void {
    const { range } = this.getEditorSelection();
    if (range) {
      let parent = range.commonAncestorContainer as HTMLElement;
      if (parent.nodeType === Node.TEXT_NODE) {
        parent = parent.parentElement as HTMLElement;
      }
      const editorEl = this.editorArea?.nativeElement;
      if (parent && parent !== editorEl && editorEl?.contains(parent)) {
        const tagName = parent.tagName.toLowerCase();
        if (['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'div'].includes(tagName)) {
          const newEl = document.createElement(level);
          newEl.innerHTML = parent.innerHTML;
          parent.parentNode?.replaceChild(newEl, parent);
          this.onEditorInput();
          return;
        }
      }
    }
    this.wrapSelectionWithTag(level);
  }

  execCmd(command: string, value: string | undefined = undefined): void {
    if (command === 'bold') {
      this.wrapSelectionWithTag('strong');
      return;
    }
    if (command === 'italic') {
      this.wrapSelectionWithTag('em');
      return;
    }
    if (command === 'insertUnorderedList') {
      const { range } = this.getEditorSelection();
      if (range && !range.collapsed) {
        const text = range.toString();
        const items = text.split('\n').filter((l) => l.trim().length > 0);
        const listHtml = `<ul>${items.map((i) => `<li>${i}</li>`).join('')}</ul>`;
        this.insertHtmlAtCursor(listHtml);
      } else {
        this.insertHtmlAtCursor('<ul><li>List item</li></ul>');
      }
      return;
    }
    try {
      document.execCommand(command, false, value);
    } catch {
      // ignore
    }
    this.onEditorInput();
  }

  insertLink(): void {
    const url = prompt('Enter link URL (https://...):');
    if (!url) return;
    const { range } = this.getEditorSelection();
    if (range && !range.collapsed) {
      const text = range.toString();
      this.insertHtmlAtCursor(`<a href="${url}" target="_blank" class="text-indigo-600 underline font-medium">${text}</a>`);
    } else {
      this.insertHtmlAtCursor(`<a href="${url}" target="_blank" class="text-indigo-600 underline font-medium">${url}</a>`);
    }
  }

  insertCallout(): void {
    const { range } = this.getEditorSelection();
    const text = range && !range.collapsed ? range.toString() : 'Add your key takeaway or insight here.';
    const html = `<blockquote>💡 <strong>Key Takeaway:</strong> ${text}</blockquote><p><br></p>`;
    this.insertHtmlAtCursor(html);
  }

  updateStats(): void {
    this.post.wordCount = this.blogService.calculateWordCount(this.post.content);
    this.post.readingTimeMinutes = Math.max(1, Math.ceil((this.post.wordCount || 0) / 200));
  }

  onTitleChange(): void {
    if (!this.post.slug || this.post.slug === this.blogService.slugify(this.post.title.slice(0, -1))) {
      this.post.slug = this.blogService.slugify(this.post.title);
      this.post.seo.slug = this.post.slug;
    }
    if (!this.post.seo.metaTitle) {
      this.post.seo.metaTitle = `${this.post.title} | LeadAI Blog`;
    }
  }

  selectCoverImage(url: string): void {
    this.post.coverImage = url;
    this.post.seo.ogImage = url;
  }

  insertImageAtCursor(imageUrl: string): void {
    if (!imageUrl) return;
    this.execCmd('insertImage', imageUrl);
  }

  removeCoverImage(): void {
    this.post.coverImage = '';
    this.post.seo.ogImage = '';
  }

  uploadCustomCover(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files[0]) {
      const reader = new FileReader();
      reader.onload = (e) => {
        const result = e.target?.result as string;
        this.selectCoverImage(result);
      };
      reader.readAsDataURL(input.files[0]);
    }
  }

  addTag(): void {
    const val = this.tagInput.trim();
    if (val && !this.post.tags.includes(val)) {
      this.post.tags.push(val);
      this.tagInput = '';
    }
  }

  removeTag(index: number): void {
    this.post.tags.splice(index, 1);
  }

  /* ==========================================================
     Workflow & Approval History
  ========================================================== */

  submitForApproval(): void {
    if (!this.post.title.trim()) {
      this.messageService.add({
        key: 'blog-toast',
        severity: 'warn',
        summary: 'Title Required',
        detail: 'Please provide a title before submitting for review.',
      });
      return;
    }

    const submittedTitle = this.post.title;
    this.isSubmittingApproval = true;

    this.blogService
      .submitForApproval(
        this.post,
        'Submitted for review and editorial approval.',
      )
      .subscribe({
        next: (updatedPost) => {
          this.isSubmittingApproval = false;
          // Update the in-memory post so status changes to pending_approval
          // This hides the 'Edit in Editor' and 'Send for Approval' buttons
          // without navigating away or resetting the editor
          this.post = { ...updatedPost };

          this.messageService.add({
            key: 'blog-toast',
            severity: 'success',
            summary: 'Submitted for Review',
            detail: `"${submittedTitle}" has been sent for editorial approval. You can track its progress in the Audit Trail.`,
          });

          // Switch to history tab to show the updated audit trail
          this.activeTab = 'history';
        },
        error: (err) => {
          this.isSubmittingApproval = false;
          this.messageService.add({
            key: 'blog-toast',
            severity: 'error',
            summary: 'Submission Failed',
            detail: err?.error?.detail || err?.message || 'Failed to submit blog post for approval.',
          });
        },
      });
  }

  publishNow(): void {
    this.isSaving = true;
    this.blogService.publishPost(this.post).subscribe({
      next: (res) => {
        this.post = res;
        this.isSaving = false;
        this.messageService.add({
          key: 'blog-toast',
          severity: 'success',
          summary: 'Published',
          detail: 'Blog post published successfully.',
        });
      },
      error: () => {
        this.isSaving = false;
      },
    });
  }

  saveDraft(): void {
    const rawContent = this.post.content ? this.post.content.replace(/<[^>]*>/g, '').trim() : '';
    const rawTitle = this.post.title ? this.post.title.trim() : '';

    if (!rawTitle && !rawContent) {
      this.messageService.add({
        key: 'blog-toast',
        severity: 'warn',
        summary: 'Cannot Save Empty Draft',
        detail: 'Please write some content or generate a blog draft first before saving.',
      });
      return;
    }

    if (!rawTitle) {
      this.post.title = this.promptForm.topic || 'Untitled Draft';
    }

    const timestamp = new Date().toISOString();
    const draftNote: BlogReviewNote = {
      id: 'rn_' + Date.now(),
      authorName: this.post.author?.name || 'Author',
      authorRole: 'author',
      timestamp: timestamp,
      content: `Draft saved and updated (${this.post.wordCount || 0} words).`,
      actionTaken: 'draft',
    };

    this.post.reviewNotes = [
      ...(this.post.reviewNotes || []),
      draftNote,
    ];
    if (this.post.status !== 'pending_approval' && this.post.status !== 'approved' && this.post.status !== 'published') {
      this.post.status = 'draft';
    }

    this.blogService.savePost(this.post).subscribe(() => {
      this.messageService.add({
        key: 'blog-toast',
        severity: 'info',
        summary: 'Draft Saved',
        detail: `"${this.post.title}" saved successfully as a draft.`,
      });
    });
  }

  resetDraft(): void {
    this.blogService.clearWorkingDraft();
    this.post = this.blogService.createInitialDraft();
    this.post.coverImage = ''; // Never auto-assign fake image
    this.promptForm = {
      topic: 'What is Generative AI?',
      tone: 'professional',
      target_audience: 'General Audience',
      targetAudience: 'General Audience',
      keywords: ['Generative AI'],
      blog_type: 'text_and_image',
      include_images: true,
      num_images: 1,
      target_words: 300,
      target_length: 'short',
      length: 'short',
      language: 'English',
      cta_text: 'Learn more about AI.',
    };
    setTimeout(() => {
      this.syncEditorContent();
    }, 50);
  }

  getStatusBadgeClass(status: BlogApprovalStatus): string {
    switch (status) {
      case 'draft':
        return 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 border-slate-300 dark:border-slate-700';
      case 'pending_approval':
        return 'bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300 border-amber-300 dark:border-amber-700';
      case 'approved':
        return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300 border-emerald-300 dark:border-emerald-700';
      case 'published':
        return 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300 border-indigo-300 dark:border-indigo-700';
      default:
        return 'bg-slate-100 text-slate-700';
    }
  }

  getStatusLabel(status: BlogApprovalStatus): string {
    switch (status) {
      case 'draft':
        return 'Draft';
      case 'pending_approval':
        return 'In Review (Pending Approval)';
      case 'approved':
        return 'Approved';
      case 'published':
        return 'Published';
      default:
        return status;
    }
  }

  getLatestReviewNote(): string {
    if (this.post.reviewNotes && this.post.reviewNotes.length > 0) {
      const lastNote = this.post.reviewNotes[this.post.reviewNotes.length - 1];
      return lastNote ? lastNote.content : '';
    }
    return '';
  }

  openPreviewModal(): void {
    this.showPreviewModal = true;
  }

  closePreviewModal(): void {
    this.showPreviewModal = false;
  }

  getAuthorInitials(name?: string): string {
    if (!name) return 'LA';
    return name
      .split(' ')
      .map((n) => n[0])
      .join('')
      .toUpperCase()
      .slice(0, 2);
  }
}
