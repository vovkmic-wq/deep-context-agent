"""Domain exceptions for the agent application."""


class AgentError(Exception):
    """Base class for expected application errors."""


class ConfigurationError(AgentError):
    """Raised when required application configuration is invalid."""


class PathSecurityError(AgentError):
    """Raised when a requested path escapes an allowed root."""


class ContextStoreError(AgentError):
    """Raised when context storage or indexing fails."""


class DiagnosticStoreError(AgentError):
    """Raised when the durable diagnostic journal cannot be updated safely."""


class WebSearchError(AgentError):
    """Raised when an internet search cannot be completed."""
