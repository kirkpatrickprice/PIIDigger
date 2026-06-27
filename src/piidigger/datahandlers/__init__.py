from piidigger.datahandlers import email, pan

# Explicit handler registry — not dynamic discovery.
# Add new handlers here as they are implemented.
HANDLER_REGISTRY = {
    pan.handler.name: pan.handler,
    email.handler.name: email.handler,
}
