import matplotlib.pyplot as plt
import matplotlib.animation as animation
from datetime import datetime
import matplotlib.dates as mdates
import os
from src.core.opcua import OPCUAClient
from typing import List, Dict, Any, Optional, Callable, Union
from logging import getLogger, Logger

class BaseChartVisualizerPLC:
    """
    Base class for real-time PLC data visualization using Matplotlib.

    Attributes:
        fig (plt.Figure): Matplotlib figure object.
        ax (plt.Axes): Matplotlib axes object.
    """

    def __init__(self, plc_instance: OPCUAClient, pou_name: str, variables: List[str],
                 interval: int = 500, title: Optional[str] = None, 
                 labels: Optional[List[str]] = None) -> None:
        """
        Initializes the base visualizer.

        Args:
            plc_instance (OPCUAClient): An instance of the OPCUA client class.
            pou_name (str): Name of the POU/GVL to monitor.
            variables (List[str]): List of variable names to track.
            interval (int): Refresh rate in milliseconds.
            title (Optional[str]): Chart title.
            labels (Optional[List[str]]): Custom labels for the variables.
        """
        self._plc_instance: Any = plc_instance
        self._pou_name: str = pou_name
        self._variables: List[str] = variables
        self._interval: int = interval
        self._node_cache: Dict[str, Any] = {}
        self._is_plc_running: bool = True
        self._logger: Logger = getLogger("CHART_LOG")

        self._chart_title: str = title or f"Monitoring: {self._pou_name}"
        self._chart_labels: List[str] = labels if labels and \
            len(labels) == len(variables) else variables
        
        self.fig, self.ax = plt.subplots(figsize=(10, 6))
        self.ani: Optional[animation.FuncAnimation] = None

    def _setup_nodes(self) -> None:
        """Maps PLC variable names to OPC UA node objects and stores them in cache."""
        if not self._node_cache:
            self._logger.info(f"--- 🔄 Setting nodes: {self._pou_name} ---")
        
        for var_name in self._variables:
            if var_name in self._node_cache: 
                continue
            
            full_path: Optional[str] = self._plc_instance.get_variable_path(
                self._pou_name, var_name)
            
            if full_path:
                try:
                    node: Any = self._plc_instance.get_node(full_path)
                    self._node_cache[var_name] = node
                    self._logger.info(f"✅ '{var_name}' mapped.")
                except Exception as e:
                    self._logger.debug(f"⚠️ Error to map {var_name}: {e}")

    def _update_chart(self, _: Any) -> None:
        """
        Callback function for FuncAnimation to fetch data and refresh the plot.
        
        Args:
            _ (Any): Current frame (unused).
        """
        try:
            data_to_render: Dict[str, float] = {}
            for var_name, node_obj in self._node_cache.items():
                data_to_render[var_name] = float(node_obj.get_value())
            
            self._is_plc_running = True
            self._render_plot(data_to_render)
        except Exception as e:
            self._handle_connection_error(e)

    def _handle_connection_error(self, error: Exception) -> None:
        """
        Logs connection errors and attempts to reconnect.

        Args:
            error (Exception): The caught exception.
        """
        self._is_plc_running = False
        self._logger.error(f"Lost link: {error}")

        if self._plc_instance.connect():
            self._node_cache.clear()
            self._setup_nodes()
        self._render_error_state(str(error))

    def _render_error_state(self, msg: str) -> None:
        """
        Displays an error message on the chart UI.

        Args:
            msg (str): Error message to display.
        """
        self.ax.clear()
        self.ax.set_facecolor('#FFF0F0')
        self.ax.text(0.5, 0.5, f"⚠️ CONNECTION ERROR\n{msg[:40]}", 
                     ha='center', va='center', transform=self.ax.transAxes)
        self.fig.canvas.draw()

    def start_monitoring(self) -> None:
        """Initializes nodes and starts the Matplotlib animation loop."""
        self._setup_nodes()
        self.fig.canvas.mpl_connect('close_event', self._on_close)
        self.ani = animation.FuncAnimation(
            self.fig, self._update_chart, 
            interval=self._interval, cache_frame_data=False)
        plt.show()

    def _on_close(self, event: Any) -> None:
        """
        Handles the window close event to safely disconnect the PLC.

        Args:
            event (Any): Close event object.
        """
        self._logger.info("📉 Closing chart...")
        if self.ani: 
            self.ani.event_source.stop()
        self._plc_instance.disconnect()
        os._exit(0)

    def _render_plot(self, data: Dict[str, float]) -> None:
        """
        Abstract method to be implemented by subclasses for specific chart types.

        Args:
            data (Dict[str, float]): Dictionary of variable names and their current values.
        """
        raise NotImplementedError("Subclasses must implement _render_plot")


class ChartBarsVisualizerPLC(BaseChartVisualizerPLC):
    """Visualizes PLC data using a dynamic Bar Chart."""
    def __init__(
            self, plc_instance: OPCUAClient, pou_name: str, variables: List[str],
            interval: int = 500, title: Optional[str] = None, 
            labels: Optional[List[str]] = None) -> None:
        """Initialize Bar Chart visualizer."""
        super().__init__(
            plc_instance=plc_instance, pou_name=pou_name,
            variables=variables, interval=interval,
            title=title, labels=labels)

    def _render_plot(self, data: Dict[str, float]) -> None:
        """Renders the bar plot with current data."""
        self.ax.clear()
        values: List[float] = list(data.values())

        num_vars: int = len(values)
        colors: Any = plt.cm.tab10(range(num_vars))
        
        bars = self.ax.bar(
            self._chart_labels, values, color=colors,
            edgecolor='black', linewidth=1)
        
        formatted_labels: List[str] = [
            f"{v:.2f}" if v % 1 != 0 else f"{int(v)}" 
            for v in values
        ]
        
        self.ax.bar_label(bars, padding=3, labels=formatted_labels)
        self.ax.set_title(self._chart_title)

        self.ax.set_ylim(
            0, max(values) * 1.15 if values and max(values) > 0 else 10)
        self.fig.tight_layout()


class ChartLineVisualizerPLC(BaseChartVisualizerPLC):
    """Visualizes PLC data using a real-time Line Chart with history."""

    def __init__(
            self, plc_instance: OPCUAClient, pou_name: str, variables: List[str],
            interval: int = 500, title: Optional[str] = None, 
            labels: Optional[List[str]] = None, max_points: int = 100) -> None:
        """
        Args:
            plc_instance: An instance of the OPCUA client class.
            pou_name: Name of the POU/GVL to monitor.
            variables: List of variable names to track.
            interval: Refresh rate in milliseconds.
            title: Chart title.
            labels: Custom labels for the variables.
            max_points: Maximum number of data points to keep in history.
        """
        super().__init__(
            plc_instance=plc_instance, pou_name=pou_name, variables=variables,
            interval=interval, title=title, labels=labels)
        self._max_points: int = max_points
        self._history: Dict[str, Dict[str, List[Any]]] = {
            var: {'times': [], 'values': []} for var in self._variables}
        self._lines: Dict[str, Any] = {}

        self.ax.set_facecolor('#1a1a1a')
        self.fig.set_facecolor('#0d0d0d')

    def _render_plot(self, data: Dict[str, float]) -> None:
        """Renders lines based on historical values."""
        now: datetime = datetime.now()

        for var_name, value in data.items():
            if var_name not in self._history:
                self._history[var_name] = {'times': [], 'values': []}

            self._history[var_name]['times'].append(now)
            self._history[var_name]['values'].append(value)
            
            times: List[datetime] = self._history[
                var_name]['times'][-self._max_points:]
            values: List[float] = self._history[
                var_name]['values'][-self._max_points:]

            if var_name not in self._lines:
                self._lines[var_name] = {'times': times, 'values': values}
            else:
                self._lines[var_name]['times'] = times
                self._lines[var_name]['values'] = values

        self.ax.clear()
        self.ax.set_facecolor('#1a1a1a')

        colors = plt.cm.tab10(range(len(self._variables)))
        for idx, (var_name, label) in enumerate(
            zip(self._variables, self._chart_labels)):
            if var_name in self._lines and self._lines[var_name]['values']:
                self.ax.plot(
                    self._lines[var_name]['times'],
                    self._lines[var_name]['values'],
                    label=label,
                    linewidth=1.5,
                    color=colors[idx])        

        self.ax.relim()
        self.ax.autoscale_view()

        total_sum: float = sum(
            h['values'][-1] for h in self._history.values() if h['values'])
        formatted_total: Union[int, float] = total_sum if total_sum % 1 != 0 \
            else int(total_sum)

        self.ax.set_title(f"{self._chart_title} (Total: {formatted_total})",
                          color='white', pad=15)
        self.ax.grid(True, linestyle='--', alpha=0.2, color='white')
        self.ax.tick_params(colors='white', labelsize=8)
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        self.fig.autofmt_xdate()        
        self.fig.subplots_adjust(top=0.9, bottom=0.2, left=0.1, right=0.95)

        if self.ax.get_legend():
            self.ax.get_legend().remove()

        self.ax.legend(
            loc='upper left',
            bbox_to_anchor=(0.01, 0.99),
            fontsize=8,
            frameon=True,
            facecolor='#262626',
            edgecolor='white',
            labelcolor='white')

        self.fig.canvas.draw_idle()


class ChartPieVisualizerPLC(BaseChartVisualizerPLC):
    """Visualizes PLC data using a Pie Chart (Max 3 variables)."""
    def __init__(
            self, plc_instance: OPCUAClient, pou_name: str, variables: List[str],
            interval: int = 500, title: Optional[str] = None, 
            labels: Optional[List[str]] = None) -> None:
        """Initialize Pie Chart visualizer."""
        super().__init__(
            plc_instance=plc_instance, pou_name=pou_name,
            variables=variables, interval=interval,
            title=title, labels=labels)
        if len(self._variables) > 3:
            raise ValueError("Pie charts accept a maximum of 3 variables.")
        self._colors: Any = plt.cm.Pastel1.colors

    def _render_plot(self, data: Dict[str, float]) -> None:
        """Renders the pie plot."""
        self.ax.clear()
        values: List[float] = list(data.values())
        total: float = sum(values)

        if total <= 0:
            self.ax.text(0.5, 0.5, "Waiting for data (Sum = 0)...", 
                         ha='center', va='center', transform=self.ax.transAxes)
            return

        def make_autopct(values: List[float]) -> Callable[[float], str]:
            def my_autopct(pct: float) -> str:
                absolute: int = int(round(pct * sum(values) / 100.0))
                return f'{absolute}\n({pct:.1f}%)'
            return my_autopct

        self.ax.pie(values, labels=self._chart_labels,
            autopct=make_autopct(values), startangle=140,
            colors=self._colors, wedgeprops={
                'edgecolor': 'black', 'linewidth': 1, 'antialiased': True})
        
        self.ax.set_title(
            f"{self._chart_title}\nTotal: {int(total)}", color='black', pad=20)        
        self.fig.tight_layout()


class PLCDashboard(BaseChartVisualizerPLC):
    """
    Advanced Dashboard capable of displaying multiple chart types in a single 
    grid.
    """

    def __init__(self, plc_instance: Any, configs: List[Dict[str, Any]], interval: int = 500) -> None:
        """
        Args:
            plc_instance (Any): OPCUA Client instance.
            configs (List[Dict]): List of configuration dictionaries for each 
                subplot.
        """
        super().__init__(plc_instance, "", [], interval)
        self._configs: List[Dict[str, Any]] = configs
        self._history: Dict[str, Dict[str, List[Any]]] = {}
        self._max_points: int = 50
        
        plt.close(self.fig)
        self.fig = plt.figure(figsize=(14, 10), facecolor='#1e1e1e')
        self._status_ax: plt.Axes
        self._setup_layout()

    def _setup_layout(self) -> None:
        """Configures the 2x2 grid layout for the dashboard."""
        cols: int = 2
        rows: int = 2

        for i in range(4):
            ax: plt.Axes = self.fig.add_subplot(rows, cols, i + 1)
            ax.set_facecolor('#2e2e2e')
            
            if i < len(self._configs):
                cfg = self._configs[i]
                cfg['ax'] = ax
                
                if cfg['type'] == 'line':
                    for var in cfg['vars']:
                        self._history[f"{cfg['pou']}.{var}"] = {
                            'times': [], 'values': []}
                
                self._map_nodes_for_config(cfg)
            else:
                self._status_ax = ax

    def _map_nodes_for_config(self, cfg: Dict[str, Any]) -> None:
        """Maps nodes specifically for a dashboard subplot configuration."""
        cfg['nodes'] = {}
        for var in cfg['vars']:
            path: Optional[str] = self._plc_instance.get_variable_path(
                cfg['pou'], var)
            
            if path:
                try:
                    node: Any = self._plc_instance.get_node(path)
                    cfg['nodes'][var] = node
                except Exception as e:
                    self._logger.error(f"Node error {var}: {e}")

    def _update_chart(self, _: Any) -> None:
        """Main update loop for the entire dashboard grid."""
        if not self._plc_instance._is_connected():
            self._render_error_state("PLC Disconnected")
            return
        
        now: datetime = datetime.now()
        total_geral: float = 0.0

        for cfg in self._configs:
            ax: plt.Axes = cfg['ax']
            data: Dict[str, float] = {}
            
            for var_name, node_obj in cfg['nodes'].items():
                try:
                    val: Any = node_obj.get_value()
                    val_float: float = float(val) if val is not None else 0.0
                    data[var_name] = val_float
                    if cfg['type'] == 'pie':
                        total_geral += val_float
                except:
                    data[var_name] = 0.0

            ax.clear()
            ax.set_facecolor('#2e2e2e')

            title: str = cfg.get('title', 'Monitoring')
            ax.set_title(
                title, color='white', fontsize=10, pad=15, fontweight='bold')

            custom_labels: List[str] = cfg.get('labels', list(data.keys()))

            if cfg['type'] == 'line':
                self._draw_line(ax, cfg, data, custom_labels, now)
            elif cfg['type'] == 'bar':
                self._draw_bar(ax, data, custom_labels)
            elif cfg['type'] == 'pie':
                self._draw_pie(ax, data, custom_labels)
        
        if hasattr(self, '_status_ax'):
            self._draw_status_panel(self._status_ax, total_geral)
        
        self.fig.tight_layout(pad=3.5)
    
    def _draw_status_panel(self, ax: plt.Axes, total_pecas: float) -> None:
        """Renders a text-based status panel with KPIs."""
        ax.clear()
        ax.set_facecolor('#1e1e1e')
        ax.axis('off')
        
        ax.text(0.5, 0.85, "INDICADORES GERAIS", transform=ax.transAxes, 
                ha='center', color='white', fontsize=12, fontweight='bold')

        total_str: str = f"{int(total_pecas)}"
        ax.text(0.5, 0.5, total_str, transform=ax.transAxes, 
                ha='center', va='center', color='#00FF7F', 
                fontsize=55, fontweight='bold')
        
        ax.text(0.5, 0.32, "Total de Peças Produzidas", transform=ax.transAxes, 
                ha='center', color='gray', fontsize=10)

        plc_connected: bool = self._plc_instance._is_connected()
        status_color: str = '#00FF00' if plc_connected else '#FF0000'
        ax.text(
            0.5,
            0.05,
            "● SISTEMA OPERACIONAL" if plc_connected else "● SISTEMA DESLIGADO",
            transform=ax.transAxes, 
            ha='center',
            color=status_color,
            fontsize=9,
            fontweight='bold')

    def _draw_line(
        self, ax: plt.Axes, cfg: Dict[str, Any], data: Dict[str, float], 
        custom_labels: List[str], now: datetime) -> None:
        """Internal helper for drawing line subplots."""
        colors = plt.cm.tab10(range(len(data)))
        
        for idx, ((var, val), label) in enumerate(
            zip(data.items(), custom_labels)):
            key: str = f"{cfg['pou']}.{var}"
            
            if key not in self._history:
                self._history[key] = {'times': [], 'values': []}
            
            self._history[key]['times'].append(now)
            self._history[key]['values'].append(val)
            
            self._history[key]['times'] = self._history[
                key]['times'][-self._max_points:]
            self._history[key]['values'] = self._history[
                key]['values'][-self._max_points:]
            
            ax.plot(
                self._history[key]['times'], self._history[key]['values'], 
                label=label, linewidth=1.5, color=colors[idx])
        
        if len(data) > 0:
            ax.legend(
                loc='upper left', fontsize=7, frameon=True,
                facecolor='#1e1e1e', edgecolor='white', labelcolor='white')
        
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        ax.tick_params(axis='x', colors='white', labelsize=7, rotation=45)
        ax.tick_params(axis='y', colors='white', labelsize=7)
        ax.grid(True, alpha=0.1, color='white')

    def _draw_bar(self, ax: plt.Axes, data: Dict[str, float],
                  custom_labels: List[str]) -> None:
        """Internal helper for drawing bar subplots."""
        values: List[float] = list(data.values())
        colors: Any = plt.cm.tab10(range(len(values)))
        bars = ax.bar(custom_labels, values, color=colors)
        
        formatted: List[str] = [
            f"{v:.2f}" if v % 1 != 0 else f"{int(v)}" for v in values]
        ax.bar_label(bars, labels=formatted, color='white',
                     padding=3, fontsize=10)
        
        ax.tick_params(axis='x', colors='white', labelsize=9)
        ax.tick_params(axis='y', colors='white', labelsize=9)
        if values and max(values) > 0:
            ax.set_ylim(0, max(values) * 1.3)

    def _draw_pie(self, ax: plt.Axes, data: Dict[str, float],
                  custom_labels: List[str]) -> None:
        """Internal helper for drawing pie subplots."""
        values: List[float] = list(data.values())
        total: float = sum(values)
        
        if total > 0:
            def label_format(pct: float) -> str:
                absolute = int(round(pct * total / 100.0))
                val_str = f"{absolute:.2f}" if absolute % 1 != 0 \
                    else f"{int(absolute)}"
                return f'{val_str}\n({pct:.1f}%)'

            ax.pie(values, labels=custom_labels, autopct=label_format, 
                   textprops={'color':"w", 'fontsize': 12},
                   colors=plt.cm.Pastel1.colors)
        else:
            ax.text(0.5, 0.5, "No data", color='white', ha='center')

    def start(self) -> None:
        """Starts the dashboard animation loop."""
        self.ani = animation.FuncAnimation(
            self.fig, self._update_chart,
            interval=self._interval, cache_frame_data=False)
        plt.show()