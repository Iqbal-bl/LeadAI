import { Component, EventEmitter, OnDestroy, Output, Input, ViewChild, ElementRef, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ContentDraftService, GenerateDraftResponse } from '../../services/content-draft.service';
import Cropper from 'cropperjs';

export interface MediaItem {
  id: string;
  file: File;
  kind: 'image' | 'video' | 'document';
  previewUrl: string | null;
}

@Component({
  selector: 'app-media-toolbar',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './media-toolbar.html',
  styleUrl: './media-toolbar.scss',
})
export class MediaToolbar implements OnDestroy {
  constructor(
    private contentDraftService: ContentDraftService,
    private cdr: ChangeDetectorRef,
  ) {
    console.log('🔥 MediaToolbar CREATED');
  }

  /* ==========================================================
     Media Items & Events
  ========================================================== */

  mediaItems: MediaItem[] = [];

  @Output()
  mediaChange = new EventEmitter<MediaItem[]>();

  @Output()
  aiContentGenerated = new EventEmitter<string>();

  /* ==========================================================
     Cropper.js State & Aspect Ratio Presets
  ========================================================== */

  isCropping = false;
  croppingItem: MediaItem | null = null;
  cropperInstance: Cropper | null = null;
  selectedAspectRatio: number | null = 1; // 1:1 Square default

  @ViewChild('cropperImage') cropperImageRef?: ElementRef<HTMLImageElement>;

  readonly aspectRatios = [
    { label: '1:1 Square', value: 1, desc: 'Feed (1080×1080)' },
    { label: '4:5 Portrait', value: 4 / 5, desc: 'Instagram Portrait (1080×1350)' },
    { label: '1.91:1 Landscape', value: 1.91, desc: 'Landscape / Link (1200×630)' },
    { label: '16:9 Wide', value: 16 / 9, desc: 'Wide Landscape (1920×1080)' },
    { label: '9:16 Reel / Story', value: 9 / 16, desc: 'Reels / Stories (1080×1920)' },
  ];

  /* ==========================================================
     AI Post Generator State
  ========================================================== */

  showAiPrompt = false;
  aiPrompt = '';
  isGenerating = false;

  toggleAiPrompt(): void {
    this.showAiPrompt = !this.showAiPrompt;
  }

  generateAiPost(): void {
    if (!this.aiPrompt || !this.aiPrompt.trim()) {
      return;
    }

    this.isGenerating = true;

    this.contentDraftService.generatePostDraft(this.aiPrompt.trim()).subscribe({
      next: (response: GenerateDraftResponse) => {
        console.log('🤖 MediaToolbar received response:', response);
        const generatedText = this.extractGeneratedContent(response);

        console.log('🤖 MediaToolbar extracted generatedText:', generatedText);

        if (generatedText) {
          this.aiContentGenerated.emit(generatedText);
        } else {
          console.error('❌ MediaToolbar could not extract text from response:', response);
        }

        this.isGenerating = false;
        this.showAiPrompt = false;
        this.aiPrompt = '';
      },
      error: (err: any) => {
        console.error('AI post generation error:', err);
        this.isGenerating = false;
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
     Reset Version From Parent
  ========================================================== */

  private _resetVersion = 0;

  @Input()
  set resetVersion(value: number) {
    if (value !== this._resetVersion) {
      this._resetVersion = value;
      this.clearMedia();
      this.showAiPrompt = false;
      this.aiPrompt = '';
      this.isGenerating = false;
      this.cancelCrop();
    }
  }

  get resetVersion(): number {
    return this._resetVersion;
  }

  /* ==========================================================
     File Selected
  ========================================================== */

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    let kind: 'image' | 'video' | 'document';
    let previewUrl: string | null = null;

    if (file.type.startsWith('image/')) {
      kind = 'image';
      previewUrl = URL.createObjectURL(file);
    } else if (file.type.startsWith('video/')) {
      kind = 'video';
      previewUrl = URL.createObjectURL(file);
    } else {
      kind = 'document';
    }

    const newItem: MediaItem = {
      id: crypto.randomUUID(),
      file,
      kind,
      previewUrl,
    };

    this.mediaItems.push(newItem);
    this.emitMediaChange();
    input.value = '';

    // Auto-open cropper on newly uploaded image so user can easily format it
    if (kind === 'image') {
      setTimeout(() => {
        this.openCropper(newItem);
      }, 50);
    }
  }

  removeMedia(id: string): void {
    const item = this.mediaItems.find((media) => media.id === id);
    if (item?.previewUrl) {
      URL.revokeObjectURL(item.previewUrl);
    }
    this.mediaItems = this.mediaItems.filter((media) => media.id !== id);
    if (this.croppingItem?.id === id) {
      this.cancelCrop();
    }
    this.emitMediaChange();
  }

  private clearMedia(): void {
    this.mediaItems.forEach((item) => {
      if (item.previewUrl) {
        URL.revokeObjectURL(item.previewUrl);
      }
    });
    this.mediaItems = [];
    this.emitMediaChange();
  }

  private emitMediaChange(): void {
    this.mediaChange.emit([...this.mediaItems]);
  }

  fileSizeLabel(file: File): string {
    const kb = file.size / 1024;
    if (kb < 1024) {
      return `${kb.toFixed(0)} KB`;
    }
    return `${(kb / 1024).toFixed(1)} MB`;
  }

  /* ==========================================================
     Cropper.js Modal Operations
  ========================================================== */

  openCropper(item: MediaItem): void {
    if (item.kind !== 'image') return;
    this.croppingItem = item;
    this.selectedAspectRatio = 1; // Default 1:1
    this.isCropping = true;
    this.cdr.detectChanges();

    setTimeout(() => {
      this.initCropperInstance();
    }, 100);
  }

  private initCropperInstance(): void {
    if (this.cropperInstance) {
      this.cropperInstance.destroy();
      this.cropperInstance = null;
    }

    if (!this.cropperImageRef?.nativeElement) return;

    this.cropperInstance = new Cropper(this.cropperImageRef.nativeElement, {
      aspectRatio: this.selectedAspectRatio || 1,
      viewMode: 1,
      dragMode: 'move',
      autoCropArea: 0.9,
      restore: false,
      guides: true,
      center: true,
      highlight: true,
      background: true,
      cropBoxMovable: true,
      cropBoxResizable: false, // Locked: user cannot manually change/drag frame dimensions
      movable: true,
      zoomable: true,
      rotatable: true,
      scalable: true,
      toggleDragModeOnDblclick: false,
      responsive: true,
      checkOrientation: false,
    });
  }

  setCropperRatio(ratio: number | null): void {
    this.selectedAspectRatio = ratio || 1;
    if (this.cropperInstance) {
      this.cropperInstance.setAspectRatio(this.selectedAspectRatio);
    }
  }

  rotateCropper(degree: number): void {
    if (this.cropperInstance) {
      this.cropperInstance.rotate(degree);
    }
  }

  zoomCropper(delta: number): void {
    if (this.cropperInstance) {
      this.cropperInstance.zoom(delta);
    }
  }

  resetCropper(): void {
    if (this.cropperInstance) {
      this.cropperInstance.reset();
    }
  }

  applyCrop(): void {
    if (!this.cropperInstance || !this.croppingItem) {
      this.cancelCrop();
      return;
    }

    // Get cropped canvas constrained to Graph API specs (320px to 1440px width)
    const canvas = this.cropperInstance.getCroppedCanvas({
      maxWidth: 1440,
      minWidth: 320,
      imageSmoothingEnabled: true,
      imageSmoothingQuality: 'high',
      fillColor: '#FFFFFF',
    });

    if (!canvas) {
      this.cancelCrop();
      return;
    }

    canvas.toBlob(
      (blob: Blob | null) => {
        if (!blob || !this.croppingItem) {
          this.cancelCrop();
          return;
        }

        // Replace old preview URL
        if (this.croppingItem.previewUrl) {
          URL.revokeObjectURL(this.croppingItem.previewUrl);
        }

        const newPreviewUrl = URL.createObjectURL(blob);
        const originalName = this.croppingItem.file.name.replace(/\.[^/.]+$/, '') + '.jpg';
        const croppedFile = new File([blob], originalName, {
          type: 'image/jpeg',
          lastModified: Date.now(),
        });

        const updatedItem: MediaItem = {
          id: crypto.randomUUID(),
          file: croppedFile,
          kind: 'image',
          previewUrl: newPreviewUrl,
        };

        const idx = this.mediaItems.findIndex((m) => m.id === this.croppingItem?.id);
        if (idx !== -1) {
          this.mediaItems[idx] = updatedItem;
        } else {
          this.mediaItems.push(updatedItem);
        }

        this.emitMediaChange();
        this.cancelCrop();
      },
      'image/jpeg',
      0.92,
    );
  }

  cancelCrop(): void {
    if (this.cropperInstance) {
      this.cropperInstance.destroy();
      this.cropperInstance = null;
    }
    this.isCropping = false;
    this.croppingItem = null;
    this.cdr.detectChanges();
  }

  ngOnDestroy(): void {
    this.cancelCrop();
    this.mediaItems.forEach((item) => {
      if (item.previewUrl) {
        URL.revokeObjectURL(item.previewUrl);
      }
    });
  }
}
