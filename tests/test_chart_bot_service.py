import io
import matplotlib
import unittest
matplotlib.use('Agg') 
from src.services.chart_bot_service import ChartGeneratorService

class TestChartGeneratorService(unittest.TestCase):

    def test_generate_line_chart_returns_valid_buffer(self):
        """Tests if the generate_line_chart method returns a valid image buffer."""
        topic = "Temperatura_Tanque"
        history_data = [
            ("10:00:01", 20.5), ("10:00:02", 21.0), ("10:00:03", 22.5), 
            ("10:00:04", 22.0), ("10:00:05", 23.5)]
        
        result = ChartGeneratorService.generate_line_chart(topic, history_data)
        
        self.assertIsInstance(result, io.BytesIO)
        self.assertGreater(
            result.getbuffer().nbytes, 0,
            "O buffer de imagem não deve estar vazio.")
        
        result.seek(0)
        header = result.read(4)
        self.assertEqual(header, b'\x89PNG')

    def test_generate_comparison_bar_returns_valid_buffer(self):
        """Tests if the generate_comparison_bar method returns a valid image 
            buffer."""
        current_values = {
            "Sensor_A": "45.0",
            "Sensor_B": "12.7",
            "Sensor_C": "90.1"
        }

        result = ChartGeneratorService.generate_comparison_bar(current_values)
        
        self.assertIsInstance(result, io.BytesIO)
        self.assertGreater(result.getbuffer().nbytes, 0)
        
        result.seek(0)
        self.assertEqual(result.read(4), b'\x89PNG')

    def test_generate_line_chart_with_empty_data(self):
        """Tests if the generate_line_chart method handles empty data correctly."""
        result = ChartGeneratorService.generate_line_chart("Vazio", [])
        self.assertIsInstance(result, io.BytesIO)
        self.assertGreater(result.getbuffer().nbytes, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)