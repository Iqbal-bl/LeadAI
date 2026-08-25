import {
  Component,
  EventEmitter,
  Output,
  OnInit,
  Input,
  OnChanges,
  SimpleChanges,
} from '@angular/core';

import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { CheckboxModule } from 'primeng/checkbox';
import { TooltipModule } from 'primeng/tooltip';

import { MediaItem } from '../media-toolbar/media-toolbar';

@Component({
  selector: 'app-account-selector',
  standalone: true,

  imports: [CommonModule, FormsModule, CheckboxModule, TooltipModule],

  templateUrl: './account-selector.html',
  styleUrl: './account-selector.scss',
})
export class AccountSelector implements OnInit, OnChanges {
  /* ==========================================================
     Media Items From Composer
  ========================================================== */

  @Input()
  mediaItems: MediaItem[] = [];

  /* ==========================================================
     Selected Accounts
  ========================================================== */

  selectedAccounts: any[] = [];

  /* ==========================================================
     Send Selected Accounts To Parent
  ========================================================== */

  @Output()
  accountsChange = new EventEmitter<any[]>();

  /* ==========================================================
     Available Accounts
  ========================================================== */

  accounts = [
    {
      name: 'Facebook',
      key: 'facebook',
      icon: 'fa-brands fa-facebook-f',
    },

    {
      name: 'Instagram',
      key: 'instagram',
      icon: 'fa-brands fa-instagram',
    },

    {
      name: 'Threads',
      key: 'threads',
      icon: 'fa-solid fa-at',
    },

    {
      name: 'Twitter',
      key: 'twitter',
      icon: 'fa-brands fa-x-twitter',
    },

    {
      name: 'LinkedIn',
      key: 'linkedin',
      icon: 'fa-brands fa-linkedin-in',
    },
  ];

  /* ==========================================================
     Constructor
  ========================================================== */

  constructor() {
    /*
      Default selected platforms

      Currently:
      Facebook
      Instagram
      LinkedIn
    */

    this.selectedAccounts = [this.accounts[0], this.accounts[1], this.accounts[4]];
  }

  /* ==========================================================
     Initial Selection
  ========================================================== */

  ngOnInit(): void {
    this.emitSelectedAccounts();
  }

  /* ==========================================================
     Detect Media Changes
  ========================================================== */

  ngOnChanges(changes: SimpleChanges): void {
    if (!changes['mediaItems']) {
      return;
    }

    console.log('AccountSelector media changed:', this.mediaItems);

    /*
      Remove platforms that are no longer
      compatible with the selected media.
    */

    const previousLength = this.selectedAccounts.length;

    this.selectedAccounts = this.selectedAccounts.filter((account) =>
      this.isPlatformSupported(account),
    );

    /*
      Notify parent only when a selected
      account was actually removed.
    */

    if (this.selectedAccounts.length !== previousLength) {
      console.log('Unsupported accounts removed:', this.selectedAccounts);

      this.emitSelectedAccounts();
    }
  }

  /* ==========================================================
     Emit Selected Accounts
  ========================================================== */

  private emitSelectedAccounts(): void {
    this.accountsChange.emit([...this.selectedAccounts]);
  }

  /* ==========================================================
     Check Selected
  ========================================================== */

  isSelected(account: any): boolean {
    return this.selectedAccounts.some((selected) => selected.key === account.key);
  }

  /* ==========================================================
     Check Platform Compatibility
  ========================================================== */

  isPlatformSupported(account: any): boolean {
    /* --------------------------------------------------------
       No media selected

       All platforms remain enabled.
    -------------------------------------------------------- */

    if (this.mediaItems.length === 0) {
      return true;
    }

    /* --------------------------------------------------------
       Instagram

       Currently supports:
       - single image
       - single video
       - multiple images
       - mixed image + video carousel
    -------------------------------------------------------- */

    if (account.key === 'instagram') {
      return true;
    }

    /* --------------------------------------------------------
       Facebook

       Currently supports:
       - single image
       - single video
       - multiple images

       Does NOT support:
       - image + video
    -------------------------------------------------------- */

    if (account.key === 'facebook') {
      const hasImage = this.mediaItems.some((item) => item.kind === 'image');

      const hasVideo = this.mediaItems.some((item) => item.kind === 'video');

      /*
        Mixed image + video
      */

      if (hasImage && hasVideo) {
        return false;
      }

      return true;
    }

    /* --------------------------------------------------------
       Other Platforms

       Compatibility rules have not been
       implemented yet.

       Keep them enabled for now.
    -------------------------------------------------------- */

    return true;
  }

  /* ==========================================================
     Toggle Account
  ========================================================== */

  toggleAccount(account: any): void {
    /* --------------------------------------------------------
       Don't allow unsupported platform
    -------------------------------------------------------- */

    if (!this.isPlatformSupported(account)) {
      console.log(`${account.name} is not supported for current media`);

      return;
    }

    /* --------------------------------------------------------
       Find Account
    -------------------------------------------------------- */

    const index = this.selectedAccounts.findIndex((selected) => selected.key === account.key);

    /* --------------------------------------------------------
       Remove Account
    -------------------------------------------------------- */

    if (index > -1) {
      this.selectedAccounts.splice(index, 1);
    }

    /* --------------------------------------------------------
       Add Account
    -------------------------------------------------------- */
    else {
      this.selectedAccounts.push(account);
    }

    /* --------------------------------------------------------
       Notify Parent
    -------------------------------------------------------- */

    this.emitSelectedAccounts();
  }

  /* ==========================================================
     Unsupported Tooltip Message
  ========================================================== */

  unsupportedMessage(account: any): string {
    const kinds = new Set(this.mediaItems.map((item) => item.kind));

    /* --------------------------------------------------------
       No media
    -------------------------------------------------------- */

    if (kinds.size === 0) {
      return `${account.name} doesn't support this media type`;
    }

    /* --------------------------------------------------------
       Mixed Media
    -------------------------------------------------------- */

    if (kinds.size > 1) {
      return `${account.name} doesn't support mixed media in one post`;
    }

    /* --------------------------------------------------------
       Single Media Type
    -------------------------------------------------------- */

    const kind = [...kinds][0];

    const kindLabel =
      kind === 'image'
        ? 'image posts'
        : kind === 'video'
          ? 'video posts'
          : kind === 'document'
            ? 'document attachments'
            : 'this media type';

    return `${account.name} doesn't support ${kindLabel}`;
  }
}
