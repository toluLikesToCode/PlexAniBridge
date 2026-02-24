"""Jellyfin webhook schema definitions."""

from functools import cached_property

from pydantic import BaseModel, Field


class JellyfinWebhook(BaseModel):
    """Represents a Jellyfin webhook event payload."""

    event: str | None = Field(default=None, alias="NotificationType")
    user_id: str | None = Field(default=None, alias="UserId")
    item_id: str | None = Field(default=None, alias="ItemId")
    item_type: str | None = Field(default=None, alias="ItemType")

    @cached_property
    def account_id(self) -> str | None:
        """Webhook sender account identifier."""
        return self.user_id

    @cached_property
    def top_level_rating_key(self) -> str | None:
        """Top-level media identifier for targeted sync."""
        return self.item_id
