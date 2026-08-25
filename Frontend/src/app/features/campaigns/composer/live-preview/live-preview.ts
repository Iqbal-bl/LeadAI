import { Component, Input, ViewChild, ElementRef, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';

import { MediaItem } from '../media-toolbar/media-toolbar';

@Component({
  selector: 'app-live-preview',
  standalone: true,

  imports: [CommonModule],

  templateUrl: './live-preview.html',
  styleUrl: './live-preview.scss',
})
export class LivePreview {
  constructor(private cdr: ChangeDetectorRef) {}

  /* ==========================================================
     Post Content From Composer
  ========================================================== */

  @Input()
  postText = '';

  /* ==========================================================
     Selected Accounts From Composer
  ========================================================== */

  private _selectedAccounts: any[] = [];

  @Input()
  set selectedAccounts(value: any[]) {
    this._selectedAccounts = value || [];
    this.ensureValidPlatform();
  }

  get selectedAccounts(): any[] {
    return this._selectedAccounts;
  }

  /* ==========================================================
     Media From Composer
  ========================================================== */

  private _mediaItems: MediaItem[] = [];

  @Input()
  set mediaItems(items: MediaItem[]) {
    this._mediaItems = items || [];
    // Clean up stale IDs in aspect ratio map
    const validIds = new Set(this._mediaItems.map((m) => m.id));
    for (const key of this.nativeAspectRatios.keys()) {
      if (!validIds.has(key)) {
        this.nativeAspectRatios.delete(key);
      }
    }
    this.cdr.markForCheck();
  }

  get mediaItems(): MediaItem[] {
    return this._mediaItems;
  }

  /* ==========================================================
     Media Carousel State
  ========================================================== */

  @ViewChild('carouselRef')
  carouselRef!: ElementRef<HTMLDivElement>;

  activeMediaIndex = 0;

  /**
   * Fires on every scroll of the carousel track (swipe, trackpad,
   * or programmatic). Figures out which slide is currently centered
   * so the correct dot can be highlighted.
   */
  onCarouselScroll(): void {
    const el = this.carouselRef?.nativeElement;
    if (!el) return;

    const index = Math.round(el.scrollLeft / el.clientWidth);
    if (index !== this.activeMediaIndex) {
      this.activeMediaIndex = index;
    }
  }

  /**
   * Clicking a dot smooth-scrolls the carousel to that slide.
   * onCarouselScroll will pick up the resulting scroll and sync
   * activeMediaIndex once the animation settles.
   */
  scrollToIndex(index: number): void {
    const el = this.carouselRef?.nativeElement;
    if (!el) return;

    el.scrollTo({
      left: index * el.clientWidth,
      behavior: 'smooth',
    });

    this.activeMediaIndex = index;
  }

  /* ==========================================================
     Fallback Account Information
  ========================================================== */

  userName = 'Your Account';

  userHandle = '@youraccount';

  /* ==========================================================
     Platforms
  ========================================================== */

  selectedPlatform = 'Instagram';

  platforms = [
    { name: 'Instagram', key: 'instagram', icon: 'fa-brands fa-instagram' },
    { name: 'Facebook', key: 'facebook', icon: 'fa-brands fa-facebook' },
    { name: 'Threads', key: 'threads', icon: 'fa-solid fa-at' },
    { name: 'Twitter', key: 'twitter', icon: 'fa-brands fa-x-twitter' },
    { name: 'LinkedIn', key: 'linkedin', icon: 'fa-brands fa-linkedin' },
  ];

  /* ==========================================================
     Platform Aspect Ratio Bounds
     Real platforms don't force every post into one fixed shape —
     they preserve the media's NATIVE ratio and only clamp it if
     it falls outside what the platform's feed actually supports
     (e.g. Instagram shows portrait video close to its real shape,
     it doesn't squash everything into a square). These are the
     min/max ratios (width / height) each platform's feed allows;
     anything inside that range renders at its true native shape.
  ========================================================== */

  private readonly platformRatioBounds: Record<string, { min: number; max: number }> = {
    instagram: { min: 0.8, max: 1.91 },  // 4:5 portrait  →  1.91:1 landscape
    threads: { min: 0.8, max: 1.91 },    // same rendering engine as Instagram
    facebook: { min: 1.0, max: 1.91 },
    linkedin: { min: 0.56, max: 1.91 },  // LinkedIn tends to allow taller portrait
    twitter: { min: 1.0, max: 1.78 },    // X crops portrait more aggressively —
                                          // effectively floors close to square
  };

  /* ==========================================================
     Per-Platform Fit Strategy
     Confirmed from real screenshots: Instagram CROPS media that
     exceeds its frame (object-fit: cover, no bars). Facebook
     instead PRESERVES the full video and pillarboxes it with
     black bars (object-fit: contain) rather than cutting it off.
     These are two fundamentally different rendering strategies,
     not just different aspect-ratio numbers — so fit mode has to
     be tracked per platform alongside the ratio bounds above.

     Threads/LinkedIn/Twitter are set to 'cover' as a starting
     assumption (unconfirmed against real posts) — verify against
     an actual published post on each before trusting these, the
     same way Instagram and Facebook were confirmed above.
  ========================================================== */

  private readonly platformFitMode: Record<string, 'cover' | 'contain'> = {
    instagram: 'cover',   // confirmed via screenshot
    facebook: 'contain',  // confirmed via screenshot
    threads: 'cover',     // assumed — same engine as Instagram
    linkedin: 'cover',    // unconfirmed
    twitter: 'cover',     // unconfirmed
  };

  getMediaFitMode(): 'cover' | 'contain' {
    const key = this.getSelectedPlatform()?.key;
    return this.platformFitMode[key] ?? 'cover';
  }

  /* Native ratio (width / height) of each media item, measured
     once the actual file loads — populated by onImageLoad() /
     onVideoMetadata() below. Falls back to 1 (square) only for
     the brief moment before the file has loaded. */
  private nativeAspectRatios = new Map<string, number>();

  onImageLoad(event: Event, itemId: string): void {
    const img = event.target as HTMLImageElement;
    if (img.naturalWidth && img.naturalHeight) {
      this.nativeAspectRatios.set(itemId, img.naturalWidth / img.naturalHeight);
      this.cdr.detectChanges();
    }
  }

  onVideoMetadata(event: Event, itemId: string): void {
    const video = event.target as HTMLVideoElement;
    if (video.videoWidth && video.videoHeight) {
      this.nativeAspectRatios.set(itemId, video.videoWidth / video.videoHeight);
      this.cdr.detectChanges();
    }
  }

  getMediaAspectRatio(): string {
    // Locked to the FIRST item, not the currently active slide.
    // This matches real platform behavior: the first media you
    // upload defines the whole post's shape, and every other
    // slide in the same carousel gets fit to match it — the
    // container doesn't reshape itself as you swipe between
    // slides, the way it would if this read activeMediaIndex.
    const firstItem = this.mediaItems[0];
    const nativeRatio = firstItem ? this.nativeAspectRatios.get(firstItem.id) ?? 1 : 1;

    const key = this.getSelectedPlatform()?.key;
    const bounds = this.platformRatioBounds[key] ?? { min: 0.8, max: 1.91 };

    const clamped = Math.min(Math.max(nativeRatio, bounds.min), bounds.max);
    return `${clamped} / 1`;
  }

  /* ==========================================================
     Platform Selection
  ========================================================== */

  selectPlatform(platform: string): void {
    this.selectedPlatform = platform;
  }

  isPlatformSelected(platformName: string): boolean {
    return this.selectedAccounts.some((account) => account.name === platformName);
  }

  getSelectedAccount(): any {
    return this.selectedAccounts.find((account) => account.name === this.selectedPlatform);
  }

  getSelectedPlatform(): any {
    return this.platforms.find((platform) => platform.name === this.selectedPlatform);
  }

  private ensureValidPlatform(): void {
    if (this.selectedAccounts.length === 0) {
      return;
    }

    const currentPlatformExists = this.selectedAccounts.some(
      (account) => account.name === this.selectedPlatform,
    );

    if (!currentPlatformExists) {
      this.selectedPlatform = this.selectedAccounts[0].name;
    }
  }
}