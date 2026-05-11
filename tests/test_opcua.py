import unittest
from unittest.mock import MagicMock, patch
from opcua import ua
from src.core.opcua import OPCUAClient
import logging
logging.disable(logging.CRITICAL)


class TestOPCUA(unittest.TestCase):

    def setUp(self):
        self.plc_ip = "192.168.15.15"
        self.plc_name = "PLC_TEST"
        self.plc = OPCUAClient(self.plc_ip, self.plc_name)

    def test_validate_ip_format(self):
        """Test the IP address format validation in OPCUAClient."""
        self.assertTrue(self.plc._validate_plc_ip_format())
        self.plc._plc_ip = "999.999.999.999"
        self.assertFalse(self.plc._validate_plc_ip_format())

    @patch('src.core.opcua.Client')
    def test_connect_success(self, mock_client_class):
        """Test successful connection to the OPC UA server."""
        mock_instance = mock_client_class.return_value
        
        result = self.plc.connect()
        
        self.assertTrue(result)
        self.assertTrue(self.plc._is_connected())
        mock_client_class.assert_called_with(f"opc.tcp://{self.plc_ip}:4840")
        mock_instance.connect.assert_called_once()

    def test_ensure_connected_raises_error(self):
        """Test that get_value raises ConnectionError when not connected."""
        with self.assertRaises(ConnectionError):
            self.plc.get_value("PLC_PRG", "myVar")
    
    @patch('src.core.opcua.Client')
    def test_get_server_state_running(self, _):
        """Test that _get_server_state correctly identifies 'Running' state."""
        self.plc._client = MagicMock()
        mock_node = MagicMock()
        mock_node.get_value.return_value = 0 # 0 = Running
        self.plc._client.get_node.return_value = mock_node

        state = self.plc._get_server_state()

        self.assertEqual(state, "Running")
        self.plc._client.get_node.assert_called_with("ns=0;i=2259")

    @patch('src.core.opcua.Client')
    def test_get_server_state_shutdown(self, _):
        """Test that _get_server_state correctly identifies 'Shutdown' state."""
        self.plc._client = MagicMock()
        mock_node = MagicMock()
        mock_node.get_value.return_value = 4 # 4 = Shutdown
        self.plc._client.get_node.return_value = mock_node

        state = self.plc._get_server_state()

        self.assertEqual(state, "Shutdown")

    @patch('src.core.opcua.Client')
    def test_get_server_state_error_handling(self, _):
        """Test that _get_server_state handles errors gracefully."""
        self.plc._client = MagicMock()
        self.plc._client.get_node.side_effect = Exception("Node not accessible")

        state = self.plc._get_server_state()

        self.assertEqual(state, "Disconnected/Error")
    
    @patch('src.core.opcua.time.sleep')
    @patch('src.core.opcua.Client')
    def test_check_process_health_success(self, _, mock_sleep):
        """Test that _check_process_health returns True when values are 
            within threshold."""
        self.plc._client = MagicMock()
        
        self.plc.get_value = MagicMock(side_effect=[10, 11])
        
        result = self.plc._check_process_health(
            "GVL_telegram", "ui_heartbeat_clp", timeout=1)
        
        self.assertTrue(result)
        self.assertEqual(self.plc.get_value.call_count, 2)
        mock_sleep.assert_called_once_with(1)

    @patch('src.core.opcua.time.sleep')
    @patch('src.core.opcua.Client')
    def test_check_process_health_stopped(self, _, mock_sleep):
        """Test that _check_process_health returns False when values exceed 
            threshold."""
        self.plc._client = MagicMock()
        
        self.plc.get_value = MagicMock(side_effect=[50, 50])
        
        result = self.plc._check_process_health(
            "GVL_telegram", "ui_heartbeat_plc", timeout=3)
        
        self.assertFalse(result)
        self.assertEqual(self.plc.get_value.call_count, 2)
        mock_sleep.assert_called_once_with(3)

    @patch('src.core.opcua.Client')
    def test_check_process_health_first_read_fail(self, _):
        """Test that _check_process_health returns False if the first read 
            fails."""
        self.plc._client = MagicMock()
        self.plc.get_value = MagicMock(return_value=None)
        
        result = self.plc._check_process_health(
            "GVL_telegram", "ui_heartbeat_plc")
        
        self.assertFalse(result)
    
    @patch('src.core.opcua.OPCUAClient._is_connected')
    def test_is_plc_healthy_not_connected(self, mock_is_connected):
        """Test that _is_plc_healthy returns False if not connected to the 
            server."""
        mock_is_connected.return_value = False
        
        result = self.plc._is_plc_healthy()
        
        self.assertFalse(result)
        self.plc.get_server_state = MagicMock()
        self.plc.get_server_state.assert_not_called()

    @patch('src.core.opcua.OPCUAClient._get_server_state')
    @patch('src.core.opcua.OPCUAClient._is_connected')
    def test_is_plc_healthy_server_not_running(
        self, mock_is_connected, mock_state):
        """Test that _is_plc_healthy returns False if server state is not 
            'Running'."""
        mock_is_connected.return_value = True
        mock_state.return_value = "Stop" # Simula servidor parado
        
        result = self.plc._is_plc_healthy()
        
        self.assertFalse(result)

    @patch('src.core.opcua.OPCUAClient._check_process_health')
    @patch('src.core.opcua.OPCUAClient._get_server_state')
    @patch('src.core.opcua.OPCUAClient._is_connected')
    def test_is_plc_healthy_full_success(
        self, mock_is_connected, mock_state, mock_health):
        """Test that _is_plc_healthy returns True when all checks pass."""
        mock_is_connected.return_value = True
        mock_state.return_value = "Running"
        mock_health.return_value = True # Simulate running logic and healthy process
        
        result = self.plc._is_plc_healthy(
            heartbeat_pou="GVL", heartbeat_var="hb")
        
        self.assertTrue(result)
        mock_health.assert_called_once_with("GVL", "hb")

    @patch('src.core.opcua.OPCUAClient._check_process_health')
    @patch('src.core.opcua.OPCUAClient._get_server_state')
    @patch('src.core.opcua.OPCUAClient._is_connected')
    def test_is_plc_healthy_logic_stopped(
        self, mock_is_connected, mock_state, mock_health):
        """Test that _is_plc_healthy returns False if server is running but 
            logic is stopped."""
        mock_is_connected.return_value = True
        mock_state.return_value = "Running"
        mock_health.return_value = False # Simulate running server but stopped logic/process
        
        result = self.plc._is_plc_healthy(
            heartbeat_pou="GVL", heartbeat_var="hb")
        
        self.assertFalse(result)

    @patch('src.core.opcua.Client')
    def test_find_pou_node_id_caching(self, _):
        """Test that find_pou_node_id returns cached node ID without querying 
            the server."""
        self.plc._client = MagicMock()
        self.plc._pou_node_cache["MAIN_POU"] = "ns=4;s=NODE_123"
        
        node_id = self.plc.find_pou_node_id("MAIN_POU")
        
        self.assertEqual(node_id, "ns=4;s=NODE_123")
        self.plc._client.get_root_node.assert_not_called()

    @patch('src.core.opcua.Client')
    def test_get_value_flow(self, mock_client_class):
        """Test the full flow of get_value including node retrieval and value 
            reading."""
        self.plc._client = MagicMock()
        mock_node = MagicMock()
        mock_node.get_value.return_value = 100
        self.plc._client.get_node.return_value = mock_node
        
        self.plc._pou_node_cache["PLC_PRG"] = "ns=4;s=POU_PATH"
        
        value = self.plc.get_value("PLC_PRG", "varTeste")
        
        self.assertEqual(value, 100)
        self.plc._client.get_node.assert_called_with("ns=4;s=POU_PATH.varTeste")

    @patch('src.core.opcua.Client')
    def test_set_value_type_validation_fail(self, _):
        """Test that _set_value returns False and does not set value if type 
            validation fails."""
        self.plc._client = MagicMock()
        mock_node = MagicMock()
        
        mock_node.get_data_type_as_variant_type.return_value = ua.VariantType.Boolean
        self.plc._client.get_node.return_value = mock_node
        self.plc._pou_node_cache["PLC_PRG"] = "ns=4;s=POU"

        result = self.plc._set_value("PLC_PRG", "varBool", 123)
        
        self.assertFalse(result)
        mock_node._set_value.assert_not_called()

if __name__ == '__main__':
    unittest.main(verbosity=2)