import logging

logger = logging.getLogger(__name__)


def notify(apprise_url: str, title: str, body: str, url: str = "/activity") -> None:
    """Send a notification everywhere the user asked for one: Apprise (when a URL is
    configured) and every device subscribed to push notifications."""
    try:
        from .webpush import send_all_async

        send_all_async(title, body, url)
    except Exception as e:
        logger.error("Push notification failed: %s", e)
    if not apprise_url:
        return
    try:
        import apprise  # type: ignore

        ap = apprise.Apprise()
        ap.add(apprise_url)
        ap.notify(title=title, body=body)
    except ImportError:
        logger.warning("apprise package not installed — notifications disabled.")
    except Exception as e:
        logger.error("Notification failed: %s", e)
