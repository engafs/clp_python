import yaml
from pathlib import Path
from typing import Dict, Any, Optional, Union
from src.utils.logging_config import setup_logging

class ConfigLoader:
    """
    Utility class specialized in handling external configuration files 
    for the industrial monitoring system.
    """

    @staticmethod
    def _load_yaml(full_path: Path) -> Dict[str, Any]:
        """
        Loads and parses a YAML configuration file.

        Args:
            full_path (Path): The absolute path to the YAML file.

        Returns:
            Dict[str, Any]: The parsed configuration data as a dictionary.

        Raises:
            FileNotFoundError: If the configuration file does not exist.
            ValueError: If the file is empty or contains invalid YAML syntax.
        """
        logger = setup_logging("system_startup.txt", logger_name="CONFIG_LOADER")

        if not full_path.exists():
            logger.error(f"Configuration file not found: {full_path}")
            raise FileNotFoundError(f"Arquivo não encontrado em: {full_path}")

        try:
            with open(full_path, 'r', encoding='utf-8') as file:
                config_data: Optional[Dict[str, Any]] = yaml.safe_load(file)
                
                if config_data is None:
                    logger.error(f"The YAML file is empty: {full_path}")
                    raise ValueError(f"The YAML file at {full_path} is empty.")
                
                return config_data

        except yaml.YAMLError as e:
            logger.error(f"YAML syntax error in {full_path}: {e}")
            raise ValueError(f"Erro ao processar sintax do arquivo YAML: {e}")
        except Exception as e:
            logger.error(f"Unexpected error loading {full_path}: {e}")
            raise
    
    @staticmethod
    def _save_yaml(full_path: Path, data: Dict[str, Any]) -> bool:
        """
        Serializes a dictionary and saves it to a YAML file.

        Args:
            full_path (Path): The absolute path where the file will be saved.
            data (Dict[str, Any]): The dictionary to be persisted.

        Returns:
            bool: True if the operation was successful, False otherwise.
        """
        logger = setup_logging("system_io.txt", logger_name="CONFIG_WRITER")
        
        try:
            # Ensure the directory exists before saving
            full_path.parent.mkdir(parents=True, exist_ok=True)

            with open(full_path, 'w', encoding='utf-8') as file:
                yaml.dump(data, file, allow_unicode=True, 
                          sort_keys=False, default_flow_style=False)
            
            logger.info(f"✅ Configuration successfully persisted to: {full_path}")
            return True

        except Exception as e:
            logger.error(f"❌ Critical failure while saving YAML to {full_path}: {e}")
            return False
    
    @staticmethod
    def _get_clean_value(payload: str) -> Union[bool, int, float, str]:
        """
        Parses and sanitizes the MQTT payload to extract the actual sensor value.
        
        Splits 'key:value' formats, handles booleans, and converts to 
        the most appropriate numeric type (int or float).

        Args:
            payload (str): The raw MQTT message (e.g., "status:true").

        Returns:
            Union[bool, int, float, str]: Converted value (bool, int, float, or raw str as fallback).
        """
        try:
            if not payload:
                return ""
                
            raw: str = payload.split(':')[-1].strip()

            # Handle Booleans
            if raw.lower() in ['true', 'false', '1', '0']:
                return raw.lower() in ['true', '1']

            # Handle Numerics
            val: float = float(raw)
            return int(val) if val.is_integer() else val

        except (ValueError, IndexError):
            return payload.strip() if payload else ""
    
    @staticmethod
    def _is_graphable(value: Any) -> bool:
        """
        Validates if a value is suitable for numeric plotting (Line Charts).
        
        Excludes Booleans (which Python treats as 1/0 internally) and None,
        while allowing numeric strings and actual numbers.

        Args:
            value (Any): The value to be checked.

        Returns:
            bool: True if the value can be converted to a float and isn't a boolean/None.
        """
        if value is None or isinstance(value, bool):
            return False

        try:
            # Handle string-based numbers with commas
            float(str(value).replace(',', '.'))
            return True
        except (ValueError, TypeError):
            return False