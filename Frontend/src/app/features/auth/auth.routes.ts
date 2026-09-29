import { Route } from '@angular/router';

export const AUTH_ROUTES: Route[] = [
  {
    path: 'callback',
    loadComponent: () =>
      import('./callback/callback.component').then((m) => m.CallbackComponent),
  },
  {
    path: 'instagram/callback',
    loadComponent: () =>
      import('./instagram/instagram-callback.component').then(
        (m) => m.InstagramCallbackComponent,
      ),
  },
  {
    path: 'facebook/callback',
    loadComponent: () =>
      import('./facebook/facebook-callback.component').then(
        (m) => m.FacebookCallbackComponent,
      ),
  },
];
