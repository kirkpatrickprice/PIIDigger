from piidigger.outputhandlers.csv import CsvSink
from piidigger.outputhandlers.json import JsonSink
from piidigger.outputhandlers.text import TextSink

# HANDLER_REGISTRY: result-format string → OutputSink class.
# Single source of truth for known formats — Config.results.formats validation
# and run.py's sink construction both read this instead of a hardcoded list.
HANDLER_REGISTRY: dict[str, type] = {
    "csv": CsvSink,
    "json": JsonSink,
    "text": TextSink,
}

__all__ = ["CsvSink", "JsonSink", "TextSink", "HANDLER_REGISTRY"]
