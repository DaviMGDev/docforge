"""Custom exceptions for pdf2md."""


class Pdf2MdException(Exception):
    """Base exception for all pdf2md errors."""
    pass


class InvalidPageRangeException(Pdf2MdException):
    """Raised when page range syntax is malformed or out of document bounds."""
    pass


class CorruptedPdfException(Pdf2MdException):
    """Raised when PDF file cannot be opened or repaired."""
    pass


class MissingApiKeyException(Pdf2MdException):
    """Raised when required API key for configured model is missing."""
    pass


class DiskWriteException(Pdf2MdException):
    """Raised when target file or asset cannot be written."""
    pass


class ChunkOrderException(Pdf2MdException):
    """Raised when chunk order is invalid during stitching."""
    pass


class LlmInferenceException(Pdf2MdException):
    """Raised when LLM calls fail repeatedly across retries."""
    pass
