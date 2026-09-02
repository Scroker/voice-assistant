"""D-Bus registration and event loop helpers for the daemon."""

import logging
import sys

from dasbus.connection import SessionMessageBus
from dasbus.loop import EventLoop

logger = logging.getLogger("VoiceAssistant.DBusBootstrap")


def register_dbus_service(assistant) -> SessionMessageBus:
    """Publish the assistant object on the session bus and register the service."""
    bus = SessionMessageBus()
    bus.publish_object("/org/local/VoiceAssistant", assistant)
    bus.register_service("org.local.VoiceAssistant")
    logger.info("Servizio D-Bus registrato su org.local.VoiceAssistant")
    return bus


def run_event_loop() -> None:
    """Run the daemon event loop and handle shutdown interrupts."""
    loop = EventLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        logger.info("Uscita.")
        sys.exit(0)
