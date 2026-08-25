import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';

const routes: Routes = [
  {
    path: '',
    redirectTo: 'approvals',
    pathMatch: 'full',
  },
  {
    path: 'create',
    loadComponent: () =>
      import('./pages/blog-composer/blog-composer.component').then(
        (m) => m.BlogComposerComponent
      ),
  },
  {
    path: 'edit/:id',
    loadComponent: () =>
      import('./pages/blog-composer/blog-composer.component').then(
        (m) => m.BlogComposerComponent
      ),
  },
  {
    path: 'approvals',
    loadComponent: () =>
      import('./pages/blog-approvals/blog-approvals.component').then(
        (m) => m.BlogApprovalsComponent
      ),
  },
  {
    path: 'review/:id',
    loadComponent: () =>
      import('./pages/article-reviewer/article-reviewer.component').then(
        (m) => m.ArticleReviewerComponent
      ),
  },
];

@NgModule({
  imports: [RouterModule.forChild(routes)],
  exports: [RouterModule],
})
export class BlogModule {}
