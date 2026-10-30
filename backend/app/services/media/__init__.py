"""Media engine — FileSplitter's scan / encode / split / trim pipeline, ported.

startup() is called from the FastAPI lifespan; nothing here runs at import time.
"""
import logging

logger = logging.getLogger(__name__)

_started = False


def startup() -> None:
    global _started
    if _started:
        return
    from . import config, encoder, store, worker

    config.load_from_db()
    store.recover_interrupted()
    encoder.start_probe()
    worker.start()
    _started = True
    logger.info("Media engine started (%d worker(s), backend=%s)",
                config.MAX_WORKERS, config.ENCODER_BACKEND)


def shutdown() -> None:
    """Stop running ffmpeg jobs so they clean up their temp files before exit."""
    from . import worker

    for event in list(worker._cancel_events.values()):
        event.set()
