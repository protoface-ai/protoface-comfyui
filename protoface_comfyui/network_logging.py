"""Suppress library URL logging only inside this adapter's HTTP request context."""

from contextvars import ContextVar
from logging import Filter, getLogger

ACTIVE = ContextVar("protoface_network_request", default=False)


class AdapterNetworkFilter(Filter):
    def filter(self, record):
        # Signed result URLs can contain credentials. Adapter errors expose safe API codes instead.
        return not ACTIVE.get()


for name in ("httpx", "httpcore"):
    logger = getLogger(name)
    if not any(isinstance(item, AdapterNetworkFilter) for item in logger.filters):
        logger.addFilter(AdapterNetworkFilter())
# httpcore emits through child loggers; logger filters do not propagate to descendants.
for name in ("httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy"):
    logger = getLogger(name)
    if not any(isinstance(item, AdapterNetworkFilter) for item in logger.filters):
        logger.addFilter(AdapterNetworkFilter())
