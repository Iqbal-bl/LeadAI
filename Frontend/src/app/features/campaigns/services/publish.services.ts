import { Injectable } from '@angular/core';

import { Observable, from, throwError } from 'rxjs';

import { switchMap } from 'rxjs/operators';

import { PublishApiService } from './publish-api.service';

import { MediaItem } from '../composer/media-toolbar/media-toolbar';

/* ==========================================================
   Account
========================================================== */

export interface PublishAccount {
  name: string;

  key: 'instagram' | 'facebook' | 'threads' | 'twitter' | 'linkedin';

  icon: string;
}

/* ==========================================================
   Media Payload
========================================================== */

export interface PublishMedia {
  type: 'image' | 'video';

  mime_type: string;

  data: string;
}

/* ==========================================================
   Unified Publish Request
========================================================== */

export interface UnifiedPublishRequest {
  caption: string;

  platforms: string[];

  media: PublishMedia[];
}

/* ==========================================================
   Publish Service
========================================================== */

@Injectable({
  providedIn: 'root',
})
export class PublishService {
  constructor(private publishApiService: PublishApiService) { }

  /* ==========================================================
     Publish To Selected Platforms
  ========================================================== */

  publish(accounts: PublishAccount[], caption: string, mediaItems: MediaItem[]): Observable<any> {
    /* ========================================================
       Validate Platforms
    ======================================================== */

    if (!accounts || accounts.length === 0) {
      return throwError(() => new Error('Please select at least one platform.'));
    }

    /* ========================================================
       Validate Media
    ======================================================== */

    if (!mediaItems || mediaItems.length === 0) {
      return throwError(() => new Error('Please select at least one media file.'));
    }

    /* ========================================================
       Selected Platforms
    ======================================================== */

    const platforms = [...new Set(accounts.map((account) => account.key))];

    console.log('Selected platforms:', platforms);

    /* ========================================================
       Convert Media
    ======================================================== */

    const mediaPromises = mediaItems.map((item) => this.fileToPublishMedia(item));

    /* ========================================================
       Wait For All Media
    ======================================================== */

    return from(Promise.all(mediaPromises)).pipe(
      switchMap((media: PublishMedia[]) => {
        /* ==================================================
             Create Backend Request
          ================================================== */

        const request: UnifiedPublishRequest = {
          caption,

          platforms,

          media,
        };

        console.log('======================================');

        console.log('Unified Publish Request');

        console.log('Platforms:', platforms);

        console.log('Media count:', media.length);

        console.log(
          'Media types:',
          media.map((item) => ({
            type: item.type,
            mime_type: item.mime_type,
          })),
        );

        console.log('======================================');

        /* ==================================================
             ONE Backend Request
          ================================================== */

        return this.publishApiService.publish(request);
      }),
    );
  }

  /* ==========================================================
     Convert File To Backend Media Format
     (Enforces Graph API Image Specifications: JPEG, 4:5 to 1.91:1, 320-1440px)
  ========================================================== */

  private async fileToPublishMedia(item: MediaItem): Promise<PublishMedia> {
    const file = item.file;
    if (!file) {
      throw new Error('Media file is missing.');
    }

    // Handle Video Files
    if (file.type.startsWith('video/')) {
      const base64Data = await this.readRawBase64(file);
      return {
        type: 'video',
        mime_type: file.type || 'video/mp4',
        data: base64Data,
      };
    }

    // Handle Image Files: Automatically verify & crop to valid aspect ratio (4:5 to 1.91:1) and JPEG
    if (file.type.startsWith('image/')) {
      try {
        const processedBlob = await this.enforceImageSpecs(file);
        const base64Data = await this.readRawBase64(processedBlob);
        return {
          type: 'image',
          mime_type: 'image/jpeg',
          data: base64Data,
        };
      } catch (err) {
        console.warn('Image specification auto-check fallback:', err);
        const base64Data = await this.readRawBase64(file);
        return {
          type: 'image',
          mime_type: file.type || 'image/jpeg',
          data: base64Data,
        };
      }
    }

    throw new Error(`Unsupported media type: ${file.type}`);
  }

  /* ----------------------------------------------------------
     Enforce Graph API Image Specifications
     - Format: JPEG
     - Aspect ratio: 4:5 (0.8) to 1.91:1 (1.91)
     - Min width: 320px
     - Max width: 1440px
     - Color Space: sRGB
  ---------------------------------------------------------- */

  private enforceImageSpecs(file: File | Blob): Promise<Blob> {
    return new Promise((resolve, reject) => {
      const img = new Image();
      const objectUrl = URL.createObjectURL(file);

      img.onload = () => {
        URL.revokeObjectURL(objectUrl);

        const origWidth = img.naturalWidth || img.width;
        const origHeight = img.naturalHeight || img.height;

        const aspect = origWidth / origHeight;
        const MIN_ASPECT = 4 / 5; // 0.8 (Portrait limit)
        const MAX_ASPECT = 1.91 / 1; // 1.91 (Landscape limit)

        let cropX = 0;
        let cropY = 0;
        let cropWidth = origWidth;
        let cropHeight = origHeight;

        // Taller than 4:5 -> center-crop top & bottom
        if (aspect < MIN_ASPECT) {
          cropHeight = Math.round(cropWidth / MIN_ASPECT);
          cropY = Math.round((origHeight - cropHeight) / 2);
        }
        // Wider than 1.91:1 -> center-crop left & right
        else if (aspect > MAX_ASPECT) {
          cropWidth = Math.round(cropHeight * MAX_ASPECT);
          cropX = Math.round((origWidth - cropWidth) / 2);
        }

        // Clamp width between 320px and 1440px
        let targetWidth = cropWidth;
        let targetHeight = cropHeight;

        if (targetWidth > 1440) {
          const scale = 1440 / targetWidth;
          targetWidth = 1440;
          targetHeight = Math.round(cropHeight * scale);
        } else if (targetWidth < 320) {
          const scale = 320 / targetWidth;
          targetWidth = 320;
          targetHeight = Math.round(cropHeight * scale);
        }

        const canvas = document.createElement('canvas');
        canvas.width = targetWidth;
        canvas.height = targetHeight;
        const ctx = canvas.getContext('2d');

        if (!ctx) {
          reject(new Error('Canvas 2D context not available'));
          return;
        }

        // Clean white background for any transparency conversion
        ctx.fillStyle = '#FFFFFF';
        ctx.fillRect(0, 0, targetWidth, targetHeight);

        // Draw cropped and scaled image
        ctx.drawImage(
          img,
          cropX,
          cropY,
          cropWidth,
          cropHeight,
          0,
          0,
          targetWidth,
          targetHeight,
        );

        canvas.toBlob(
          (blob) => {
            if (blob) {
              resolve(blob);
            } else {
              reject(new Error('Failed to encode image to JPEG blob'));
            }
          },
          'image/jpeg',
          0.92,
        );
      };

      img.onerror = () => {
        URL.revokeObjectURL(objectUrl);
        reject(new Error('Failed to load image for aspect ratio verification'));
      };

      img.src = objectUrl;
    });
  }

  /* ----------------------------------------------------------
     Read Blob or File to Base64 String (Without Data URL Prefix)
  ---------------------------------------------------------- */

  private readRawBase64(file: File | Blob): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result;
        if (typeof result !== 'string') {
          reject(new Error('Failed to read file as data string'));
          return;
        }
        const commaIndex = result.indexOf(',');
        if (commaIndex === -1) {
          reject(new Error('Invalid Base64 format'));
          return;
        }
        resolve(result.substring(commaIndex + 1));
      };
      reader.onerror = () => reject(reader.error || new Error('FileReader error'));
      reader.readAsDataURL(file);
    });
  }
}
