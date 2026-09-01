from . import razorpay

ADAPTERS = (razorpay,)


def detect(columns):
    for adapter in ADAPTERS:
        if adapter.matches(columns):
            return adapter
    return None
