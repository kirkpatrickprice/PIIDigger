from piidigger.datahandlers import email, pan
from piidigger.protocols import DataHandler

# Explicit handler registry — not dynamic discovery.
# Add new handlers here as they are implemented.
HANDLER_REGISTRY: dict[str, DataHandler] = {
    pan.handler.name: pan.handler,
    email.handler.name: email.handler,
}
