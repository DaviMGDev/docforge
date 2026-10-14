"""Custom exceptions for docforge."""


class DocForgeException(Exception):
    """Base exception for all docforge errors."""
    pass


# Backwards compatibility alias
Pdf2MdException = DocForgeException


class InvalidPageRangeException(DocForgeException):
    """Raised when page range syntax is malformed or out of document bounds."""
    pass


class CorruptedPdfException(DocForgeException):
    """Raised when PDF file cannot be opened or repaired."""
    pass


class MissingApiKeyException(DocForgeException):
    """Raised when required API key for configured model is missing."""
    pass


class DiskWriteException(DocForgeException):
    """Raised when target file or asset cannot be written."""
    pass


class ChunkOrderException(DocForgeException):
    """Raised when chunk order is invalid during stitching."""
    pass


class LlmInferenceException(DocForgeException):
    """Raised when LLM calls fail repeatedly across retries."""
    pass


class LlmTruncationException(LlmInferenceException):
    """Raised when LLM response is truncated due to token limit or finish_reason == 'length'."""
    pass
