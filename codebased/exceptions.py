from __future__ import annotations

class CodebasedException(Exception):
    """
    Differentiate between business logic exceptions and Knightian exceptions.
    """
    pass


class MissingConfigFileException(CodebasedException):
    """
    Raised when the config directory is not found.
    """
    pass

class NotFoundException(CodebasedException, LookupError):
    """
    Raised when something is not found.
    """

    def __init__(self, identifier: object):
        self.identifier = identifier
        super().__init__(identifier)
