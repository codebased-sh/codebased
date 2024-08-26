from __future__ import annotations

from pathlib import Path


class CodebasedException(Exception):
    """
    Differentiate between business logic exceptions and Knightian exceptions.
    """
    pass


class NoApplicationDirectoryException(CodebasedException):
    """
    Raised when the application directory is not found.
    """

    def __init__(self, application_directory: Path):
        self.application_directory = application_directory
        super().__init__(f"The application directory {str(application_directory)} was not found.")


class NotFoundException(CodebasedException, LookupError):
    """
    Raised when something is not found.
    """

    def __init__(self, identifier: object):
        self.identifier = identifier
        super().__init__(identifier)


class AlreadyExistsException(CodebasedException):
    def __init__(self, identifier: int):
        self.identifier = identifier
        super().__init__(identifier)
