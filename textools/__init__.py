"""Tools for working with LaTeX files."""
from .clean import Cleaned, clean
from .comments import Options, Result, remove_comments
from .macros import MacroOptions, MacroResult, remove_macros

__version__ = "0.1.0"

__all__ = ["Cleaned", "MacroOptions", "MacroResult", "Options", "Result",
           "clean", "remove_comments", "remove_macros"]
