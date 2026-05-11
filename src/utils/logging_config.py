import io
import sys
import logging
from pathlib import Path

def create_txt_file(file_path: str) -> None:
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as file:
            file.write('')
    except Exception as e:
        print(f"Error creating file {file_path}: {e}")
    return None

def setup_logging(
        file_name, logger_name=False, debug_mode=False) -> logging.Logger:
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    LOGS_DIR = BASE_DIR / "logs"

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    full_log_path = LOGS_DIR / file_name

    if sys.platform == "win32":
        if not isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    
    if not full_log_path.exists():
        create_txt_file(full_log_path)
    
    global_level = logging.DEBUG if debug_mode else logging.INFO
    log_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    logger = logging.getLogger(logger_name)
    logger.setLevel(global_level)

    if logger.hasHandlers():
        logger.handlers.clear()

    file_handler = logging.FileHandler(full_log_path, encoding='utf-8')
    file_handler.setFormatter(log_format)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(log_format)
    stream_handler.setLevel(logging.WARNING)
    logger.addHandler(stream_handler)
    
    logging.getLogger("opcua").setLevel(logging.ERROR)
    logging.getLogger("asyncua").setLevel(logging.ERROR)
    logging.getLogger("matplotlib").setLevel(logging.ERROR)
    logging.getLogger("PIL").setLevel(logging.ERROR)
    logging.getLogger("paho").setLevel(logging.WARNING)

    return logger