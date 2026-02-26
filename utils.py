import random
import string
import os
from urllib.parse import urlparse


def is_valid_url(url: str) -> bool:
    """Perform a conservative syntactic check that the URL is valid.

    Requirements:
    - scheme must be http or https
    - netloc (host) must be present
    - reasonable length (protect against extremely long values)
    """
    if not url or not isinstance(url, str):
        return False
    if len(url) > 2000:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    return True

def generate_short_id(length=8):
    """Generate a random alphanumeric short ID."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))

def ensure_uploads_dir():
    """Ensure that the uploads directory exists."""
    if not os.path.exists("uploads"):
        os.makedirs("uploads")

# Modified from twitchio utils.py

from typing import Any
import logging
import logging.handlers
import sys
import datetime

def setup_logging(
    *,
    handler: logging.Handler | None = None,
    formatter: logging.Formatter | None = None,
    level: int = logging.INFO,  # Default console level
    loggers_config: dict[str, str | dict] | None = None,
) -> None:
    """
    Set up logging with both console and rotating file handlers.
    
    Console handler: Shows logs at the specified level (default: INFO)
    File handler: Rotates logs when they reach 5MB, keeping 5 backup files, logs everything at DEBUG level
    
    Parameters:
        handler: Optional logging.Handler to use for console output
        formatter: Optional logging.Formatter to use for formatting log messages
        level: Default logging level for console output (default: logging.INFO)
        loggers_config: Dictionary mapping logger names to their configuration.
            Example: {
                # Simple format - just specify filename
                'module1': 'module1.log',
                
                # Advanced format with file and console settings
                'module2': {
                    'filename': 'module2.log',  # Required
                    'level': logging.INFO,      # File log level (default: DEBUG)
                    'console_level': logging.WARNING  # Console log level (default: use root level)
                },
                
                # Multiple loggers can share the same file
                'module3': {
                    'filename': 'shared.log',
                    'console_level': logging.ERROR  # Only show errors for this module in console
                },
                'module4': 'shared.log'  # Will share the same file as module3
            }
    """
    # Create logs directory if it doesn't exist
    os.makedirs('logs', exist_ok=True)
    
    # Process loggers config to handle both string and dict formats
    log_files = {}
    if loggers_config:
        for logger_name, config in loggers_config.items():
            if isinstance(config, str):
                # Simple case: {'logger_name': 'filename.log'}
                log_files[logger_name] = {
                    'filename': config,
                    'level': logging.DEBUG,
                    'console_level': level  # Use root console level
                }
            elif isinstance(config, dict):
                # Advanced case with custom configuration
                log_files[logger_name] = {
                    'filename': config.get('filename', None),
                    'level': config.get('level', logging.DEBUG),
                    'console_level': config.get('console_level', level)  # Default to root level
                }
    
    # Create a mapping of filenames to their handlers to avoid duplicate handlers
    file_handlers = {}
    
    # Set up console handler with filter for per-logger levels
    if handler is None:
        handler = logging.StreamHandler()
    console_handler = handler
    
    # Apply formatter to console handler
    if formatter is None:
        # Use color formatter for console if supported
        if isinstance(console_handler, logging.StreamHandler) and stream_supports_colour(console_handler.stream):
            formatter = ColourFormatter()
        else:
            formatter = logging.Formatter(
                '[{asctime}] [{levelname:<8}] {name}: {message}',
                datefmt='%Y-%m-%d %H:%M:%S',
                style='{'
            )
    console_handler.setFormatter(formatter)
    
    # Create a filter function for console logging based on per-logger levels
    def console_filter(record):
        # First check for exact matches
        if record.name in log_files:
            return record.levelno >= log_files[record.name]['console_level']
            
        # Then check for child loggers (logger names that start with the configured name plus a dot)
        for name, config in log_files.items():
            if record.name.startswith(f"{name}."):
                return record.levelno >= config['console_level']
                
        # Default to root level for unconfigured loggers
        return record.levelno >= level
    
    console_handler.addFilter(console_filter)
    console_handler.setLevel(logging.DEBUG)  # Set to most verbose, let the filter handle the actual level
    
    # Configure the root logger to handle all messages
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # Remove all existing handlers from root logger
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)
    
    # Add console handler to root logger
    root_logger.addHandler(console_handler)
    
    # Set up file handlers for configured loggers
    for logger_name, config in log_files.items():
        filename = config['filename']
        if not filename:
            continue
        
        # Create or get existing file handler for this filename
        if filename not in file_handlers:
            file_handler = logging.handlers.RotatingFileHandler(
                f'logs/{filename}',
                maxBytes=5*1024*1024,  # 5MB
                backupCount=5,
                encoding='utf-8'
            )
            file_handler.setLevel(logging.DEBUG)  # Always DEBUG for files
            # Use a simple formatter for file output
            file_formatter = logging.Formatter(
                '[{asctime}.{msecs:03.0f}] [{levelname:<8}] {name}: {message}',
                datefmt='%Y-%m-%d %H:%M:%S',
                style='{'
            )
            file_handler.setFormatter(file_formatter)
            file_handlers[filename] = file_handler
        else:
            file_handler = file_handlers[filename]
        
        # Configure the specific logger
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        
        # Remove any existing file handlers from this logger
        for h in logger.handlers[:]:
            if isinstance(h, logging.FileHandler):
                logger.removeHandler(h)
        
        # Add the file handler
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        logger.propagate = False  # Prevent logs from propagating to root logger
    
    # Set up default file handler for all other loggers
    default_file_handler = logging.handlers.RotatingFileHandler(
        'logs/main.log',
        maxBytes=5*1024*1024,  # 5MB
        backupCount=5,
        encoding='utf-8'
    )
    default_file_handler.setLevel(logging.DEBUG)
    
    # Use a simple formatter for the default file output with milliseconds
    default_file_formatter = logging.Formatter(
        '[{asctime}.{msecs:03.0f}] [{levelname:<8}] {name}: {message}',
        datefmt='%Y-%m-%d %H:%M:%S',
        style='{'
    )
    default_file_handler.setFormatter(default_file_formatter)
    
    # Add default file handler to root logger for any unconfigured loggers
    root_logger.addHandler(default_file_handler)
    
    # Log startup message once per unique log file
    startup_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]  # Include milliseconds
    startup_msg = f"\n{'='*80}\nApplication started at {startup_time}\n{'='*80}"
    
    # Track which log files we've already written the startup message to
    logged_files = set()
    
    # Log to all configured log files, but only once per unique file
    for logger_name, config in log_files.items():
        filename = config['filename']
        if not filename:
            continue
        if filename not in logged_files:
            logger = logging.getLogger(logger_name)
            logger.debug(startup_msg)
            logged_files.add(filename)
    
    # Also log to the default log file if we haven't already
    default_log_file = 'logs/ravenfall-bot.log'
    if default_log_file not in logged_files:
        root_logger.debug(startup_msg)
        logged_files.add(default_log_file)

def stream_supports_colour(stream: Any) -> bool:
    is_a_tty = hasattr(stream, "isatty") and stream.isatty()

    # Pycharm and Vscode support colour in their inbuilt editors
    if "PYCHARM_HOSTED" in os.environ or os.environ.get("TERM_PROGRAM") == "vscode":
        return is_a_tty

    if sys.platform != "win32":
        return is_a_tty

    # ANSICON checks for things like ConEmu
    # WT_SESSION checks if this is Windows Terminal
    return is_a_tty and ("ANSICON" in os.environ or "WT_SESSION" in os.environ)


def stream_supports_rgb(stream: Any) -> bool:
    if not stream_supports_colour(stream):
        return False

    if "COLORTERM" in os.environ:
        return os.environ["COLORTERM"] in ("truecolor", "24bit")

    return False

class ColourFormatter(logging.Formatter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        self._handler: logging.StreamHandler[Any] = kwargs.get("handler", logging.StreamHandler())

        self._supports_colour: bool = stream_supports_colour(self._handler.stream)
        self._supports_rgb: bool = stream_supports_rgb(self._handler.stream)

        self._colours: dict[int, str] = {}
        self._RESET: str = "\033[0m"

        if self._supports_rgb:
            self._colours = {
                logging.DEBUG: "\x1b[40;1m",
                logging.INFO: "\x1b[38;2;100;55;215;1m",
                logging.WARNING: "\x1b[38;2;204;189;51;1m",
                logging.ERROR: "\x1b[38;2;161;38;46m",
                logging.CRITICAL: "\x1b[48;2;161;38;46",
            }

        elif self._supports_colour:
            self._colours = {
                logging.DEBUG: "\x1b[40;1m",
                logging.INFO: "\x1b[34;1m",
                logging.WARNING: "\x1b[33;1m",
                logging.ERROR: "\x1b[31m",
                logging.CRITICAL: "\x1b[41",
            }

        self._FORMATS: dict[int, logging.Formatter] = {
            level: logging.Formatter(
                f"\x1b[30;1m%(asctime)s\x1b[0m {colour}%(levelname)-8s\x1b[0m {colour}%(name)s\x1b[0m %(message)s"
            )
            for level, colour in self._colours.items()
        }

    def format(self, record: logging.LogRecord) -> str:
        formatter: logging.Formatter | None = self._FORMATS.get(record.levelno, None)
        if formatter is None:
            formatter = self._FORMATS[logging.DEBUG]

        # Override the traceback to always print in red
        if record.exc_info:
            text = formatter.formatException(record.exc_info)
            record.exc_text = f"\x1b[31m{text}\x1b[0m"

        output = formatter.format(record)

        # Remove the cache layer
        record.exc_text = None
        return output


