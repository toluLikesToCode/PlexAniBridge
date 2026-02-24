"""Jellyfin client placeholder module."""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from src import log

__all__ = ["JellyfinClient", "JellyfinSection"]


@dataclass(slots=True)
class JellyfinSection:
    """Minimal Jellyfin section model used by scheduling/sync scaffolding."""

    title: str
    type: str
    id: str


class JellyfinClient:
    """Lightweight Jellyfin client scaffold.

    This class intentionally exposes the subset of methods used by the bridge so the
    application can route profiles by provider and expose status/webhook flows without
    breaking Plex paths.
    """

    def __init__(
        self,
        jellyfin_token: str,
        jellyfin_user: str,
        jellyfin_url: str,
        jellyfin_sections: list[str],
        jellyfin_genres: list[str],
    ) -> None:
        self.jellyfin_token = jellyfin_token
        self.jellyfin_user = jellyfin_user
        self.jellyfin_url = jellyfin_url.rstrip("/")
        self.jellyfin_sections = jellyfin_sections
        self.jellyfin_genres = jellyfin_genres

        self.user_account_id: str = jellyfin_user

    async def close(self) -> None:
        """Close async resources."""
        return None

    def clear_cache(self) -> None:
        """Clear local caches (placeholder)."""
        return None

    def get_sections(self) -> list[JellyfinSection]:
        """Return sections to sync.

        Full Jellyfin section discovery is not yet implemented; when no explicit
        sections are configured, return an empty list.
        """
        return [
            JellyfinSection(title=title, type="show", id=title)
            for title in self.jellyfin_sections
        ]

    def get_section_items(self, *args, **kwargs):
        """Yield items for a section.

        Full Jellyfin item adaptation to the Plex-based sync engine is pending.
        """
        if False:
            yield None
        return

    async def fetch_metadata_batch(
        self, item_ids: tuple[str, ...]
    ) -> dict[str, dict | None]:
        """Fetch Jellyfin metadata for timeline enrichment."""
        if not item_ids:
            return {}

        headers = {
            "X-Emby-Token": self.jellyfin_token,
            "Accept": "application/json",
        }
        result: dict[str, dict | None] = {item_id: None for item_id in item_ids}

        async with aiohttp.ClientSession(headers=headers) as session:
            for item_id in item_ids:
                url = f"{self.jellyfin_url}/Items/{item_id}"
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=15)
                    ) as resp:
                        if resp.status != 200:
                            continue
                        payload = await resp.json()
                        result[item_id] = {
                            "guid": payload.get("Id", item_id),
                            "title": payload.get("Name") or "Unknown Title",
                            "type": payload.get("Type", "unknown").lower(),
                            "thumb": payload.get("ImageTags", {}).get("Primary"),
                            "art": payload.get("BackdropImageTags", [None])[0],
                        }
                except Exception:
                    log.debug(
                        "Failed to fetch Jellyfin metadata for item '%s'", item_id
                    )
                    continue
        return result
