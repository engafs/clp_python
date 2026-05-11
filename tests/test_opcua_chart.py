import unittest
from unittest.mock import MagicMock, patch
from src.core.opcua_chart import (
    ChartBarsVisualizerPLC, ChartLineVisualizerPLC, 
    ChartPieVisualizerPLC, PLCDashboard)

class TestChartVisualizers(unittest.TestCase):

    def setUp(self):
        self.mock_plc = MagicMock()
        self.mock_plc._plc_name = "PLC_TESTE"
        self.pou = "PLC_PRG"
        self.variables = ["var1", "var2"]

    def test_base_initialization(self):
        """Test initialization of ChartBarsVisualizerPLC with mocked PLC and 
            matplotlib."""
        with patch('matplotlib.pyplot.subplots',
                   return_value=(MagicMock(), MagicMock())):
            viz = ChartBarsVisualizerPLC(
                self.mock_plc, self.pou, self.variables)
            self.assertEqual(viz._pou_name, self.pou)
            self.assertEqual(viz._variables, self.variables)
            self.assertIsNotNone(viz.fig)
            self.assertIsNotNone(viz.ax)

    def test_setup_nodes_caching(self):
        """Test the node caching functionality in ChartBarsVisualizerPLC."""
        self.mock_plc.get_variable_path.return_value = "ns=4;s=Path"
        self.mock_plc.get_node.return_value = "MockNode"

        with patch('matplotlib.pyplot.subplots',
                   return_value=(MagicMock(), MagicMock())):
            viz = ChartBarsVisualizerPLC(
                self.mock_plc, self.pou, self.variables)
            viz._setup_nodes()
            
            self.assertEqual(self.mock_plc.get_variable_path.call_count, 2)
            self.assertIn("var1", viz._node_cache)
            self.assertEqual(viz._node_cache["var1"], "MockNode")

    def test_pie_chart_limit_validation(self):
        """Test that ChartPieVisualizerPLC raises an error when more than 3 
            variables are provided."""
        invalid_vars = ["v1", "v2", "v3", "v4"]
        with patch('matplotlib.pyplot.subplots',
                   return_value=(MagicMock(), MagicMock())):
            with self.assertRaises(ValueError) as context:
                ChartPieVisualizerPLC(self.mock_plc, self.pou, invalid_vars)
            
            self.assertIn("Pie charts accept a maximum of 3 variables.",
                          str(context.exception))

    def test_update_chart_flow(self):
        """Test the flow of updating the chart in ChartBarsVisualizerPLC, 
            including node value retrieval and rendering."""
        with patch('matplotlib.pyplot.subplots',
                   return_value=(MagicMock(), MagicMock())):
            viz = ChartBarsVisualizerPLC(
                self.mock_plc, self.pou, self.variables)
            
            mock_node = MagicMock()
            mock_node.get_value.return_value = 42.0
            viz._node_cache = {"var1": mock_node}

            with patch.object(viz, '_render_plot') as mock_render:
                viz._update_chart(None)
                
                mock_render.assert_called_once_with({"var1": 42.0})
                self.assertTrue(viz._is_plc_running)

    def test_handle_connection_error(self):
        """Test the error handling flow in ChartBarsVisualizerPLC when a 
            connection error occurs."""
        with patch('matplotlib.pyplot.subplots',
                   return_value=(MagicMock(), MagicMock())):
            viz = ChartBarsVisualizerPLC(
                self.mock_plc, self.pou, self.variables)
            
            self.mock_plc.connect_client_plc_codesys.return_value = False
            
            with patch.object(viz, '_render_error_state') as mock_render_error:
                viz._handle_connection_error("Timeout")
                
                self.assertFalse(viz._is_plc_running)
                mock_render_error.assert_called_with("Timeout")

    def test_line_chart_history_limit(self):
        """Test the history limit functionality in ChartLineVisualizerPLC."""
        max_pts = 10
        with patch('matplotlib.pyplot.subplots', 
                return_value=(MagicMock(), MagicMock())):

            viz = ChartLineVisualizerPLC(
                self.mock_plc, self.pou, ["v1"], max_points=max_pts)

            for i in range(10):
                viz._render_plot({"v1": float(i)})
            
            self.assertEqual(len(viz._history["v1"]["values"]), max_pts)
            
            self.assertEqual(viz._history["v1"]["values"][-1], 9.0)
            
            from datetime import datetime
            self.assertIsInstance(viz._history["v1"]["times"][-1], datetime)


class TestPLCDashboard(unittest.TestCase):

    def setUp(self):
        self.mock_plc = MagicMock()
        self.mock_plc.is_connected.return_value = True
        self.mock_plc.get_variable_path.return_value = "ns=4;s=Path"
        
        self.configs = [
            {'type': 'line', 'pou': 'P1', 'vars': ['v1'], 'title': 'Linha'},
            {'type': 'bar', 'pou': 'P1', 'vars': ['v2'], 'title': 'Barra'},
            {'type': 'pie', 'pou': 'P1', 'vars': ['v3'], 'title': 'Pizza'}
        ]

    @patch('matplotlib.pyplot.figure')
    @patch('matplotlib.pyplot.close')
    def test_dashboard_layout_setup(self, mock_close, mock_figure):
        """Test the layout setup of PLCDashboard, ensuring correct figure and 
            axes creation."""
        mock_fig_instance = mock_figure.return_value
        
        dash = PLCDashboard(self.mock_plc, self.configs)
        
        self.assertEqual(mock_fig_instance.add_subplot.call_count, 4)
        self.assertIn("P1.v1", dash._history)

    @patch('matplotlib.pyplot.figure')
    def test_map_nodes_integration(self, _):
        """Test the integration of node mapping in PLCDashboard, ensuring that
            nodes are correctly retrieved and stored in the configuration."""
        dash = PLCDashboard(self.mock_plc, self.configs)
        
        for cfg in dash._configs:
            self.assertIn('nodes', cfg)
            self.assertIn(cfg['vars'][0], cfg['nodes'])
            self.mock_plc.get_variable_path.assert_any_call(
                cfg['pou'], cfg['vars'][0])

    @patch('matplotlib.pyplot.figure')
    def test_update_all_charts_flow(self, _):
        """Test the flow of updating all charts in PLCDashboard, ensuring that
            node values are retrieved and charts are updated accordingly."""
        dash = PLCDashboard(self.mock_plc, self.configs)
        
        for cfg in dash._configs:
            cfg['ax'] = MagicMock()
            mock_node = MagicMock()
            mock_node.get_value.return_value = 10.0
            cfg['nodes'] = {cfg['vars'][0]: mock_node}

        dash._update_chart(None)

        for cfg in dash._configs:
            cfg['ax'].clear.assert_called_once()
            cfg['ax'].set_title.assert_called()

    @patch('matplotlib.pyplot.figure')
    def test_render_error_on_disconnection(self, _):
        """Test that PLCDashboard renders an error state when the PLC is 
            disconnected."""
        self.mock_plc._is_connected.return_value = False
        dash = PLCDashboard(self.mock_plc, self.configs)
        
        with patch.object(dash, '_render_error_state') as mock_error:
            dash._update_chart(None)
            mock_error.assert_called_once_with("PLC Disconnected")

    @patch('matplotlib.pyplot.figure')
    def test_draw_methods_dispatch(self, _):
        """Test that the correct draw methods are called based on chart type in 
            PLCDashboard."""
        dash = PLCDashboard(self.mock_plc, self.configs)
        
        with patch.object(dash, '_draw_line') as m_line, \
             patch.object(dash, '_draw_bar') as m_bar, \
             patch.object(dash, '_draw_pie') as m_pie:
            
            dash._update_chart(None)
            
            m_line.assert_called_once()
            m_bar.assert_called_once()
            m_pie.assert_called_once()
    
    def test_draw_status_panel_connected(self):
        """Test the status panel drawing in PLCDashboard when the PLC is 
            connected."""
        self.mock_plc._is_connected.return_value = True
        dash = PLCDashboard(self.mock_plc, self.configs)
        
        mock_ax = MagicMock()
        total_test = 150
        
        dash._draw_status_panel(mock_ax, total_test)
        
        mock_ax.clear.assert_called_once()
        mock_ax.set_facecolor.assert_called_with('#1e1e1e')
        mock_ax.axis.assert_called_with('off')
        
        self.assertEqual(mock_ax.text.call_count, 4)
        
        args, kwargs = mock_ax.text.call_args_list[3]
        self.assertEqual(kwargs['color'], '#00FF00')
        self.assertIn("● SISTEMA OPERACIONAL", args)

    def test_draw_status_panel_disconnected(self):
        """Test the status panel drawing in PLCDashboard when the PLC is 
            disconnected."""
        self.mock_plc._is_connected.return_value = False
        dash = PLCDashboard(self.mock_plc, self.configs)
        
        mock_ax = MagicMock()
        dash._draw_status_panel(mock_ax, 0)
        
        args, kwargs = mock_ax.text.call_args_list[3]
        self.assertEqual(kwargs['color'], '#FF0000')

if __name__ == '__main__':
    import matplotlib
    matplotlib.use('Agg')
    unittest.main(verbosity=2)