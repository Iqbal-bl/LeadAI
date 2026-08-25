import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class CommonLibService {
  public isFeatureUpdated = new BehaviorSubject<boolean>(true);

  constructor() {}
  async generateCodeChallenge() {
    const codeVerifier = this.generateRandomString(64);
    sessionStorage.setItem('codeVerifier', codeVerifier);

    const codeChallenge = await this.pkceChallengeFromVerifier(codeVerifier);
    sessionStorage.setItem('codeChallenge', codeChallenge);

    return codeChallenge;
  }

  generateRandomString(length: number): string {
    const charset =
      'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~';
    let result = '';
    const values = new Uint8Array(length);
    window.crypto.getRandomValues(values);
    for (let i = 0; i < values.length; i++) {
      result += charset.charAt(values[i] % charset.length);
    }
    return result;
  }

  async pkceChallengeFromVerifier(verifier: string): Promise<string> {
    const encoder = new TextEncoder();
    const data = encoder.encode(verifier);
    const digest = await window.crypto.subtle.digest('SHA-256', data);

    return this.base64UrlEncode(new Uint8Array(digest));
  }

  base64UrlEncode(buffer: Uint8Array): string {
    let binary = '';
    const len = buffer.byteLength;
    for (let i = 0; i < len; i++) {
      binary += String.fromCharCode(buffer[i]);
    }
    return btoa(binary)
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '');
  }
}
