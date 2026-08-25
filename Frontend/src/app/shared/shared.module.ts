import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule, ReactiveFormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';

// PrimeNG Modules
import { ButtonModule } from 'primeng/button';
import { InputTextModule } from 'primeng/inputtext';
import { TableModule } from 'primeng/table';
// import { CardModule } from 'primeng/card';
// import { ToolbarModule } from 'primeng/toolbar';
import { MenuModule } from 'primeng/menu';
// import { MenubarModule } from 'primeng/menubar';
// import { PanelMenuModule } from 'primeng/panelmenu';
// import { BreadcrumbModule } from 'primeng/breadcrumb';
import { TabsModule } from 'primeng/tabs';
import { AccordionModule } from 'primeng/accordion';
// import { PanelModule } from 'primeng/panel';
// import { SplitterModule } from 'primeng/splitter';
import { DialogModule } from 'primeng/dialog';
import { ToastModule } from 'primeng/toast';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { SkeletonModule } from 'primeng/skeleton';
// import { TreeTableModule } from 'primeng/treetable';
// import { TreeModule } from 'primeng/tree';
import { TimelineModule } from 'primeng/timeline';
// import { OrganizationChartModule } from 'primeng/organizationchart';
import { FileUploadModule } from 'primeng/fileupload';
import { AvatarModule } from 'primeng/avatar';
// import { AvatarGroupModule } from 'primeng/avatargroup';
// import { BadgeModule } from 'primeng/badge';
import { TagModule } from 'primeng/tag';
// import { ChipModule } from 'primeng/chip';
// import { PopoverModule } from 'primeng/popover';
import { StepperModule } from 'primeng/stepper';
// import { AutoCompleteModule } from 'primeng/autocomplete';
import { CalendarModule } from 'primeng/calendar';
import { DropdownModule } from 'primeng/dropdown';
// import { MultiSelectModule } from 'primeng/multiselect';
// import { SelectButtonModule } from 'primeng/selectbutton';
// import { ToggleButtonModule } from 'primeng/togglebutton';
import { SliderModule } from 'primeng/slider';
// import { RatingModule } from 'primeng/rating';
// import { TextareaModule } from 'primeng/textarea';
import { PasswordModule } from 'primeng/password';
// import { InputMaskModule } from 'primeng/inputmask';
import { CheckboxModule } from 'primeng/checkbox';
// import { RadioButtonModule } from 'primeng/radiobutton';
import { ProgressBarModule } from 'primeng/progressbar';
import { KnobModule } from 'primeng/knob';
import { ChartModule } from 'primeng/chart';
// import { SpeedDialModule } from 'primeng/speeddial';
// import { FloatLabelModule } from 'primeng/floatlabel';
// import { ScrollPanelModule } from 'primeng/scrollpanel';
import { ScrollTopModule } from 'primeng/scrolltop';
// import { SplitButtonModule } from 'primeng/splitbutton';
// import { DockModule } from 'primeng/dock';
// import { CarouselModule } from 'primeng/carousel';
// import { GalleriaModule } from 'primeng/galleria';
// import { ScrollerModule } from 'primeng/scroller';
// import { PaginatorModule } from 'primeng/paginator';
// import { OrderListModule } from 'primeng/orderlist';
// import { PickListModule } from 'primeng/picklist';
import { TooltipModule } from 'primeng/tooltip';
// import { ContextMenuModule } from 'primeng/contextmenu';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { DividerModule } from 'primeng/divider';
// import { OverlayBadgeModule } from 'primeng/overlaybadge';
import { InputGroupModule } from 'primeng/inputgroup';
import { InputGroupAddonModule } from 'primeng/inputgroupaddon';
import { IconFieldModule } from 'primeng/iconfield';
import { InputIconModule } from 'primeng/inputicon';
// import { MessageModule } from 'primeng/message';
// import { RippleModule } from 'primeng/ripple';
import { InputNumberModule } from 'primeng/inputnumber';
import { InputSwitchModule } from 'primeng/inputswitch';
// import { MeterGroupModule } from 'primeng/metergroup';
// import { ImageModule } from 'primeng/image';
import { SidebarModule } from 'primeng/sidebar';

const PRIMENG_MODULES = [
  ButtonModule,
  InputTextModule,
  TableModule,
  // CardModule,
  // ToolbarModule,
  MenuModule,
  // MenubarModule,
  // PanelMenuModule,
  // BreadcrumbModule,
  TabsModule,
  AccordionModule,
  // PanelModule,
  // SplitterModule,
  SidebarModule,
  DialogModule,
  ToastModule,
  ProgressSpinnerModule,
  SkeletonModule,
  // TreeTableModule,
  // TreeModule,
  TimelineModule,
  // OrganizationChartModule,
  FileUploadModule,
  AvatarModule,
  // AvatarGroupModule,
  // BadgeModule,
  TagModule,
  // ChipModule,
  // PopoverModule,
  StepperModule,
  // AutoCompleteModule,
  CalendarModule,
  DropdownModule,
  // MultiSelectModule,
  // SelectButtonModule,
  // ToggleButtonModule,
  SliderModule,
  // RatingModule,
  // TextareaModule,
  PasswordModule,
  // InputMaskModule,
  CheckboxModule,
  // RadioButtonModule,
  ProgressBarModule,
  KnobModule,
  ChartModule,
  // SpeedDialModule,
  // FloatLabelModule,
  // ScrollPanelModule,
  ScrollTopModule,
  // SplitButtonModule,
  // DockModule,
  // CarouselModule,
  // GalleriaModule,
  // ScrollerModule,
  // PaginatorModule,
  // OrderListModule,
  // PickListModule,
  TooltipModule,
  // ContextMenuModule,
  ConfirmDialogModule,
  DividerModule,
  // OverlayBadgeModule,
  InputGroupModule,
  InputGroupAddonModule,
  IconFieldModule,
  InputIconModule,
  // MessageModule,
  // RippleModule,
  InputNumberModule,
  InputSwitchModule,
  // MeterGroupModule,
  // ImageModule,
];

import { EmptyStateComponent } from './components/empty-state/empty-state.component';
import { ErrorStateComponent } from './components/error-state/error-state.component';
import { LoadingSkeletonComponent } from './components/loading-skeleton/loading-skeleton.component';
import { ConfirmationDialogComponent } from './components/confirmation-dialog/confirmation-dialog.component';
import { SearchBarComponent } from './components/search-bar/search-bar.component';
import { HasPermissionDirective } from './directives/has-permission.directive';

const SHARED_COMPONENTS = [
  EmptyStateComponent,
  ErrorStateComponent,
  LoadingSkeletonComponent,
  ConfirmationDialogComponent,
  SearchBarComponent,
];

@NgModule({
  declarations: [
    HasPermissionDirective,
    ...SHARED_COMPONENTS,
  ],
  imports: [
    CommonModule,
    FormsModule,
    ReactiveFormsModule,
    RouterModule,
    ...PRIMENG_MODULES,
  ],
  exports: [
    CommonModule,
    FormsModule,
    ReactiveFormsModule,
    RouterModule,
    ...PRIMENG_MODULES,
    ...SHARED_COMPONENTS,
    HasPermissionDirective,
  ],
})
export class SharedModule {}
