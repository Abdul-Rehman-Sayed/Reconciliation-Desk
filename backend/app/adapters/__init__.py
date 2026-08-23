"""Column-mapping adapters for real-world settlement exports."""

from . import razorpay

ADAPTERS = (razorpay,)


def detect(columns):
    """Return the first adapter that recognises this column set, or None."""
    for adapter in ADAPTERS:
        if adapter.matches(columns):
            return adapter
    return None
