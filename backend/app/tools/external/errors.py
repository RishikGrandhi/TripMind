class ExternalToolError(RuntimeError):
    """Stable, secret-free failure raised by live provider adapters."""

    def __init__(self, provider: str, code: str, message: str) -> None:
        self.provider = provider
        self.code = code
        self.message = message
        super().__init__(f"{provider}:{code}: {message}")
