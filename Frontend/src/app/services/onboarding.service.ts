import { Injectable, inject } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { AuthService } from './auth.service';
import { ApiService } from './api.service';

export type OnboardingStep = 'channels' | 'knowledge-base';

export interface OnboardingState {
  companyId: string;
  startedAt: string;
  completedAt?: string;
  completed: boolean;
  currentStep: OnboardingStep;
  channelConnected?: boolean;
  channelSkipped?: boolean;
  kbAdded?: boolean;
  kbSkipped?: boolean;
}

export interface OnboardingBackendState {
  eligible: boolean;
  has_active_subscription: boolean;
  status: string;
  current_step: string;
  is_completed: boolean;
  channel_connected: boolean;
  channel_skipped: boolean;
  kb_added: boolean;
  kb_skipped: boolean;
  completed_at?: string | null;
  connected_channels: string[];
  active_plan_channels: string[];
  kb_document_count: number;
  client_id: string;
}

@Injectable({
  providedIn: 'root',
})
export class OnboardingService {
  private authService = inject(AuthService);
  private apiService = inject(ApiService);
  private readonly PREFIX = 'leadai_onboarding_';

  /**
   * Resolves localStorage key unique to the current company context.
   */
  private getStorageKey(companyId?: string | null): string {
    const cid =
      companyId ||
      this.authService.getSelectedCompanyId() ||
      this.authService.getCurrentUser()?.client_id ||
      'default';
    return `${this.PREFIX}${cid}`;
  }

  /**
   * Initializes a fresh onboarding state for the company when subscription payment succeeds.
   */
  public initOnboarding(companyId?: string | null): OnboardingState {
    const key = this.getStorageKey(companyId);
    const resolvedId = key.replace(this.PREFIX, '');
    const state: OnboardingState = {
      companyId: resolvedId,
      startedAt: new Date().toISOString(),
      completed: false,
      currentStep: 'channels',
      channelConnected: false,
      channelSkipped: false,
      kbAdded: false,
      kbSkipped: false,
    };
    try {
      localStorage.setItem(key, JSON.stringify(state));
    } catch (e) {
      console.warn('Failed to save onboarding state in localStorage', e);
    }
    return state;
  }

  /**
   * Retrieves the current onboarding state for the company, if any.
   */
  public getState(companyId?: string | null): OnboardingState | null {
    const key = this.getStorageKey(companyId);
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return null;
      return JSON.parse(raw) as OnboardingState;
    } catch {
      return null;
    }
  }

  /**
   * Helper to ensure state exists.
   */
  public initOrGetState(companyId?: string | null): OnboardingState {
    return this.getState(companyId) || this.initOnboarding(companyId);
  }

  /**
   * Persists updated state to localStorage.
   */
  public saveState(state: OnboardingState): void {
    const key = this.getStorageKey(state.companyId);
    try {
      localStorage.setItem(key, JSON.stringify(state));
    } catch (e) {
      console.warn('Failed to save onboarding state in localStorage', e);
    }
  }

  /**
   * Returns true if onboarding has been initiated and is not yet completed.
   */
  public isOnboardingInProgress(companyId?: string | null): boolean {
    const state = this.getState(companyId);
    return !!(state && !state.completed);
  }

  /**
   * Returns true if onboarding has already been completed or skipped to completion.
   */
  public isCompleted(companyId?: string | null): boolean {
    const state = this.getState(companyId);
    return !!(state && state.completed);
  }

  /**
   * Sets current step (e.g. 'channels' | 'knowledge-base').
   */
  public setStep(step: OnboardingStep, companyId?: string | null): void {
    const state = this.initOrGetState(companyId);
    state.currentStep = step;
    this.saveState(state);
  }

  /**
   * Records Channel Wizard step completion or skip, transitioning state to 'knowledge-base'.
   */
  public markChannelDone(connected: boolean, skipped: boolean, companyId?: string | null): void {
    const state = this.initOrGetState(companyId);
    state.channelConnected = connected || state.channelConnected;
    state.channelSkipped = skipped;
    state.currentStep = 'knowledge-base';
    this.saveState(state);
  }

  /**
   * Records Knowledge Base step completion or skip, marking onboarding as completed.
   */
  public markKnowledgeBaseDone(added: boolean, skipped: boolean, companyId?: string | null): void {
    const state = this.initOrGetState(companyId);
    state.kbAdded = added || state.kbAdded;
    state.kbSkipped = skipped;
    state.completed = true;
    state.completedAt = new Date().toISOString();
    this.saveState(state);
  }

  /**
   * Explicitly marks onboarding completed.
   */
  public completeOnboarding(companyId?: string | null): void {
    const state = this.initOrGetState(companyId);
    state.completed = true;
    state.completedAt = new Date().toISOString();
    this.saveState(state);
  }

  /**
   * Clears saved onboarding state.
   */
  public resetOnboarding(companyId?: string | null): void {
    const key = this.getStorageKey(companyId);
    try {
      localStorage.removeItem(key);
    } catch (e) {
      console.warn('Failed to remove onboarding state', e);
    }
  }

  // --- Backend API Sync Methods ---

  public fetchBackendState(): Observable<OnboardingBackendState> {
    return this.apiService.get<OnboardingBackendState>('onboarding/state', { companyScoped: true }).pipe(
      tap((res) => {
        const local = this.initOrGetState(res.client_id);
        local.completed = res.is_completed;
        local.currentStep = res.current_step === 'knowledge_base' ? 'knowledge-base' : 'channels';
        local.channelConnected = res.channel_connected;
        local.channelSkipped = res.channel_skipped;
        local.kbAdded = res.kb_added;
        local.kbSkipped = res.kb_skipped;
        this.saveState(local);
      })
    );
  }

  public saveStepToBackend(step: string, action: 'complete' | 'skip'): Observable<OnboardingBackendState> {
    return this.apiService.post<OnboardingBackendState>('onboarding/step', { step, action }, { companyScoped: true }).pipe(
      tap((res) => {
        const local = this.initOrGetState(res.client_id);
        local.completed = res.is_completed;
        local.currentStep = res.current_step === 'knowledge_base' ? 'knowledge-base' : 'channels';
        local.channelConnected = res.channel_connected;
        local.channelSkipped = res.channel_skipped;
        local.kbAdded = res.kb_added;
        local.kbSkipped = res.kb_skipped;
        this.saveState(local);
      })
    );
  }

  public completeBackendOnboarding(): Observable<OnboardingBackendState> {
    return this.apiService.post<OnboardingBackendState>('onboarding/complete', {}, { companyScoped: true }).pipe(
      tap((res) => {
        const local = this.initOrGetState(res.client_id);
        local.completed = true;
        this.saveState(local);
      })
    );
  }
}
