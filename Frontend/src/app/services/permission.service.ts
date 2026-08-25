import { Injectable, inject } from '@angular/core';
import { environment } from '../../environments/environment';
import { AuthService } from './auth.service';
import { UserMe } from '../models/auth.models';
import { tap } from 'rxjs/operators';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';

@Injectable({
  providedIn: 'root',
})
export class PermissionService {
  private api = inject(ApiService);
  private authService = inject(AuthService);

  public fetchPermissions(): Observable<UserMe> {
    const url = `${environment.apiPrefix}/access/me`;
    return this.api.get<UserMe>(url).pipe(
      tap((user) => {
        this.authService.setCurrentUser(user);
      }),
    );
  }
}
