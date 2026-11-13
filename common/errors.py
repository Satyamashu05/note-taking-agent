class RetryableError(Exception):
    """Temporary problem (429, network, 5xx): try again later."""
    def __init__(self, code, message, retry_after=None):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after

class FatalError(Exception):
    """Permanent problem: retrying will not help."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
