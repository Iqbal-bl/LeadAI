import { Component, OnInit, ViewChild } from '@angular/core';
import { Router } from '@angular/router';
import { SharedModule } from '../../../shared/shared.module';
import { ContactListService } from '../../../services/contact-list.service';
import {
  ContactList,
  ContactListPreview,
} from '../../../models/contact-list.models';
import { MessageService, MenuItem } from 'primeng/api';
import { Menu } from 'primeng/menu';
import { ConfirmationService } from '../../../shared/services/confirmation.service';
import { ContactListUploadComponent } from '../contact-list-upload/contact-list-upload.component';

@Component({
  selector: 'app-contact-list-list',
  standalone: true,
  imports: [SharedModule, ContactListUploadComponent],
  templateUrl: './contact-list-list.component.html',
  styleUrl: './contact-list-list.component.scss',
})
export class ContactListListComponent implements OnInit {
  @ViewChild('listActionMenu') listActionMenu!: Menu;
  activeListMenuItems: MenuItem[] = [];

  lists: ContactList[] = [];
  loading = true;

  showUpload = false;
  showFromLeads = false;

  // Items Dialog variables
  showItemsDialog = false;
  activeList: ContactList | null = null;
  listItems: any[] = [];
  displayedColumns: string[] = [];
  itemsLoading = false;
  totalItemsCount = 0;
  currentPage = 1;
  pageSize = 50;
  onlyInvalid = false;

  constructor(
    private contactListService: ContactListService,
    private messageService: MessageService,
    private confirmationService: ConfirmationService,
    private router: Router,
  ) {}

  get totalRecordsCount(): number {
    return this.lists.reduce((acc, curr) => acc + (curr.total_rows ?? curr.total_count ?? 0), 0);
  }

  openRowMenu(event: Event, list: ContactList): void {
    event.stopPropagation();
    this.activeListMenuItems = [
      {
        label: 'Launch Campaign',
        icon: 'pi pi-megaphone',
        command: () => this.launchCampaign(list),
      },
      {
        label: 'View Contacts',
        icon: 'pi pi-eye',
        command: () => this.viewItems(list),
      },
      { separator: true },
      {
        label: 'Delete List',
        icon: 'pi pi-trash',
        styleClass: 'text-red-500',
        command: () => this.deleteList(list),
      },
    ];
    this.listActionMenu.toggle(event);
  }

  launchCampaign(list: ContactList): void {
    this.router.navigate(['/client/campaigns'], {
      queryParams: { audienceId: list.id, create: 'true' },
    });
  }

  ngOnInit(): void {
    this.loadLists();
  }

  loadLists(): void {
    this.loading = true;
    this.contactListService.getLists().subscribe({
      next: (res: any) => {
        const raw = Array.isArray(res) ? res : res?.items || [];
        this.lists = raw.map((l: any) => ({
          ...l,
          total_rows: l.total_rows ?? l.total_count ?? 0,
        }));
        this.loading = false;
      },
      error: () => {
        this.lists = [];
        this.loading = false;
      },
    });
  }

  openUpload(): void {
    this.showUpload = true;
  }

  openFromLeads(): void {
    this.showFromLeads = true;
  }

  onUploadComplete(): void {
    this.showUpload = false;
    this.showFromLeads = false;
    this.loadLists();
    this.messageService.add({
      severity: 'success',
      summary: 'List Created',
      detail: 'Your contact list has been saved successfully.',
    });
  }

  onUploadClose(): void {
    this.showUpload = false;
    this.showFromLeads = false;
  }

  deleteList(list: ContactList): void {
    this.confirmationService.confirm({
      message: `Are you sure you want to delete "${list.name}"?`,
      header: 'Delete Contact List',
      icon: 'pi pi-trash',
      acceptButtonStyleClass: 'p-button-danger',
      accept: () => {
        this.contactListService.deleteList(list.id).subscribe({
          next: () => {
            this.loadLists();
            this.messageService.add({
              severity: 'success',
              summary: 'Deleted',
              detail: 'Contact list has been removed.',
            });
          },
          error: (err) => {
            if (err.status === 409) {
              const campaignName = err.error?.campaign_name || 'a campaign';
              this.messageService.add({
                severity: 'error',
                summary: 'Cannot Delete',
                detail: `This list is in use by campaign "${campaignName}". Remove or complete the campaign first.`,
                life: 6000,
              });
            } else {
              this.messageService.add({
                severity: 'error',
                summary: 'Error',
                detail: 'Failed to delete contact list.',
              });
            }
          },
        });
      },
    });
  }

  viewItems(list: ContactList): void {
    this.activeList = list;
    this.showItemsDialog = true;
    this.onlyInvalid = false;
    this.loadListItems(1);
  }

  loadListItems(page: number = 1): void {
    if (!this.activeList) return;
    this.itemsLoading = true;
    this.currentPage = page;
    this.contactListService
      .getListItems(this.activeList.id, {
        page: this.currentPage,
        page_size: this.pageSize,
        only_invalid: this.onlyInvalid,
      })
      .subscribe({
        next: (res: any) => {
          this.listItems = res.items || [];
          this.totalItemsCount = res.total_items || 0;
          this.updateColumns();
          this.itemsLoading = false;
        },
        error: () => {
          this.listItems = [];
          this.totalItemsCount = 0;
          this.displayedColumns = [];
          this.itemsLoading = false;
        },
      });
  }

  onPageChange(event: any): void {
    const newPage = event.first / event.rows + 1;
    this.loadListItems(newPage);
  }

  toggleOnlyInvalid(): void {
    this.loadListItems(1);
  }

  updateColumns(): void {
    if (!this.activeList) {
      this.displayedColumns = [];
      return;
    }
    if (this.activeList.columns && this.activeList.columns.length > 0) {
      this.displayedColumns = this.activeList.columns.filter(
        (c) => c && c.trim() !== '',
      );
      return;
    }
    const cols = new Set<string>();
    cols.add('name');
    cols.add('phone_masked');
    for (const item of this.listItems) {
      if (item.fields) {
        Object.keys(item.fields).forEach((key) => cols.add(key));
      }
    }
    this.displayedColumns = Array.from(cols);
  }

  getColumns(): string[] {
    return this.displayedColumns;
  }

  getColValue(item: any, col: string): string {
    if (!col) return '—';
    const colLower = col.toLowerCase();
    if (colLower === 'name') {
      return item.name || '—';
    }
    if (colLower === 'phone' || colLower === 'phone_masked') {
      return item.phone_masked || item.phone || '—';
    }
    if (colLower === 'country_code') {
      return item.country_code || '—';
    }
    if (colLower === 'row_number') {
      return item.row_number || '—';
    }
    if (
      item.fields &&
      item.fields[col] !== undefined &&
      item.fields[col] !== null
    ) {
      return String(item.fields[col]) || '—';
    }
    if (item[col] !== undefined && item[col] !== null) {
      return String(item[col]) || '—';
    }
    return '—';
  }
}
