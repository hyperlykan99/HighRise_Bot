"""Radio V2 message rendering."""
from __future__ import annotations

from modules.radio_renderer import render_now_playing


def queue_confirmation(
    *,
    title: str = "",
    artist: str = "",
    position: int = 0,
    priority: int = 0,
    staff_free: bool = False,
    plays_left: "int | None" = None,
) -> str:
    lines = ["⭐ Priority added" if priority else "✅ Added to queue"]
    if title:
        lines.append(f"Title: {title[:50]}")
    if artist:
        lines.append(f"Artist: {artist[:28]}")
    lines.append(f"Position: #{position or '?'}")
    if staff_free:
        lines.append("🛠️ Staff: Free")
    elif priority:
        lines.append("⭐ Priority")
    elif plays_left is not None:
        lines.append(f"💿 Plays left: {int(plays_left)}")
    else:
        lines.append("Cost: Free")
    lines.append("Please wait…")
    return "\n".join(lines)[:249]


def queue_pages(rows: list[dict]) -> list[str]:
    if not rows:
        return ["🎶 UP NEXT\nempty\n!play to request a song"]
    lines = ["🎧 Queue"]
    for idx, row in enumerate(rows, 1):
        status = (row.get("status") or "").lower()
        icon = "✅" if status in ("uploaded", "submitted") else "⏳"
        uname = (row.get("username") or "?").strip()[:12]
        title = (row.get("title") or "Preparing…").strip()
        source = (row.get("source_type") or "").strip()
        source_icon = "📀 " if source == "local_favorite" else ""
        lines.append(f"{idx}. {icon} @{uname} — {source_icon}{title[:34]}")
    lines.append("!play to request a song")

    pages: list[str] = []
    page = ""
    for line in lines:
        candidate = f"{page}\n{line}" if page else line
        if len(candidate) > 249 and page:
            pages.append(page)
            page = line
        else:
            page = candidate
    if page:
        pages.append(page[:249])
    return pages


def request_history(rows: list[dict], title: str = "📜 My Requests") -> list[str]:
    if not rows:
        return [f"{title}\nNo request history yet."]
    lines = [title]
    icons = {
        "pending": "⏳",
        "preparing": "⏳",
        "uploaded": "✅",
        "submitted": "✅",
        "playing": "▶️",
        "played": "🎵",
        "cancelled": "🚫",
        "failed": "❌",
        "cleaned": "🧹",
    }
    for row in rows:
        status = (row.get("status") or "").lower()
        lines.append(f"{icons.get(status, '•')} {status}: {(row.get('title') or 'Unknown')[:34]}")
    return ["\n".join(lines)[:249]]

