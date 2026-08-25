import {
  Component,
  ChangeDetectorRef
} from '@angular/core';

import { CommonModule } from '@angular/common';

import { AccountSelector } from '../../composer/account-selector/account-selector';
import { AiTools } from '../../composer/ai-tools/ai-tools';
import { ScheduleBar } from '../../composer/schedule-bar/schedule-bar';
import { PostEditor } from '../../composer/post-editor/post-editor';

import {
  MediaToolbar,
  MediaItem
} from '../../composer/media-toolbar/media-toolbar';

import { ComposerActions } from '../../composer/composer-actions/composer-actions';
import { LivePreview } from '../../composer/live-preview/live-preview';

import { PublishService } from '../../services/publish.services';

import { ToastModule } from 'primeng/toast';
import { MessageService } from 'primeng/api';


@Component({
  selector: 'app-composer',
  standalone: true,

  imports: [
    CommonModule,
    AccountSelector,
    ToastModule,
    AiTools,
    ScheduleBar,
    PostEditor,
    MediaToolbar,
    ComposerActions,
    LivePreview
  ],

  providers: [
    MessageService
  ],

  templateUrl: './composer.html',
  styleUrl: './composer.scss'
})
export class ComposerComponent {

  /* ==========================================================
     Publishing State
  ========================================================== */

  isPublishing = false;


  /* ==========================================================
     Reset Version
  ========================================================== */

  resetVersion = 0;


  /* ==========================================================
     Publish Mode
  ========================================================== */

  publishMode: 'now' | 'schedule' = 'now';


  /* ==========================================================
     Post Content
  ========================================================== */

  postContent = '';


  /* ==========================================================
     Selected Accounts
  ========================================================== */

  selectedAccounts: any[] = [];


  /* ==========================================================
     Selected Media
  ========================================================== */

  mediaItems: MediaItem[] = [];


  /* ==========================================================
     Constructor
  ========================================================== */

  constructor(
    private publishService: PublishService,
    private messageService: MessageService,
    private cdr: ChangeDetectorRef
  ) {}


  /* ==========================================================
     Account Selection
  ========================================================== */

  onAccountsChange(
    accounts: any[]
  ): void {

    this.selectedAccounts = accounts;

    console.log(
      'Selected Accounts:',
      this.selectedAccounts
    );

  }


  /* ==========================================================
     Post Content Change
  ========================================================== */

  onPostContentChange(
    content: string
  ): void {

    this.postContent = content;

  }


  /* ==========================================================
     Media Selection
  ========================================================== */

  onMediaChange(
    mediaItems: MediaItem[]
  ): void {

    this.mediaItems = mediaItems;

    console.log(
      'Media Items:',
      this.mediaItems
    );

  }


  /* ==========================================================
     Publish
  ========================================================== */

  onPublish(): void {

    /* ----------------------------------------------------------
       Prevent Double Click
    ---------------------------------------------------------- */

    if (this.isPublishing) {

      return;

    }


    console.log(
      this.publishMode === 'schedule'
        ? 'Schedule Post'
        : 'Publish Now'
    );


    console.log(
      'Post Content:',
      this.postContent
    );


    console.log(
      'Media Items:',
      this.mediaItems
    );


    console.log(
      'Selected Accounts:',
      this.selectedAccounts
    );


    /* ----------------------------------------------------------
       Validate Platform Selection
    ---------------------------------------------------------- */

    if (
      !this.selectedAccounts ||
      this.selectedAccounts.length === 0
    ) {

      this.messageService.add({
        key: 'publish-toast',
        severity: 'error',
        summary: 'No platform selected',
        detail:
          'Please select at least one platform before publishing.',
        life: 4000
      });

      return;

    }


    /* ----------------------------------------------------------
       Validate Media
    ---------------------------------------------------------- */

    const publishableMedia =
      this.mediaItems.filter(
        item =>
          item.kind === 'image' ||
          item.kind === 'video'
      );


    if (
      publishableMedia.length === 0
    ) {

      this.messageService.add({
        key: 'publish-toast',
        severity: 'error',
        summary: 'No media selected',
        detail:
          'Please add an image or video before publishing.',
        life: 4000
      });

      return;

    }


    /* ----------------------------------------------------------
       Start Loading
    ---------------------------------------------------------- */

    this.isPublishing = true;


    /* ----------------------------------------------------------
       Publish
    ---------------------------------------------------------- */

    this.publishSelectedMedia(
      publishableMedia
    );

  }


  /* ==========================================================
     Publish Selected Media
     
     IMPORTANT:
     No Cloudinary upload here.
     
     The File objects are passed to PublishService.
     PublishService converts them to Base64 and sends
     them to the unified backend API.
  ========================================================== */

  private publishSelectedMedia(
    mediaToPublish: MediaItem[]
  ): void {

    console.log(
      'Publishing media:',
      mediaToPublish
    );


    /* ----------------------------------------------------------
       Publish Through Unified Backend
    ---------------------------------------------------------- */

    this.publishService
      .publish(
        this.selectedAccounts,
        this.postContent,
        mediaToPublish
      )
      .subscribe({
        next: (response) => {
          console.log('Publish response:', response);
          this.handlePublishResponse(response);
        },
        error: (error) => {
          console.error('Publish failed:', error);
          this.handlePublishError(error);
        }
      });
  }

  /* ==========================================================
     Handle Publish Response (Full / Partial Success / Error)
  ========================================================== */

  private handlePublishResponse(response: any): void {
    this.isPublishing = false;

    const formatName = (p: string) => {
      if (!p) return '';
      const clean = p.trim().toLowerCase();
      if (clean === 'facebook') return 'Facebook';
      if (clean === 'instagram') return 'Instagram';
      if (clean === 'twitter' || clean === 'x') return 'X / Twitter';
      if (clean === 'linkedin') return 'LinkedIn';
      if (clean === 'threads') return 'Threads';
      return p.charAt(0).toUpperCase() + p.slice(1);
    };

    const selectedKeys = this.selectedAccounts.map((a) => a.key.toLowerCase());
    const selectedNames = this.selectedAccounts.map((a) => a.name || formatName(a.key));

    let successful: string[] = [];
    let failed: { platform: string; reason?: string }[] = [];

    // 1. If response is an Array of results
    if (Array.isArray(response)) {
      for (const item of response) {
        const plat = formatName(item.platform || item.account || '');
        const isSuccess =
          item.status === 'success' ||
          item.status === 'published' ||
          item.success === true ||
          item.id ||
          item.post_id;
        if (isSuccess) {
          successful.push(plat);
        } else {
          failed.push({
            platform: plat || 'Platform',
            reason: item.error || item.message || item.reason,
          });
        }
      }
    }
    // 2. If response has explicit arrays like successful_platforms / failed_platforms
    else if (
      response &&
      (response.successful_platforms ||
        response.failed_platforms ||
        response.published ||
        response.failed)
    ) {
      if (Array.isArray(response.successful_platforms)) {
        successful = response.successful_platforms.map((p: string) => formatName(p));
      } else if (Array.isArray(response.published)) {
        successful = response.published.map((p: string) => formatName(p));
      }

      if (Array.isArray(response.failed_platforms)) {
        failed = response.failed_platforms.map((p: any) =>
          typeof p === 'string'
            ? { platform: formatName(p) }
            : { platform: formatName(p.platform || p.name), reason: p.error || p.message },
        );
      } else if (Array.isArray(response.failed)) {
        failed = response.failed.map((p: any) =>
          typeof p === 'string'
            ? { platform: formatName(p) }
            : { platform: formatName(p.platform || p.name), reason: p.error || p.message },
        );
      }

      if (response.errors && typeof response.errors === 'object') {
        Object.entries(response.errors).forEach(([k, v]) => {
          const platName = formatName(k);
          if (!failed.some((f) => f.platform.toLowerCase() === platName.toLowerCase())) {
            failed.push({ platform: platName, reason: String(v) });
          }
        });
      }
    }
    // 3. If response has a nested results / platforms / data object map
    else if (
      response &&
      (response.results || response.platforms || response.data) &&
      typeof (response.results || response.platforms || response.data) === 'object'
    ) {
      const resultsMap = response.results || response.platforms || response.data;
      Object.entries(resultsMap).forEach(([key, val]: [string, any]) => {
        const platName = formatName(key);
        if (val && typeof val === 'object') {
          const isSuccess =
            val.status === 'success' ||
            val.status === 'published' ||
            val.success === true ||
            val.id ||
            val.post_id;
          if (isSuccess) {
            successful.push(platName);
          } else {
            failed.push({
              platform: platName,
              reason: val.error || val.message || 'Failed to publish',
            });
          }
        } else if (val === true || val === 'success' || val === 'published') {
          successful.push(platName);
        } else if (val === false || val === 'failed' || val === 'error') {
          failed.push({ platform: platName });
        }
      });
    }
    // 4. If root object directly has platform keys (e.g. { instagram: { ... }, facebook: { ... } })
    else if (response && typeof response === 'object') {
      let matchedPlatform = false;
      selectedKeys.forEach((k) => {
        if (response[k] !== undefined) {
          matchedPlatform = true;
          const val = response[k];
          const platName = formatName(k);
          if (val && typeof val === 'object') {
            const isSuccess =
              val.status === 'success' ||
              val.status === 'published' ||
              val.success === true ||
              val.id ||
              val.post_id;
            if (isSuccess) {
              successful.push(platName);
            } else {
              failed.push({
                platform: platName,
                reason: val.error || val.message,
              });
            }
          } else if (val === true || val === 'success') {
            successful.push(platName);
          } else {
            failed.push({ platform: platName });
          }
        }
      });

      if (!matchedPlatform) {
        if (
          response.status === 'partial_success' ||
          response.status === 'partial' ||
          response.partial === true
        ) {
          successful = [selectedNames[0] || '1 platform'];
          failed = [
            {
              platform: selectedNames.slice(1).join(', ') || 'other platform',
              reason: response.message || response.error,
            },
          ];
        } else if (response.status === 'error' || response.success === false) {
          failed = selectedNames.map((name) => ({
            platform: name,
            reason: response.message || response.error,
          }));
        } else {
          successful = selectedNames;
        }
      }
    } else {
      successful = selectedNames;
    }

    successful = [...new Set(successful)];

    // Helper to format clean platform list with "and"
    const joinNames = (names: string[]) => {
      const unique = [...new Set(names.filter(Boolean))];
      if (unique.length === 0) return '';
      if (unique.length === 1) return unique[0];
      if (unique.length === 2) return `${unique[0]} and ${unique[1]}`;
      return `${unique.slice(0, -1).join(', ')}, and ${unique[unique.length - 1]}`;
    };

    const hasSuccess = successful.length > 0;
    const hasFailed = failed.length > 0;

    /* ----------------------------------------------------------
       CASE 1: Partial Success (Some published, some failed)
    ---------------------------------------------------------- */
    if (hasSuccess && hasFailed) {
      const successStr = joinNames(successful);
      const failedStr = joinNames(failed.map((f) => f.platform));

      this.messageService.add({
        key: 'publish-toast',
        severity: 'warn',
        summary: 'Partially Published',
        detail: `Published to ${successStr}, but not to ${failedStr}.`,
        life: 6000,
      });

      // Keep only failed platforms selected so the user can easily retry
      const failedNames = failed.map((f) => f.platform.toLowerCase());
      const remainingAccounts = this.selectedAccounts.filter((a) =>
        failedNames.some(
          (fn) => a.name.toLowerCase().includes(fn) || a.key.toLowerCase().includes(fn),
        ),
      );

      if (remainingAccounts.length > 0) {
        this.selectedAccounts = remainingAccounts;
      }
      this.cdr.detectChanges();
      return;
    }

    /* ----------------------------------------------------------
       CASE 2: Full Failure
    ---------------------------------------------------------- */
    if (!hasSuccess && hasFailed) {
      const failedStr = joinNames(failed.map((f) => f.platform));

      this.messageService.add({
        key: 'publish-toast',
        severity: 'error',
        summary: "Publish Failed",
        detail: `Failed to publish to ${failedStr}.`,
        life: 5000,
      });
      return;
    }

    /* ----------------------------------------------------------
       CASE 3: Full Success
    ---------------------------------------------------------- */
    const successListStr =
      successful.length > 0 ? joinNames(successful) : joinNames(selectedNames);

    this.messageService.add({
      key: 'publish-toast',
      severity: 'success',
      summary: 'Post Published',
      detail: `Published to ${successListStr}.`,
      life: 4500,
    });

    this.resetComposer();
  }

  /* ==========================================================
     Handle Publish Error (HTTP Error)
  ========================================================== */

  private handlePublishError(error: any): void {
    this.isPublishing = false;

    // If HTTP error response contains a partial result body
    if (error?.error && typeof error.error === 'object') {
      const errBody = error.error;
      if (
        errBody.results ||
        errBody.platforms ||
        errBody.successful_platforms ||
        errBody.failed_platforms ||
        errBody.errors
      ) {
        this.handlePublishResponse(errBody);
        return;
      }
    }

    const platformNames = this.selectedAccounts.map(
      (a) => a.name || a.key.charAt(0).toUpperCase() + a.key.slice(1),
    );
    const targetStr =
      platformNames.length > 1
        ? `${platformNames.slice(0, -1).join(', ')} and ${platformNames[platformNames.length - 1]}`
        : platformNames[0] || 'selected platforms';

    this.messageService.add({
      key: 'publish-toast',
      severity: 'error',
      summary: "Couldn't publish",
      detail: `Failed to publish to ${targetStr}.`,
      life: 5000,
    });
  }


  /* ==========================================================
     Toast Close
  ========================================================== */

  onToastClose(): void {

    this.messageService.clear(
      'publish-toast'
    );

  }


  /* ==========================================================
     Reset Composer
  ========================================================== */

  resetComposer(): void {

    console.log(
      'Resetting composer...'
    );


    /* ----------------------------------------------------------
       Clear Parent State
    ---------------------------------------------------------- */

    this.postContent = '';

    this.mediaItems = [];


    /* ----------------------------------------------------------
       Tell Child Components To Reset
    ---------------------------------------------------------- */

    this.resetVersion++;


    console.log(
      'Reset version:',
      this.resetVersion
    );


    /* ----------------------------------------------------------
       Force Change Detection
    ---------------------------------------------------------- */

    this.cdr.detectChanges();

  }


  /* ==========================================================
     Save Draft
  ========================================================== */

  onSaveDraft(): void {

    console.log(
      'Save Draft'
    );


    console.log(
      'Post Content:',
      this.postContent
    );


    console.log(
      'Media Items:',
      this.mediaItems
    );

  }


  /* ==========================================================
     Cancel
  ========================================================== */

  onCancel(): void {

    console.log(
      'Cancel'
    );

  }

}