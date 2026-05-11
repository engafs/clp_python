import matplotlib
matplotlib.use('Agg')  # Avoid close terminal after graphic generator
import matplotlib.pyplot as plt
import io
from typing import List, Tuple, Dict, Any, Union

class ChartGeneratorService:
    """
    Service responsible for generating graphical visualizations of system data
    using Matplotlib, returning images as byte buffers.
    """

    @staticmethod
    def generate_line_chart(topic: str, history_data: List[Tuple[Any, Union[int, float]]]) -> io.BytesIO:
        """
        Generates a line chart showing the temporal trend of a specific sensor or variable.

        Args:
            topic: The name/label of the variable being plotted.
            history_data: A list of tuples containing (timestamp/label, value).

        Returns:
            io.BytesIO: A buffer containing the PNG image data of the chart.
        """
        # Data extraction with implicity typing by list comprehension
        times: List[Any] = [item[0] for item in history_data]
        values: List[Union[int, float]] = [item[1] for item in history_data]

        plt.figure(figsize=(10, 6))
        plt.plot(times, values, marker='o',
                 linestyle='-', color='b', linewidth=2)
        
        plt.title(f"Tendência Temporal: {topic}", fontsize=14)
        plt.xlabel("Hora da Leitura", fontsize=12)
        plt.ylabel("Valor", fontsize=12)
        plt.grid(True, linestyle='--', alpha=0.7)

        # Otimização do eixo X para não sobrecarregar com muitas labels
        if len(times) > 10:
            plt.xticks(range(0, len(times), len(times) // 10))
            
        plt.xticks(rotation=45)
        plt.tight_layout()

        # Salva o gráfico em um buffer de bytes para envio via API do Telegram
        buf: io.BytesIO = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()
        return buf

    @staticmethod
    def generate_comparison_bar(
        current_values: Dict[str, Union[int, float, str]]) -> io.BytesIO:
        """
        Generates a bar chart comparing current values from multiple sensors.

        Args:
            current_values: A dictionary where keys are sensor names and values are readings.

        Returns:
            io.BytesIO: A buffer containing the PNG image data of the chart.
        """
        plt.figure(figsize=(10, 6))
        
        labels: List[str] = list(current_values.keys())
        values: List[float] = [float(v) for v in current_values.values()]
        
        bars = plt.bar(labels, values, color=plt.cm.tab10.colors)
        plt.bar_label(bars, padding=3, fmt='%.2f')
        
        plt.title("Comparativo Instantâneo de Sensores", fontsize=14)
        plt.ylabel("Valores", fontsize=12)
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        buf: io.BytesIO = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close('all')
        return buf