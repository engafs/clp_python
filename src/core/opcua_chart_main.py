import yaml
from pathlib import Path
from typing import Dict, Any, Union, List, Type
from logging import Logger
from src.core.opcua import OPCUAClient
from src.utils.logging_config import setup_logging 
from src.core.opcua_chart import (
    ChartBarsVisualizerPLC, ChartLineVisualizerPLC, 
    ChartPieVisualizerPLC, PLCDashboard, BaseChartVisualizerPLC)

class VisualizerFactory:
    """
    Factory class to instantiate different types of PLC visualizers.
    """

    # Mapping chart types to their respective classes
    _MAP: Dict[str, Type[Union[BaseChartVisualizerPLC, PLCDashboard]]] = {
        "bar": ChartBarsVisualizerPLC,
        "line": ChartLineVisualizerPLC,
        "pie": ChartPieVisualizerPLC,
        "dashboard": PLCDashboard
    }

    @classmethod
    def create(
        cls, chart_cfg: Union[str, Dict[str, Any]],
        plc: OPCUAClient, 
        pou: str,
        interval: int = 500) -> Union[BaseChartVisualizerPLC, PLCDashboard]:
        """
        Creates and returns a visualizer instance based on the configuration.

        Args:
            chart_cfg (Union[str, Dict]): Either a string representing the 
                chart type or a dictionary with full configuration.
            plc (OPCUAClient): An active instance of the OPC UA client.
            pou (str): Default POU name to use if not specified in config.
            interval (int): Refresh interval in milliseconds. Defaults to 500.

        Returns:
            Union[BaseChartVisualizerPLC, PLCDashboard]: An instance of the 
                requested visualizer.

        Raises:
            ValueError: If the requested chart type is not supported.
        """
        if isinstance(chart_cfg, str):
            chart_type: str = chart_cfg.lower()
            cfg_dict: Dict[str, Any] = {}
        else:
            chart_type = chart_cfg.get('type', '').lower()
            cfg_dict = chart_cfg

        chart_class = cls._MAP.get(chart_type)
        
        if not chart_class:
            raise ValueError(f"Chart type '{chart_type}' is not supported.")
        
        common_params = {
            'plc_instance': plc,
            'pou_name': cfg_dict.get('pou', pou),
            'variables': cfg_dict.get('vars', []),
            'interval': interval,
            'title': cfg_dict.get('title'),
            'labels': cfg_dict.get('labels')
        }

        if chart_type == 'line':
            common_params['max_points'] = cfg_dict.get('max_points', 100)
            
        return chart_class(**common_params)

def main(yaml_filename: str) -> None:
    """
    Main entry point for the PLC Monitoring System.
    Loads YAML config, connects to PLC, and launches the visualizer.

    Args:
        yaml_filename (str): Name of the configuration file located in /config.
    """
    # Path resolution
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    CONFIG_PATH: Path = BASE_DIR / "config" / yaml_filename

    # Logger initialization
    logger: Logger = setup_logging(
        file_name="chart_log.txt", logger_name="CHART_LOG", debug_mode=False)
    
    logger.info("--- 🚀 System started ---")

    # Loading YAML configuration
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config: Dict[str, Any] = yaml.safe_load(f)
        logger.info(f"✅ Configuration loaded: {CONFIG_PATH.name}")
    except Exception as e:
        logger.error(f"❌ Failed to load YAML file at {CONFIG_PATH}: {e}")
        return

    # PLC Connection Setup
    plc_cfg: Dict[str, Any] = config['plc_connection']
    plc: OPCUAClient = OPCUAClient(
        plc_ip=plc_cfg['ip'], plc_name=plc_cfg['name'])
    
    if not plc.connect():
        logger.error("❌ Failed to connect to PLC.")
        return

    layout: List[Dict[str, Any]] = config.get('dashboard_layout', [])

    try:
        if len(layout) > 1:
            logger.info("📊 Dashboard mode detected. (Multiple Charts)")

            # Ensure all items have a default POU name
            for item in layout:
                item.setdefault('pou', plc_cfg['pou_name'])
            
            app: PLCDashboard = PLCDashboard(plc, layout, interval=500)
            app.start()
        
        elif len(layout) == 1:
            cfg: Dict[str, Any] = layout[0]
            logger.info(f"📈 Individual mode detected: {cfg['type'].upper()}")
            
            # Using the factory to create the specific visualizer
            individual_app: Any = VisualizerFactory.create(
                chart_cfg=cfg, plc=plc,
                pou=plc_cfg['pou_name'], interval=500)
            
            individual_app.start_monitoring()
        else:
            logger.warning("⚠️ No layout found in YAML.")
            
    except Exception as e:
        logger.error(f"💥 Critical execution error: {e}")
    finally:
        plc.disconnect()
        logger.info("🔌 PLC connection closed.")

if __name__ == "__main__":
    main('config_scene_11.yaml')