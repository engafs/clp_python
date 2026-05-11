import unittest
from unittest.mock import patch, mock_open, MagicMock
from pathlib import Path
from src.utils.io_utils import ConfigLoader
import logging
logging.disable(logging.CRITICAL)

class TestConfigLoader(unittest.TestCase):

    def tearDown(self):
        """Clean up logging handlers to avoid ResourceWarnings."""
        logger_names = ["CONFIG_LOADER", "CONFIG_WRITER"]
        for name in logger_names:
            logger = logging.getLogger(name)
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

    # --- Tests for _load_yaml ---

    @patch("src.utils.logging_config.setup_logging")
    @patch("pathlib.Path.exists")
    def test_load_yaml_file_not_found(self, mock_exists, mock_log):
        """Tests if FileNotFoundError is raised when file does not exist."""
        mock_exists.return_value = False
        with self.assertRaises(FileNotFoundError):
            ConfigLoader._load_yaml(Path("fake_config.yaml"))

    @patch("src.utils.logging_config.setup_logging")
    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data="")
    def test_load_yaml_empty_file(self, mock_file, mock_exists, mock_log):
        """Tests if ValueError is raised when the YAML file is empty."""
        mock_exists.return_value = True
        with self.assertRaisesRegex(ValueError, "is empty"):
            ConfigLoader._load_yaml(Path("empty.yaml"))

    @patch("src.utils.logging_config.setup_logging")
    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data="key: value")
    def test_load_yaml_success(self, mock_file, mock_exists, mock_log):
        """Tests if a valid YAML file is loaded correctly."""
        mock_exists.return_value = True
        result = ConfigLoader._load_yaml(Path("valid.yaml"))
        self.assertEqual(result, {"key": "value"})

    # --- Tests for _save_yaml ---

    @patch("src.utils.logging_config.setup_logging")
    @patch("pathlib.Path.mkdir")
    @patch("builtins.open", new_callable=mock_open)
    def test_save_yaml_success(self, mock_file, mock_mkdir, mock_log):
        """Tests if the dictionary is correctly serialized to YAML."""
        data = {"sensor": "A1"}
        path = Path("fake_dir/config.yaml")
        
        mock_mkdir.reset_mock()
        
        success = ConfigLoader._save_yaml(path, data)
        
        self.assertTrue(success)

        self.assertTrue(mock_mkdir.called)
        mock_file.assert_called_with(path, 'w', encoding='utf-8')

    # --- Tests for _get_clean_value ---

    def test_get_clean_value_numeric(self):
        """Tests conversion to int and float."""
        self.assertEqual(ConfigLoader._get_clean_value("temp:25.0"), 25)  # int if integer
        self.assertEqual(ConfigLoader._get_clean_value("temp:25.5"), 25.5) # float
        self.assertEqual(ConfigLoader._get_clean_value("id:100"), 100)

    def test_get_clean_value_boolean(self):
        """Tests boolean detection from strings."""
        self.assertTrue(ConfigLoader._get_clean_value("status:true"))
        self.assertTrue(ConfigLoader._get_clean_value("active:1"))
        self.assertFalse(ConfigLoader._get_clean_value("status:false"))
        self.assertFalse(ConfigLoader._get_clean_value("active:0"))

    def test_get_clean_value_fallback(self):
        """Tests fallback to original string on parsing failure."""
        self.assertEqual(ConfigLoader._get_clean_value("mode:auto"), "mode:auto")
        self.assertEqual(ConfigLoader._get_clean_value(""), "")

    # --- Tests for _is_graphable ---

    def test_is_graphable_valid(self):
        """Tests values that should be graphable (numeric)."""
        self.assertTrue(ConfigLoader._is_graphable(10))
        self.assertTrue(ConfigLoader._is_graphable(25.5))
        self.assertTrue(ConfigLoader._is_graphable("25.5"))
        self.assertTrue(ConfigLoader._is_graphable("25,5")) # comma handling

    def test_is_graphable_invalid(self):
        """Tests values that should NOT be graphable."""
        self.assertFalse(ConfigLoader._is_graphable(True)) # Booleans excluded
        self.assertFalse(ConfigLoader._is_graphable(None))
        self.assertFalse(ConfigLoader._is_graphable("not_a_number"))


if __name__ == "__main__":
    unittest.main(verbosity=2)