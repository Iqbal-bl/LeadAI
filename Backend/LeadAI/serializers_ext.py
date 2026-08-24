"""
ORM -> DTO mapping for the Phase-2 modules.

The rule inherited from serializers.py holds: PascalCase columns become
snake_case fields in exactly one place. The security rule also holds — a
serializer NEVER emits a decrypted contact detail or a stored credential. Masked
values only; revealing is a separate, permissioned, audited endpoint.
"""
from __future__ import annotations

from .config import settings
from .models import (
    LeadAccount,
    LeadAccountNote,
    LeadCampaign,
    LeadCampaignRecipient,
    LeadChannelAccount,
    LeadContactList,
    LeadContactListItem,
    LeadFile,
)
from .schemas_ext import (
    AccountNoteOut,
    AccountOut,
    CampaignOut,
    ChannelAccountOut,
    ContactListItemOut,
    ContactListOut,
    FileOut,
    RecipientOut,
)


def channel_account_out(row: LeadChannelAccount, public_base: str | None = None) -> ChannelAccountOut:
    base = (public_base or "").rstrip("/")
    return ChannelAccountOut(
        id=row.Id,
        client_id=row.ClientId,
        channel=row.Channel,
        provider=row.Provider,
        name=row.Name,
        external_id=row.ExternalId,
        display_number=row.DisplayNumber,
        business_account_id=row.BusinessAccountId,
        api_version=row.ApiVersion,
        is_active=bool(row.IsActive),
        auto_reply=bool(row.AutoReply),
        script_id=row.ScriptId,
        default_language=row.DefaultLanguage,
        # Presence, never the value.
        has_access_token=bool(row.AccessTokenEnc),
        has_app_secret=bool(row.AppSecretEnc),
        verify_token=row.VerifyToken,
        webhook_url=f"{base}{settings.api_prefix}/public/webhooks/meta" if base else None,
        last_inbound_at=row.LastInboundAt,
        last_outbound_at=row.LastOutboundAt,
        last_error=row.LastError,
        last_error_at=row.LastErrorAt,
        created_at=row.CreatedAt,
    )


def contact_list_out(row: LeadContactList) -> ContactListOut:
    return ContactListOut(
        id=row.Id,
        client_id=row.ClientId,
        name=row.Name,
        description=row.Description,
        source_type=row.SourceType,
        source_file_id=row.SourceFileId,
        total_count=row.TotalCount or 0,
        valid_count=row.ValidCount or 0,
        invalid_count=row.InvalidCount or 0,
        duplicate_count=row.DuplicateCount or 0,
        column_map=row.ColumnMapJson,
        status=row.Status,
        status_message=row.StatusMessage,
        tags=row.Tags,
        created_at=row.CreatedAt,
        created_by=row.CreatedBy,
    )


def contact_list_item_out(row: LeadContactListItem) -> ContactListItemOut:
    return ContactListItemOut(
        id=row.Id,
        row_number=row.RowNumber or 0,
        name=row.Name,
        phone_masked=row.PhoneMasked,
        country_code=row.CountryCode,
        is_valid=bool(row.IsValid),
        invalid_reason=row.InvalidReason,
        fields=row.FieldsJson,
    )


def campaign_out(row: LeadCampaign) -> CampaignOut:
    return CampaignOut(
        id=row.Id,
        client_id=row.ClientId,
        name=row.Name,
        description=row.Description,
        kind=row.Kind,
        channel=row.Channel,
        channel_account_id=row.ChannelAccountId,
        purpose=row.Purpose,
        audience_type=row.AudienceType,
        list_id=row.ListId,
        audience_filter=row.AudienceFilterJson,
        template_name=row.TemplateName,
        template_language=row.TemplateLanguage,
        message_body=row.MessageBody,
        script_id=row.ScriptId,
        language=row.Language,
        status=row.Status,
        status_message=row.StatusMessage,
        scheduled_at=row.ScheduledAt,
        started_at=row.StartedAt,
        completed_at=row.CompletedAt,
        concurrency=row.Concurrency or 5,
        rate_per_minute=row.RatePerMinute or 60,
        respect_opt_out=bool(row.RespectOptOut),
        quiet_hours_start=row.QuietHoursStart,
        quiet_hours_end=row.QuietHoursEnd,
        timezone=row.TimeZone,
        total_count=row.TotalCount or 0,
        queued_count=row.QueuedCount or 0,
        sent_count=row.SentCount or 0,
        delivered_count=row.DeliveredCount or 0,
        read_count=row.ReadCount or 0,
        replied_count=row.RepliedCount or 0,
        failed_count=row.FailedCount or 0,
        skipped_count=row.SkippedCount or 0,
        leads_created=row.LeadsCreated or 0,
        created_at=row.CreatedAt,
        created_by=row.CreatedBy,
    )


def recipient_out(row: LeadCampaignRecipient) -> RecipientOut:
    return RecipientOut(
        id=row.Id,
        name=row.Name,
        phone_masked=row.PhoneMasked,
        status=row.Status,
        attempts=row.Attempts or 0,
        external_message_id=row.ExternalMessageId,
        rendered_body=row.RenderedBody,
        conversation_id=row.ConversationId,
        call_id=row.CallId,
        sent_at=row.SentAt,
        delivered_at=row.DeliveredAt,
        read_at=row.ReadAt,
        replied_at=row.RepliedAt,
        failure_reason=row.FailureReason,
    )


def account_out(row: LeadAccount) -> AccountOut:
    return AccountOut(
        id=row.Id,
        client_id=row.ClientId,
        display_name=row.DisplayName,
        company_name=row.CompanyName,
        phone_masked=row.PhoneMasked,
        email_masked=row.EmailMasked,
        stage=row.Stage,
        status=row.Status,
        owner_email=row.OwnerEmail,
        product=row.Product,
        value=row.Value or 0.0,
        currency=row.Currency or "INR",
        source=row.Source,
        tags=row.Tags,
        notes=row.Notes,
        opt_in_whatsapp=bool(row.OptInWhatsApp),
        opt_in_sms=bool(row.OptInSms),
        opt_in_email=bool(row.OptInEmail),
        opt_in_call=bool(row.OptInCall),
        do_not_disturb=bool(row.DoNotDisturb),
        converted_at=row.ConvertedAt,
        last_contacted_at=row.LastContactedAt,
        next_follow_up_at=row.NextFollowUpAt,
        birthday=row.Birthday,
        anniversary=row.Anniversary,
        source_conversation_id=row.SourceConversationId,
        fields=row.FieldsJson,
        created_at=row.CreatedAt,
    )


def account_note_out(row: LeadAccountNote) -> AccountNoteOut:
    return AccountNoteOut(
        id=row.Id,
        note_type=row.NoteType,
        body=row.Body,
        author_email=row.AuthorEmail,
        meta=row.MetaJson,
        created_at=row.CreatedAt,
    )


def file_out(row: LeadFile, download_url: str | None = None) -> FileOut:
    return FileOut(
        id=row.Id,
        client_id=row.ClientId,
        purpose=row.Purpose,
        file_name=row.FileName,
        content_type=row.ContentType,
        size_bytes=row.SizeBytes or 0,
        bucket=row.Bucket,
        object_key=row.ObjectKey,
        storage_backend=row.StorageBackend,
        download_url=download_url,
        uploaded_by=row.UploadedByEmail,
        linked_entity_type=row.LinkedEntityType,
        linked_entity_id=row.LinkedEntityId,
        created_at=row.CreatedAt,
    )
