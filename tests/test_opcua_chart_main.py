import unittest
from unittest.mock import MagicMock, patch, mock_open
import textwrap

from src.core.opcua_chart_main import VisualizerFactory, main
from src.core.opcua_chart import ChartPieVisualizerPLC

class TestIndustrialSystemOrchestrator(unittest.TestCase):

    def setUp(self):
        self.mock_plc = MagicMock()
        self.mock_plc._plc_name = "MockPLC"
        self.pou = "PLC_PRG"
        self.vars = ["var1", "var2"]

    def test_visualizer_factory_pie_creation(self):
        """Test if the VisualizerFactory creates a ChartPieVisualizerPLC when 
            type is 'pie'."""      
        config_pie = {'type': 'pie'} 
        chart = VisualizerFactory.create(
            config_pie, self.mock_plc, self.pou, self.vars)
        
        self.assertIsInstance(chart, ChartPieVisualizerPLC)

    @patch('src.core.opcua_chart_main.setup_logging')
    @patch('src.core.opcua_chart_main.OPCUAClient')
    @patch('src.core.opcua_chart_main.VisualizerFactory.create')
    def test_main_execution_single_chart(
        self, mock_factory, mock_plc_class, mock_log):
        """Test the main function execution with a single chart configuration."""
        yaml_content = textwrap.dedent("""
            plc_connection:
              ip: "127.0.0.1"
              name: "Test"
              pou_name: "PRG"
            dashboard_layout:
              - type: "pie"
                vars: ["v1", "v2"]
                title: "Teste"
        """).strip()

        instance = mock_plc_class.return_value
        instance.connect.return_value = True
        
        mock_chart_instance = MagicMock()
        mock_factory.return_value = mock_chart_instance

        with patch('builtins.open', mock_open(read_data=yaml_content)):
            main('fake.yaml')
            
            instance.connect.assert_called()
            mock_factory.assert_called_once()
            mock_chart_instance.start_monitoring.assert_called_once()

    # --- Teste Main: Modo Dashboard ---
    @patch('src.core.opcua_chart_main.setup_logging')
    @patch('src.core.opcua_chart_main.PLCDashboard')
    @patch('src.core.opcua_chart_main.OPCUAClient')
    def test_main_dashboard_mode(self, mock_plc_class, mock_dash, mock_log):
        """Test the main function execution in dashboard mode with multiple 
            chart configurations."""
        yaml_content = textwrap.dedent("""
            plc_connection:
              ip: "127.0.0.1"
              name: "Test"
              pou_name: "PRG"
            dashboard_layout:
              - type: "pie"
                vars: ["v1"]
              - type: "bar"
                vars: ["v2"]
        """).strip()

        instance = mock_plc_class.return_value
        instance.connect.return_value = True
        
        mock_dash_instance = mock_dash.return_value

        with patch('builtins.open', mock_open(read_data=yaml_content)):
            main('fake.yaml')
            
            mock_dash.assert_called_once()
            mock_dash_instance.start.assert_called_once()

if __name__ == '__main__':
    unittest.main(verbosity=2)