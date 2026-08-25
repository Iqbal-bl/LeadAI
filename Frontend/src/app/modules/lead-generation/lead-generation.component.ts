import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { ChatService } from '../../services/chat.service';
import { SharedModule } from '../../shared/shared.module';
import { PublicCompany, ChatStartPayload } from '../../models/chat.models';
import { Subscription } from 'rxjs';

@Component({
  selector: 'app-lead-generation',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './lead-generation.component.html',
  styleUrl: './lead-generation.component.scss'
})
export class LeadGenerationComponent implements OnInit, OnDestroy {
  private chatService = inject(ChatService);

  companies: PublicCompany[] = [];
  selectedCompany: PublicCompany | null = null;

  // Form Fields
  displayName = '';
  email = '';
  phone = '';
  whatsapp = '';
  instagram = '';
  selectedLanguage = 'English';

  languages = [
    { label: 'English', value: 'English' },
    { label: 'Spanish', value: 'Spanish' },
    { label: 'French', value: 'French' },
    { label: 'Hindi', value: 'Hindi' },
    { label: 'German', value: 'German' }
  ];

  // States
  chatStarted = false;
  chatEnded = false;
  loadingCompanies = false;
  startingChat = false;
  sendingMessage = false;
  isTyping = false;

  // Chat Data
  messages: Array<{ sender: string; text: string; time: string; sources?: any[] }> = [];
  currentMessage = '';
  leadScore = 0;
  leadStatus = 'New';
  greetingMessage = '';

  private pollInterval: any = null;

  ngOnInit(): void {
    this.loadCompanies();
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  loadCompanies(): void {
    this.loadingCompanies = true;
    this.chatService.getPublicCompanies().subscribe({
      next: (data) => {
        this.companies = data;
        if (data.length > 0) {
          this.selectedCompany = data[0];
        }
        this.loadingCompanies = false;
      },
      error: () => {
        this.loadingCompanies = false;
        // Fallback mock companies for local demo widgets
        this.companies = [
          {
            id: 'company-1',
            name: 'TechCorp Solutions',
            description: 'Sales automation & CRM workflows provider',
            widget_greeting: 'Hello! I can help you register your business and get in touch with a specialist.'
          },
          {
            id: 'company-2',
            name: 'GlobalFin Partners',
            description: 'Financial advisory and secure investment accounts',
            widget_greeting: 'Welcome to GlobalFin. Let\'s get your account setup details.'
          }
        ];
        this.selectedCompany = this.companies[0];
      }
    });
  }

  get isFormValid(): boolean {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return (
      !!this.selectedCompany &&
      this.displayName.trim().length >= 2 &&
      this.email.trim().length > 0 &&
      emailRegex.test(this.email) &&
      this.phone.trim().length >= 8
    );
  }

  startConversation(): void {
    if (!this.isFormValid || !this.selectedCompany) return;

    this.startingChat = true;
    const payload: ChatStartPayload = {
      company: this.selectedCompany.id,
      display_name: this.displayName.trim(),
      email: this.email.trim(),
      phone: this.phone.trim(),
      whatsapp: this.whatsapp.trim() || null,
      instagram: this.instagram.trim() || null,
      channel: 'web',
      language: this.selectedLanguage
    };

    this.chatService.startChat(payload).subscribe({
      next: (session) => {
        this.chatStarted = true;
        this.startingChat = false;
        this.greetingMessage = session.greeting || this.selectedCompany!.widget_greeting;
        
        // Push initial greeting from assistant
        this.messages.push({
          sender: 'ai',
          text: this.greetingMessage,
          time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        });

        this.startPolling();
      },
      error: (err) => {
        this.startingChat = false;
        // Mock fallback to allow demo interface to function locally
        this.chatStarted = true;
        this.greetingMessage = this.selectedCompany!.widget_greeting;
        this.messages.push({
          sender: 'ai',
          text: this.greetingMessage,
          time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        });
        this.startPolling();
      }
    });
  }

  sendMessage(): void {
    if (!this.currentMessage.trim()) return;
    const userText = this.currentMessage.trim();
    this.currentMessage = '';

    // Add user message locally immediately
    this.messages.push({
      sender: 'customer',
      text: userText,
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    });

    this.isTyping = true;
    this.chatService.sendChatMessage(userText).subscribe({
      next: (res) => {
        this.isTyping = false;
        if (res.lead_score !== undefined) {
          this.leadScore = res.lead_score;
        }
        if (res.lead_status) {
          this.leadStatus = res.lead_status;
        }

        // Only add AI reply if it's not already in the message list via polling
        const isAlreadyAdded = this.messages.some(m => m.sender === 'ai' && m.text === res.reply);
        if (!isAlreadyAdded) {
          this.messages.push({
            sender: 'ai',
            text: res.reply || 'Thank you, I have recorded your message.',
            time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            sources: res.sources
          });
        }
      },
      error: () => {
        this.isTyping = false;
        // Mock reply generation for local simulation
        setTimeout(() => {
          this.leadScore = Math.min(100, this.leadScore + 15);
          this.leadStatus = this.leadScore > 75 ? 'Interested' : this.leadScore > 40 ? 'Follow-up' : 'Assigned';
          this.messages.push({
            sender: 'ai',
            text: `Thanks ${this.displayName}, I have updated your lead score and status in TechCorp database. What product features are you most interested in?`,
            time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
          });
        }, 1200);
      }
    });
  }

  endChat(): void {
    this.stopPolling();
    this.chatService.endChat().subscribe({
      next: () => {
        this.chatEnded = true;
      },
      error: () => {
        this.chatEnded = true; // Fallback
      }
    });
  }

  resetChat(): void {
    this.chatStarted = false;
    this.chatEnded = false;
    this.messages = [];
    this.displayName = '';
    this.email = '';
    this.phone = '';
    this.whatsapp = '';
    this.instagram = '';
    this.leadScore = 0;
    this.leadStatus = 'New';
  }

  private startPolling(): void {
    this.stopPolling();
    this.pollInterval = setInterval(() => {
      this.chatService.pollChatMessages().subscribe({
        next: (msgs) => {
          const mapped = msgs.map((m) => ({
            sender: m.sender || 'system',
            text: m.content || m.message,
            time: m.created_at ? new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''
          }));
          
          if (mapped.length > this.messages.length) {
            this.messages = mapped;
          }
        }
      });
    }, 4000);
  }

  private stopPolling(): void {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }

  getInitials(name: string): string {
    if (!name) return 'US';
    return name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
  }

  getStatusSeverity(status: string): "success" | "secondary" | "info" | "warn" | "danger" | "contrast" | undefined {
    const map: Record<string, "success" | "secondary" | "info" | "warn" | "danger" | "contrast"> = {
      'New': 'info',
      'Assigned': 'secondary',
      'Follow-up': 'warn',
      'Interested': 'success',
      'Negotiation': 'contrast',
      'Won': 'success',
      'Lost': 'danger',
      'Closed': 'secondary',
    };
    return map[status] || 'info';
  }
}
