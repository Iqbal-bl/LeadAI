import { Component, EventEmitter, Input, OnInit, Output } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { ChannelService } from '../../../services/channel.service';
import { Channel, ChannelContact } from '../../../models/channel.models';

@Component({
  selector: 'app-channel-contacts',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './channel-contacts.component.html',
  styleUrl: './channel-contacts.component.scss',
})
export class ChannelContactsComponent implements OnInit {
  @Input() visible = false;
  @Input() channel!: Channel;
  @Output() close = new EventEmitter<void>();

  contacts: any[] = [];
  loading = true;
  currentPage = 1;
  pageSize = 50;
  totalRecords = 0;

  constructor(private channelService: ChannelService) {}

  get activeSessionCount(): number {
    return this.contacts.filter((c) => c.in_session_window).length;
  }

  get optedOutCount(): number {
    return this.contacts.filter((c) => c.opted_out).length;
  }

  ngOnInit(): void {
    if (this.channel?.id) {
      this.loadContacts(1);
    }
  }

  loadContacts(page: number = 1): void {
    if (!this.channel?.id) return;
    this.loading = true;
    this.currentPage = page;

    this.channelService
      .getChannelContacts(this.channel.id, this.currentPage, this.pageSize)
      .subscribe({
        next: (res: any) => {
          if (Array.isArray(res)) {
            this.contacts = res;
            this.totalRecords = res.length;
          } else {
            this.contacts = res?.items || res?.data || res?.contacts || [];
            this.totalRecords =
              res?.total_items ?? res?.total ?? res?.total_count ?? this.contacts.length;
            this.currentPage = res?.page ?? this.currentPage;
            this.pageSize = res?.page_size ?? this.pageSize;
          }
          this.loading = false;
        },
        error: () => {
          this.contacts = [];
          this.totalRecords = 0;
          this.loading = false;
        },
      });
  }

  onPageChange(event: any): void {
    const page = Math.floor((event.first || 0) / (event.rows || this.pageSize)) + 1;
    this.pageSize = event.rows || this.pageSize;
    this.loadContacts(page);
  }

  onClose(): void {
    this.close.emit();
  }
}

