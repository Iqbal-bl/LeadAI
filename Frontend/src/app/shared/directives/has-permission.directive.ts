import {
  Directive,
  Input,
  TemplateRef,
  ViewContainerRef,
  inject,
} from '@angular/core';
import { AuthService } from '../../services/auth.service';

@Directive({
  selector: '[appHasPermission]',
  standalone: false,
})
export class HasPermissionDirective {
  private templateRef = inject(TemplateRef<any>);
  private viewContainer = inject(ViewContainerRef);
  private authService = inject(AuthService);

  private hasView = false;

  @Input('appHasPermission') set appHasPermission(
    permission: string | string[],
  ) {
    console.log(permission);

    const user = this.authService.getCurrentUser();
    const userPermissions = user?.permissions || [];
    const isPlatformAdmin = this.authService.isPlatformAdmin();

    let hasAccess = isPlatformAdmin;
    if (!hasAccess) {
      if (Array.isArray(permission)) {
        hasAccess = permission.some((p) => userPermissions.includes(p));
      } else {
        hasAccess = userPermissions.includes(permission);
      }
    }

    if (hasAccess && !this.hasView) {
      this.viewContainer.createEmbeddedView(this.templateRef);
      this.hasView = true;
    } else if (!hasAccess && this.hasView) {
      this.viewContainer.clear();
      this.hasView = false;
    }
  }
}
