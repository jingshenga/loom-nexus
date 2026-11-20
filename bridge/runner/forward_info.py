"""Telegram forward-lineage extraction.

Telethon exposes the upstream origin of a forwarded message on
msg.fwd_from (MessageFwdHeader). Extracting it at intake time is what
makes "mother channel" discovery possible downstream — without it the
original author/channel of a re-posted listing card is lost forever.

Pure function; tolerant of every shape Telethon has shipped (bare
user forwards, channel reposts with hidden authors, service messages).
"""
import logging

logger = logging.getLogger("nexus-bridge.forward-info")


def extract_forward_info(msg) -> dict | None:
    """Return the structured forward lineage of a message, or None when
    the message is not a forward. Never raises."""
    fwd = getattr(msg, "fwd_from", None)
    if not fwd:
        return None
    from_id = None
    from_type = None
    from_peer = getattr(fwd, "from_id", None)
    if from_peer:
        if hasattr(from_peer, "channel_id"):
            from_id = from_peer.channel_id
            from_type = "channel"
        elif hasattr(from_peer, "chat_id"):
            from_id = from_peer.chat_id
            from_type = "chat"
        elif hasattr(from_peer, "user_id"):
            from_id = from_peer.user_id
            from_type = "user"
    fwd_date = None
    if getattr(fwd, "date", None):
        try:
            fwd_date = fwd.date.isoformat()
        except Exception:
            fwd_date = str(fwd.date)
    try:
        info = {
            "from_id": from_id,
            "from_type": from_type,
            "from_name": getattr(fwd, "from_name", None),
            "post_author": getattr(fwd, "post_author", None),
            "channel_post": getattr(fwd, "channel_post", None),
            "date": fwd_date,
        }
    except Exception as exc:
        logger.warning("forward info extraction failed: %s", exc)
        return None
    if not any((info.get("from_id"), info.get("from_name"), info.get("post_author"))):
        return None
    return info
