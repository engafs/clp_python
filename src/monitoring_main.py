import os
import sys
import io
import yaml
import asyncio
import threading
import shutil    
import time
import subprocess
import platform
import psutil
import logging
from datetime import datetime
from collections import deque
from pathlib import Path
from typing import Dict, Any, List, Union, Optional, Callable, Tuple, Deque, Set
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from src.utils.logging_config import setup_logging
from src.core.opcua import OPCUAClient
from src.services.mqtt import MQTTManager
from src.services.email_service import EmailService
from src.services.telegram_bot_service import TelegramBotService
from src.services.chart_bot_service import ChartGeneratorService
from src.utils.formatters import MonitoringFormatter
from src.core.rules import RuleEngine
from src.utils.io_utils import ConfigLoader


class IndustrialMonitoringSystem:
    """
    Core orchestrator for the Industrial Monitoring System.
    
    This class manages the lifecycle of PLC communications, MQTT data publishing,
    Telegram bot interactions, and automated email alerting. It acts as the 
    central hub for rule evaluation and state management.
    """

    def __init__(self, config_filename: str):
        """
        Initializes the monitoring system by loading configurations and 
        starting services.
        
        Args:
            config_filename: The name of the YAML file inside the /config directory.
        """
        self._setup_paths_and_config(config_filename)
        
        self.formatter: MonitoringFormatter = MonitoringFormatter()
        self.rules: RuleEngine = RuleEngine()
        self.plc_lock: threading.RLock = threading.RLock()

        self._setup_loggers()
        self._logger_plc.info("Industrial Monitoring System starting...")

        self._init_services()
        self._init_internal_state()
    
    def _setup_paths_and_config(self, config_filename: str) -> None:
        """
        Defines system directories and loads the YAML configuration.
        """
        self.BASE_DIR: Path = Path(__file__).resolve().parent.parent
        self.CONFIG_DIR: Path = self.BASE_DIR / "config"
        self.LOGS_DIR: Path = self.BASE_DIR / "logs"

        self._config_filename: str = config_filename        
        full_path: Path = self.CONFIG_DIR / self._config_filename

        try:
            self._config: Dict[str, Any] = ConfigLoader._load_yaml(full_path)
            print(f"✅ Configuration loaded: {full_path}")
        except Exception as e:
            logging.error(
                f"❌ CRITICAL: Failed to load config at {full_path}: {e}")
            sys.exit(1)
    
    def _setup_loggers(self) -> None:
        """
        Initializes dedicated loggers for each service domain.
        """
        self._logger_plc: logging.Logger = setup_logging(
            "plc_log.txt", logger_name="PLC_LOG")
        self._logger_mqtt: logging.Logger = setup_logging(
            "mqtt_log.txt", logger_name="MQTT_LOG")
        self._logger_telegram: logging.Logger = setup_logging(
            "telegram_log.txt", logger_name="TELEGRAM_LOG")
        self._logger_email: logging.Logger = setup_logging(
            "email_log.txt", logger_name="EMAIL_LOG")
    
    def _init_internal_state(self) -> None:
        """
        Initializes caches, monitoring tasks, and system uptime trackers.
        """
        self._logger_plc.info("Initializing internal state and caches...")

        self._last_mqtt_heartbeat: float = time.time()
        self._start_time: float = time.time()
        self._watchdog_interval: int = 30
        self._max_offline_time: int = 120

        self._active_alerts: set = set()
        self.maintenance_active: bool = False
        self._user_states: Dict[str, Any] = {}
        self._current_values_cache: Dict[str, Any] = {}

        monitoring_list: List[Dict[str, Any]] = self._config.get(
            'monitoring_list', [])
        self.sensor_mapping: Dict[str, str] = {
            item['topic']: item.get('description', 'Sensor') 
            for item in monitoring_list if isinstance(item, dict)}
        
        self._history_cache: Dict[str, deque] = {var['topic']: deque(maxlen=100) 
            for var in self._config['monitoring_list']}
        
        self._active_monitoring_tasks: Dict[str, asyncio.Task] = {}
        self._logger_plc.debug(
            f"Mapped {len(self.sensor_mapping)} sensors to monitoring list.")
    
    def _init_services(self) -> None:
        """
        Instantiates external communication services based on configuration.
        """
        try:
            self._logger_plc.info("Connecting to external services...")

            self.plc: OPCUAClient = OPCUAClient(
                plc_ip=self._config['plc_connection']['ip'], 
                plc_name=self._config['plc_connection']['name'])
            
            # MQTT Publisher
            self.mqtt: MQTTManager = MQTTManager(
                broker_address=self._config['mqtt_connection']['broker'],
                port=self._config['mqtt_connection']['port'])
            
            # Telegram Bot
            self.telegram_service: TelegramBotService = TelegramBotService(
                self._config['telegram_connection']['bot_token'],
                self._config['telegram_connection']['bot_chat_id'],
                self._config['telegram_connection']['admin_ids'])
            
            # Email Alerting
            email_cfg = self._config.get('email', {})
            self.email_service: EmailService = self._setup_email_service(
                email_cfg.get('sender', ''), 
                email_cfg.get('password', ''))
            
            self._logger_plc.info("All services instantiated successfully.")
            
        except KeyError as e:
            self._logger_plc.error(f"Missing mandatory configuration key: {e}")
            raise
        except Exception as e:
            self._logger_plc.error(
                f"Unexpected error during service initialization: {e}")
            raise
    
    def _save_config_to_yaml(self) -> bool:
        """
        Synchronizes internal mappings and persists the current configuration to disk.

        Updates the in-memory 'sensor_mapping' from the 'monitoring_list' and 
        delegates the file writing operation to the ConfigLoader utility.

        Returns:
            bool: True if the file was saved successfully, False otherwise.
        """
        full_path: Path = self.CONFIG_DIR / self._config_filename

        try:
            # Sync sensor mapping to ensure memory reflects config changes
            self.sensor_mapping: Dict[str, str] = {
                item['topic']: item['description'] 
                for item in self._config.get('monitoring_list', []) 
                if isinstance(item, dict)}

            success: bool = ConfigLoader._save_yaml(full_path, self._config)

            if success:
                self._logger_telegram.info(
                    f"✅ Settings synchronized and saved to: {full_path}")
            else:
                self._logger_telegram.error(
                    f"❌ ConfigLoader failed to persist changes to: {full_path}")
            
            return success

        except Exception as e:
            self._logger_telegram.error(
                f"❌ Critical error during YAML synchronization: {e}")
            return False

    def _setup_email_service(self, sender: str, password: str) -> EmailService:
        """
        Initializes and authenticates the EmailService using system credentials.

        Args:
            sender (str): The email address used to send notifications.
            password (str): The authentication password or app-specific token.

        Returns:
            EmailService: An authenticated and configured instance of the 
            email service.

        Raises:
            RuntimeError: If the EmailService fails to initialize or set 
            credentials.
        """
        try:
            service: EmailService = EmailService(sender)
            service.set_credentials(password)
            
            self._logger_email.info(
                f"📧 EmailService initialized for sender: {sender}")
            return service
            
        except Exception as e:
            self._logger_email.error(
                f"❌ Failed to initialize EmailService: {e}")
            raise RuntimeError(
                f"O serviço de inicialização de email falhou: {e}")
    
    def _show_plc_info(self) -> Dict[str, str]:
        """
        Requests and returns the current PLC connection metadata.

        Acts as a high-level bridge to the OPCUAClient, ensuring hardware 
        details are retrieved and logged.

        Returns:
            Dict[str, str]: Dictionary containing 'name', 'ip', and 'port'.
        
        Raises:
            ConnectionError: If the PLC service is unavailable or disconnected.
        """
        try:
            plc_data: Dict[str, str] = self.plc._get_info()
            
            self._logger_plc.info(
                f"✅ PLC Data retrieved: {plc_data.get('name')} "
                f"({plc_data.get('ip')})")
            
            return plc_data

        except ConnectionError as e:
            self._logger_plc.error(
                f"❌ Cannot retrieve PLC info - Connection Lost: {e}")
            raise
        except Exception as e:
            self._logger_plc.info(
                f"⚠️ Unexpected error while fetching PLC metadata: {e}")
            raise

    def _format_alert_texts(self, topic: str, value: float, threshold: float, 
                            op: str, label: Optional[str]) -> Dict[str, str]:
        """
        Orchestrates the generation of alert messages for all communication 
        channels. It attempts to fetch the latest PLC metadata to include in 
        the alerts. If the PLC is offline, it falls back to a safe "Offline"
        status.

        Args:
            topic (str): The MQTT topic of the sensor that triggered the alert.
            value (float): The current reading that caused the alert to trigger.
            threshold (float): The configured limit that was breached.
            op (str): The comparison operator used in the rule (e.g., '>').
            label (Optional[str]): A human-readable name for the sensor, 
            if available.

        Returns:
            Dict[str, str]: A dictionary containing 'email' body, 'telegram' 
            body, and 'subject'.
        """
        try:
            plc_info: Dict[str, str] = self._show_plc_info()
        except ConnectionError:
            plc_info: Dict[str, str] = {"name": "Offline", "ip": "N/A"}
            self._logger_plc.warning(
                f"⚠️ Alert generated while PLC was unreachable for topic: {topic}")

        telegram_msg: str = self.formatter._build_telegram_alert(
            topic, value, threshold, op, label, plc_info)
        email_msg: str = self.formatter._build_email_alert(
            topic, value, threshold, op, label, plc_info)
        
        alert_name: str = label if label else topic

        return {
            "email": email_msg, 
            "telegram": telegram_msg, 
            "subject": f"⚠️ ALERTA: {alert_name} fora dos limites"}

    def _execute_alert_notifications(
            self, topic: str, value: float, threshold: float,
            op: str, channels: List[str], email_list: Optional[List[str]] = None,
            label: Optional[str] = None) -> None:
        """
        Dispatches alert notifications through the specified communication channels.

        Args:
            topic (str): MQTT topic of the sensor.
            value (float): Current reading that triggered the alert.
            threshold (float): The set limit value.
            op (str): Comparison operator (e.g., '>').
            channels (List[str]): List of delivery methods ('email', 'telegram').
            email_list (Optional[List[str]]): Optional list of email recipients.
            label (Optional[str]): Human-readable name for the sensor.
        """
        alerts: Dict[str, str] = self._format_alert_texts(
            topic, value, threshold, op, label)

        if "email" in channels and email_list:
            self._dispatch_emails(
                email_list, alerts["subject"], alerts["email"])

        if "telegram" in channels:
            self._dispatch_telegram(alerts["telegram"], topic)

    def _dispatch_emails(
            self, recipients: List[str], subject: str, body: str) -> None:
        """
        Handles batch email delivery with individual error tracking.
        Iterates through the list of recipients and attempts to send the email 
        to each one. Logs successes and failures separately to provide clear 
        visibility into which notifications were sent and which encountered 
        issues. This method ensures that a failure to send to one recipient 
        does not prevent attempts to notify others in the list.

        Args:
            recipients (List[str]): List of email addresses to send the alert to.
            subject (str): The subject line for the email.
            body (str): The main content of the email message.
        """
        for recipient in recipients:
            try:
                self.email_service.send_email(recipient, subject, body)
                self._logger_email.info(
                    f"📧 Alert email successfully sent to: {recipient}")
            except Exception as e:
                self._logger_email.error(
                    f"❌ Critical failure sending email to {recipient}: {e}")

    def _dispatch_telegram(self, message: str, topic: str) -> None:
        """
        Handles Telegram notification delivery with logging. Attempts to send 
        the message and logs the outcome. If the Telegram service encounters 
        an error during sending, it logs the failure with details of the 
        exception. This method ensures that issues with Telegram notifications 
        are captured without affecting the overall flow of the monitoring system.

        Args:
            message (str): The formatted message to be sent via Telegram.
            topic (str): The MQTT topic associated with the alert, used for 
            logging context.
        """
        try:
            self.telegram_service._send_notifications(message)
            self._logger_telegram.info(
                f"📱 Telegram alert dispatched for topic: {topic}")
        except Exception as e:
            self._logger_telegram.error(
                f"❌ Failed to send Telegram notification: {e}")
    
    def _format_normalization_texts(
            self, topic: str, value: Any, threshold: Any, 
            op: str, label: Optional[str]) -> Dict[str, str]:
        """
        Generates formatted messages for system normalization (recovery) events.
        Fetches current PLC metadata to contextualize the recovery message. 
        Falls back to 'Offline' status if the PLC connection is lost.

        Args:
            topic (str): The MQTT topic of the sensor that normalized.
            value (Any): The current reading that returned to normal.
            threshold (Any): The configured limit that was previously breached.
            op (str): The comparison operator used in the rule (e.g., '>').
            label (Optional[str]): A human-readable name for the sensor, if 
            available.

        Returns:
            Dict[str, str]: Dictionary with 'telegram', 'email', and 'subject' keys.
        """
        try:
            plc_info: Dict[str, str] = self._show_plc_info()
        except Exception:
            plc_info = {"name": "Offline", "ip": "N/A"}
            self._logger_plc.warning(
                f"⚠️ Normalization text generated without PLC info for topic: {topic}")

        telegram_body: str = self.formatter._build_telegram_normalization(
            topic, value, threshold, op, label, plc_info)
        
        email_body: str = self.formatter._build_email_normalization(
            topic, value, threshold, op, label, plc_info)

        return {
            "telegram": telegram_body,
            "email": email_body,
            "subject": f"✅ NORMALIZAÇÃO: {label if label else topic}"}

    def _execute_normalization_notifications(
            self, topic: str, value: Any, threshold: Any, op: str,
            channels: List[str], emails: List[str], label: str) -> None:
        """
        Orchestrates and dispatches normalization notifications via Telegram 
        and Email. Generates context-rich messages that include the current 
        PLC status and sensor details. Ensures that users are informed when a 
        previously triggered alert condition has returned to normal, providing 
        reassurance and closure on the incident.

        Args:
            topic (str): MQTT sensor topic.
            value (Any): Current reading.
            threshold (Any): Limit configured.
            op (str): Comparison operator used.
            channels (List[str]): Enabled notification channels.
            emails (List[str]): List of email recipients.
            label (str): Friendly name for the sensor.
        """
        texts: Dict[str, str] = self._format_normalization_texts(
            topic, value, threshold, op, label)

        if "telegram" in channels:
            self._dispatch_telegram(texts["telegram"], topic)
            self._logger_telegram.info(f"✅ Normalization status sent: {label}")

        if "email" in channels and emails:
            self._dispatch_emails(emails, texts["subject"], texts["email"])
    
    def _get_clean_value_from_mqtt_publisher(self, payload: str) -> Any:
        """
        Bridge to parse MQTT payloads into Python types using ConfigLoader utility.

        Args:
            payload (str): The raw string payload received from MQTT.

        Returns:
            Any: The parsed value, which could be a number, boolean, or string, 
            depending on the content of the payload.
        """
        return ConfigLoader._get_clean_value(payload)

    def _alert_callback_handler(
            self, topic: str, payload: str, threshold: Any,
            op_sign: str, notify_via: List[str], email_list: List[str],
            label: str, action_cfg: Optional[Dict[str, Any]] = None, 
            trigger_topic: Optional[str] = None) -> None:
        """
        Evaluates incoming MQTT messages against rules and triggers alerts or 
        normalizations. This is the main entry point for data processing. It 
        validates the operator, compares the value, and delegates to the 
        appropriate trigger/normalization handler.

        Args:
            topic (str): MQTT topic of the sensor.
            payload (str): Raw payload from MQTT.
            threshold (Any): Configured limit for triggering alerts.
            op_sign (str): Comparison operator as a string (e.g., '>', '<').
            notify_via (List[str]): Channels to send notifications through.
            email_list (List[str]): List of email recipients for alerts.
            label (str): Friendly name for the sensor.
            action_cfg (Optional[Dict[str, Any]]): Configuration for automated 
            PLC actions.
            trigger_topic (Optional[str]): The topic that triggered the 
            action, used for context in messages.
        """
        try:
            current_value: Any = self._get_clean_value_from_mqtt_publisher(payload)
            
            operation_func: Callable[[Any, Any], bool] = self.rules.get_operation(op_sign)

            if not operation_func:
                self._logger_mqtt.warning(
                    f"⚠️ Unsupported operator: {op_sign}. Aborting callback.")
                return

            alert_id: str = f"{topic}_{op_sign}_{threshold}"
            
            is_condition_met: bool = operation_func(current_value, threshold)

            if is_condition_met:
                self._handle_alert_trigger(
                    alert_id, topic, current_value, threshold, 
                    op_sign, notify_via, email_list, label, 
                    action_cfg, trigger_topic)
            else:
                self._handle_alert_normalization(
                    alert_id, topic, current_value, threshold, 
                    op_sign, notify_via, email_list, label)

        except Exception as e:
            self._logger_mqtt.error(
                f"❌ Critical Error in Callback Handler for {topic}: {e}")

    def _handle_alert_trigger(
            self, alert_id: str, topic: str,
            current_value: Any, threshold: Any,
            op_sign: str, notify_via: List[str],
            email_list: List[str], label: str,
            action_cfg: Optional[Dict[str, Any]] = None, 
            trigger_topic: Optional[str] = None) -> None:
        """
        Handles the activation logic when a sensor value breaches a threshold.

        It prevents duplicate notifications by checking the '_active_alerts' set.
        If maintenance mode is inactive, it dispatches notifications and executes
        any configured automated PLC actions.

        Args:
            alert_id (str): Unique string identifier for the specific alert rule.
            topic (str): MQTT topic that triggered the alert.
            current_value (Any): The reading that caused the breach.
            threshold (Any): The configured limit.
            op_sign (str): The operator used (e.g., ">=").
            notify_via (List[str]): List of communication channels.
            email_list (List[str]): List of recipients for email alerts.
            label (str): Friendly name of the sensor.
            action_cfg (Optional[Dict[str, Any]]): Configuration for automated PLC responses.
            trigger_topic (Optional[str]): The origin topic or label for action logging.
        """
        if alert_id in self._active_alerts:
            return

        if not self.maintenance_active:
            self._execute_alert_notifications(
                topic, current_value, threshold, op_sign,
                notify_via, email_list, label=label)
        else:
            self._logger_mqtt.info(
                f"🔕 Alert suppressed (Maintenance Active): {label}")

        if action_cfg:
            origin: str = trigger_topic if trigger_topic else label
            self._execute_auto_action(action_cfg, trigger_topic=origin)

        self._active_alerts.add(alert_id)
        self._logger_mqtt.info(
            f"🚨 ALERT ACTIVATED: {label} (Value: {current_value})")

    def _handle_alert_normalization(
            self, alert_id: str, topic: str,
            current_value: Any, threshold: Any, 
            op_sign: str, notify_via: List[str],
            email_list: List[str], label: str) -> None:
        """
        Handles recovery logic when a sensor returns to its safe operational state.

        Clears the alert from '_active_alerts' and sends a recovery notification
        to inform users that the issue is resolved.

        Args:
            alert_id (str): Unique string identifier for the specific alert rule.
            topic (str): MQTT topic that triggered the normalization.
            current_value (Any): The reading that returned to normal.
            threshold (Any): The configured limit.
            op_sign (str): The operator used (e.g., ">=").
            notify_via (List[str]): List of communication channels.
            email_list (List[str]): List of recipients for email notifications.
            label (str): Friendly name of the sensor.
        """
        if alert_id not in self._active_alerts:
            return

        if not self.maintenance_active:
            self._execute_normalization_notifications(
                topic, current_value, threshold, op_sign,
                notify_via, email_list, label=label)
        else:
            self._logger_mqtt.info(
                f"⚪ Normalization suppressed (Maintenance): {label}")

        self._active_alerts.remove(alert_id)        
        self._logger_mqtt.info(
            f"✅ STATUS NORMALIZED: {label} (Current Value: {current_value})")
    
    def _format_auto_action_message(
            self, action_cfg: Dict[str, Any], trigger_topic: str) -> str:
        """
        Formats the notification message for an automated PLC action.
        Delegates the construction of the message to the MonitoringFormatter,
        providing the action configuration and the context of the trigger.

        Args:
            action_cfg (Dict[str, Any]): The configuration dictionary for the 
                automated action.
            trigger_topic (str): The topic or label that caused the action to 
                execute.

        Returns:
            A formatted string message describing the automated action taken.
        """
        return self.formatter._build_auto_action_message(
            action_cfg, trigger_topic)
    
    def _execute_auto_action(
            self, action_cfg: Dict[str, Any],
            trigger_topic: str = "Tópico não informado") -> None:
        """
        Executes an automated intervention on the PLC based on a triggered rule.
        Uses a threading lock to ensure atomic operations on the PLC. Supports 
        both 'pulse' (momentary trigger) and 'bool' (static state) actions.

        Args:
            action_cfg (Dict[str, Any]): Configuration dict specifying the 
                action details.
            trigger_topic (str): The origin topic or label for logging and 
                message context.
        """
        var_name: Optional[str] = action_cfg.get('variable')
        action_type: str = action_cfg.get('type', 'bool')
        pou_name: str = action_cfg.get('pou', 'PLC_PRG') 

        try:
            if action_type == 'pulse':
                success = self._send_plc_pulse(var_name, pou_name)
                if not success:
                    raise ConnectionError(
                        f"Physical failure firing pulse at {var_name}")
            else:
                with self.plc_lock:
                    target_value = action_cfg.get('value', False)
                    self.plc._set_value(pou_name, var_name, target_value)

            msg: str = self._format_auto_action_message(
                action_cfg, trigger_topic)
            self.telegram_service._send_notifications(msg)
            
            self._logger_plc.info(
                f"🤖 AUTO-ACTION SUCCESS: {pou_name}.{var_name} "
                f"triggered by {trigger_topic}")

        except Exception as e:
            error_msg: str = ("❌ *FALHA EM AÇÃO AUTOMÁTICA*\n⚠️ Tag: "
                              f"`{var_name}`\n📝 Erro: {e}")
            
            self._logger_plc.error(
                f"❌ Error in auto action ({var_name}): {e}")
            self.telegram_service._send_notifications(error_msg)
    
    def _categorize_cached_values(self) -> Dict[str, List[str]]:
        """
        Groups current cached sensor values by their originating PLC POU.
        Delegates the grouping logic to the MonitoringFormatter utility, which 
        organizes the data into a structured format for reporting.

        Returns:
            Dict[str, List[str]]: A dictionary where keys are POU names and 
                values are lists of formatted sensor readings.
        """
        return self.formatter._group_by_pou(self._current_values_cache)

    def _format_status_report(self) -> Optional[str]:
        """
        Orchestrates the data collection and delegates the final report 
        formatting. Handles the edge case of an empty cache by returning 
        None, which signals the caller to send a waiting message instead. If 
        data is available, it attempts to fetch PLC metadata and organizes the 
        cached sensor values before passing everything to the formatter for 
        the final report construction.

        Returns:
            Optional[str]: The formatted status report ready for Telegram, or 
            None if the cache is empty.
        """
        if not self._current_values_cache:
            return None

        try:
            plc_info: Dict[str, str] = self._show_plc_info()
        except ConnectionError:
            plc_info: Dict[str, str] = {"name": "Offline", "ip": "N/A"}

        grouped_vars: Dict[str, List[str]] = self._categorize_cached_values()
        return self.formatter._build_status_report(plc_info, grouped_vars)

    def _send_dynamic_status(self, chat_id: Optional[int] = None) -> Any:
        """
        Dispatches a real-time status report to a specific Telegram chat or 
        default channel. If the cache is empty, it sends a waiting message 
        instead. Otherwise, it formats the status report and sends it to 
        Telegram, logging the action accordingly.

        Args:
            chat_id (Optional[int]): Specific Telegram chat ID to send the 
                report to. If None, it sends to the default channel.

        Returns:
            Any: The response from the Telegram service after sending the 
                notification.
        """
        status_msg: Optional[str] = self._format_status_report()

        if not status_msg:
            waiting_msg: str = "⏳ *Aguardando primeiras leituras dos sensores via MQTT...*"
            self._logger_mqtt.info(
                "Status report requested but cache is still empty.")
            return self.telegram_service._send_notifications(
                waiting_msg, chat_id=chat_id)

        self._logger_telegram.info(
            "📊 Dynamic status report dispatched to Telegram.")
        return self.telegram_service._send_notifications(
            status_msg, chat_id=chat_id)
    
    def _send_uptime_message(self, chat_id: Optional[int] = None) -> Any:
        """
        Calculates system uptime and dispatches a formatted report via Telegram.        
        Converts elapsed seconds since startup into a human-readable format 
        (Days, Hours, Minutes, Seconds).

        Args:
            chat_id (Optional[int]): Specific Telegram chat ID to send the 
                uptime report to. If None, it sends to the default channel.

        Returns:
            Any: The response from the Telegram service after sending the 
            notification.
        """
        uptime_seconds: int = int(time.time() - self._start_time)
        
        days: int = uptime_seconds // 86400
        hours: int = (uptime_seconds % 86400) // 3600
        minutes: int = (uptime_seconds % 3600) // 60
        seconds: int = uptime_seconds % 60
        
        uptime_str: str = f"{days}d {hours}h {minutes}m {seconds}s"
        start_date_str: str = time.strftime(
            '%d/%m/%Y %H:%M:%S', time.localtime(self._start_time))
        
        msg: str = self.formatter._build_uptime_message(
            uptime_str, start_date_str)
        
        self._logger_telegram.info(f"⏱️ Uptime report sent: {uptime_str}")
        return self.telegram_service._send_notifications(msg, chat_id=chat_id)
    
    def _is_graphable(self, value: Any) -> bool:
        """
        Validates if a sensor value is numeric and suitable for time-series 
        charting.

        Args:
            value (Any): The sensor reading to evaluate.

        Returns:
            bool: True if the value is a number (int or float), False otherwise.
        """
        return ConfigLoader._is_graphable(value)

    def _build_graph_keyboard(self) -> List[List[Dict[str, str]]]:
        """
        Filters active sensors for numeric data and builds an interactive 
        keyboard. Only sensors currently present in the cache with 'graphable' 
        values will be included in the menu.

        Returns:
            List[List[Dict[str, str]]]: A nested list representing rows of 
            inline keyboard buttons, where each button is a dictionary with 
            'topic' and 'label'.
        """
        graphable_list: List[Dict[str, str]] = []

        for var_config in self._config.get('monitoring_list', []):
            topic: str = var_config.get('topic', '')
            label: str = var_config.get('description', 'Sensor')
            
            data: Any = self._current_values_cache.get(topic)
            value: Any = data.get("value") if isinstance(data, dict) else data

            if self._is_graphable(value):
                graphable_list.append({
                    "topic": topic,
                    "label": label})

        return self.formatter._build_sensor_graph_keyboard(graphable_list)

    def _handle_graph_command(self, chat_id: int) -> Union[Any, bool]:
        """
        Orchestrates the generation and dispatch of the graph selection menu.        
        Provides immediate feedback if no data is available or if no numeric 
        sensors are currently active.

        Args:
            chat_id (int): The Telegram chat ID to send the graph menu to.

        Returns:
            Union[Any, bool]: The response from the Telegram service after 
                sending the menu, or False if an error occurred during 
                processing.
        """
        try:
            if not self._current_values_cache:
                msg: str = \
                    "⚠️ Nenhum dado disponível para gerar gráficos no momento."
                return self.telegram_service._send_notifications(
                    msg, chat_id=chat_id)

            keyboard_buttons: List[List[Dict[str, str]]] = self._build_graph_keyboard()

            if not keyboard_buttons:
                msg: str = (
                    "⚠️ No momento, não há sensores com dados numéricos "
                    "ativos para gerar gráficos históricos.")
                return self.telegram_service._send_notifications(
                    msg, chat_id=chat_id)

            reply_markup: Dict[str, List[List[Dict[str, str]]]] = {
                "inline_keyboard": keyboard_buttons}
            
            self._logger_telegram.info("📊 Graph selection menu dispatched.")
            return self.telegram_service._send_notifications(
                "📈 *Menu de Gráficos*\nEscolha um sensor para visualizar o histórico:",
                chat_id, reply_markup)

        except Exception as e:
            self._logger_telegram.error(f"❌ Error generating graph menu: {e}")
            return False
    
    def _extract_sensor_info(self, command: str) -> Tuple[str, Optional[str]]:
        """
        Parses a Telegram callback command to extract the MQTT topic and 
        sensor label.

        Args:
            command: The raw callback string (e.g., "view_graph_factory/temp").

        Returns:
            Tuple[str, Optional[str]]: A tuple containing (topic, label). 
                Label is None if the topic is not mapped.
        """
        topic: str = str(command).replace("view_graph_", "").strip()
        label: Optional[str] = self.sensor_mapping.get(topic)

        if not label:
            self._logger_telegram.warning(
                f"⚠️ Label not found for topic: {topic}")
        return topic, label

    def _get_sensor_history(self, topic: str) -> Optional[Deque[Any]]:
        """
        Retrieves the historical data queue (deque) for a specific sensor topic.
        
        Args:
            topic (str): The unique MQTT topic identifier.

        Returns:
            Optional[Deque[Any]]: A thread-safe double-ended queue of values 
                                  or None if the topic has no history yet.
        """
        history_data: Optional[Deque[Any]] = self._history_cache.get(topic)

        if history_data is None or len(history_data) == 0:
            self._logger_mqtt.debug(f"ℹ️ No history found for topic: {topic}")
            return None

        return history_data

    def _process_graph_request(
            self, command: str, user_name: str, chat_id: int) -> None:
        """
        Orchestrates the lifecycle of a graph request. Handles parsing, data 
        retrieval from cache, image generation via ChartGeneratorService, and 
        dispatching the final image to Telegram. Provides comprehensive error 
        handling and user feedback at each step, ensuring a robust user 
        experience even in edge cases (e.g., no data, invalid topic).

        Args:
            command (str): The raw callback command containing the topic 
                info.
            user_name (str): The name of the user requesting the graph, for 
                logging.
            chat_id (int): The ID of the chat where the graph should be sent.
        """
        try:
            topic, label = self._extract_sensor_info(command)

            if not label:
                self.telegram_service._send_notifications(
                    f"❌ Erro: Sensor não mapeado para o tópico: `{topic}`",
                    chat_id)
                return

            self.telegram_service._send_notifications(
                f"⌛ Gerando gráfico histórico: *{label}*...", chat_id)

            history: Optional[Deque[Any]] = self._get_sensor_history(topic)
            
            if history and len(history) >= 2:
                history_list = list(history)
                
                img_buf: io.BytesIO = ChartGeneratorService.generate_line_chart(
                    label, history_list)
                
                self._logger_telegram.info(
                    f"📊 Chart successfully sent to {user_name} (Sensor: {label})")
                self.telegram_service._send_image(
                    img_buf, caption=f"📈 Histórico de Tendência: {label}",
                    chat_id=chat_id)
                
            else:
                error_msg = (
                    f"⚠️ Dados insuficientes para {label}. (Mínimo: 2  "
                    f"leituras, Atual: {len(history) if history else 0})")
                self.telegram_service._send_notifications(
                    error_msg, chat_id=chat_id)
                self._logger_telegram.warning(
                    f"📉 Insufficient data for graph request: {label}")

        except Exception as e:
            self._logger_telegram.error(
                f"❌ Failed to process graph request for {user_name}: {e}",
                exc_info=True)
            self.telegram_service._send_notifications(
                "❌ Ocorreu um erro técnico ao gerar o gráfico.", chat_id)

    def _extract_user_context(self, user_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extracts and normalizes user metadata from Telegram's raw data.        
        Ensures consistent naming and ID mapping for security checks and 
        logging.
        
        Args:
            user_info (Dict[str, Any]): The raw user information dictionary 
                from Telegram.

        Returns:
            Dict[str, Any]: A normalized dictionary containing 'name',
            'user_id', 'chat_id', 'msg_id', and 'is_private' keys for
            consistent downstream processing.
        """
        user_name: str = user_info.get('first_name', 'Desconhecido')
        
        return {
            "name": user_name,
            "user_id": user_info.get('id', 0),
            "chat_id": user_info.get('chat_id'),
            "msg_id": user_info.get('message_id'),
            "is_private": user_info.get('type') == 'private'
        }

    def _execute_with_private_redirect(
            self, user_id: int, user_name: str, chat_id: Union[int, str],
            command_func: Callable[[], bool], force_silent: bool = False) -> None:
        """
        Executes a command by temporarily redirecting output to the user's 
        private chat. Prevents sensitive industrial data exposure in public 
        groups. It switches the active telegram service chat_id, runs the 
        command, and reverts back.

        Args:
            user_id (int): Unique Telegram ID of the user.
            user_name (str): User's display name for logging and 
                notifications.
            chat_id (Union[int, str]): The originating chat ID 
                (group or private).
            command_func (Callable): The function or lambda to execute. Must 
                return bool.
            force_silent (bool, optional): If True, suppresses the "sent to 
                private" notification in groups. Defaults to False.
        """
        original_chat_id: str = self.telegram_service._chat_id
        is_origin_group: bool = str(chat_id) != str(user_id)

        try:
            if is_origin_group and not force_silent:
                self.telegram_service._send_notifications(
                    f"🔐 `{user_name}`, por segurança, a resposta "
                    "foi enviada ao seu chat privado.", chat_id=chat_id)
            
            self.telegram_service._chat_id = str(user_id)
            
            success: bool = command_func()
            
            if not success:
                self.telegram_service._chat_id = original_chat_id
                self._logger_telegram.warning(
                    f"⚠️ Redirect failed for {user_name} "
                    "(Private chat not initialized).")
                
                self.telegram_service._send_notifications(
                    f"⚠️ `{user_name}`, não consegui acessar seu chat privado. "
                    "Por favor, clique em @TelemetriaPyBot e envie um `/start`.")
                
        except Exception as e:
            self._logger_telegram.error(
                f"❌ Error during private redirect execution: {e}")
        
        finally:
            self.telegram_service._chat_id = original_chat_id
    
    def _promote_user_to_admin(
            self, requester_id: int, target_new_id: int) -> None:
        """
        Orchestrates the admin promotion process. Persists the new admin ID to 
        the configuration file and notifies the requester.

        Args:
            requester_id (int): ID of the admin who initiated the promotion.
            target_new_id (int): ID of the user to be promoted.
        """
        success: bool = self._save_new_admin_to_yaml(target_new_id)
        msg: str = self.formatter._format_admin_update_status(
            success, target_new_id)

        self.telegram_service._send_notifications(msg, chat_id=requester_id)
        self._logger_telegram.warning(f"🎊 ID {target_new_id} access accepted.")
    
    def _execute_admin_revocation(
            self, requester_id: int, target_id: int) -> None:
        """
        Revokes administrative privileges, updates configuration, and notifies 
        parties.

        Args:
            requester_id (int): Telegram ID of the admin performing the 
                revocation.
            target_id (int): Telegram ID of the user losing admin rights.
        """
        try:
            self._remove_admin_from_yaml(target_id)
            
            # Revert in-memory service state
            if target_id in self.telegram_service._admin_ids:
                self.telegram_service._admin_ids.remove(target_id)

            msg: str = self.formatter._format_admin_removal_status(target_id)
            self.telegram_service._send_notifications(
                msg, chat_id=requester_id)
            self._logger_telegram.warning(
                f"🛡️ Admin access revoked for ID {target_id} by {requester_id}")
        except Exception as e:
            self._logger_telegram.error(
                f"❌ Failed to revoke admin status: {e}")

    def _handle_admin_callback(
            self, cmd: str, user_id: int,
            msg_id: Optional[int], user_info: Dict[str, Any],
            user_name: str = 'Admin') -> bool:
        """
        Orchestrates administrative callbacks with verification and UI cleanup.
        Handles inline button interactions for system control, user management,
        and confirmation dialogs.

        Args:
            cmd (str): The raw command string or callback data received.
            user_id (int): Telegram ID of the user who triggered the callback.
            msg_id (Optional[int]): ID of the message containing the buttons 
                to be deleted.
            user_info (Dict[str, Any]): Full context of the user for promotion 
                logic.
            user_name (str): Display name for logging.

        Returns:
            bool: True if the command was recognized and handled, False 
                otherwise.
        """
        current_name: str = (user_info.get('first_name') or 
                        user_info.get('name') or 
                        user_info.get('username') or 
                        "Admin")

        if self._handle_monitoring_callbacks(
            cmd, user_id, msg_id, user_name=current_name):
            return True
        
        try:
            def cleanup():
                if msg_id: self.telegram_service._delete_message(
                    user_id, msg_id)
        
            if cmd.startswith("view_graph_"):
                cleanup()                
                self._process_graph_request(
                    command=cmd, user_name=user_name, chat_id=user_id)
                return True
            
            if cmd.startswith("/set_var "):
                parts: List[str] = cmd.split()
                return self._handle_set_var_with_cleanup(
                    parts=parts, user_name=current_name, 
                    user_id=user_id, msg_id=msg_id)
            
            if cmd == "CANCEL_CMD_MENU":
                cleanup() 
                self.telegram_service._send_notifications(
                    "❌ *Acionamento cancelado.*\nNenhum comando foi enviado ao CLP.", 
                    chat_id=user_id)
                return True
                    
            if cmd == "CONFIRM_HALT":
                if self.telegram_service._is_active_admin_in_group(user_id):
                    cleanup() 
                    self._shutdown_bot_system(user_id=user_id)
                return True
            
            if cmd == "CANCEL_HALT":
                cleanup()
                self.telegram_service._send_private_message(
                    user_id, "✅ Operação cancelada. O sistema continua ativo.")
                return True

            if cmd == "EVENT_PROMOTE":
                if self.telegram_service._admin_ids:
                    target_admin: int = self.telegram_service._admin_ids[0]
                    self.telegram_service._send_promotion_confirmation(
                        target_admin, user_info)
                    self._logger_telegram.info(
                        f"📢 Promotion notification sent to admin: {target_admin}")
                return True
            
            if cmd == "EVENT_DEMOTE":
                target_id: int = user_info.get('target_id', user_id)
                requester_id: int = user_info.get('executor_id', user_id)

                if requester_id == target_id and self.telegram_service._admin_ids:
                    requester_id = self.telegram_service._admin_ids[0]

                self._execute_admin_revocation(
                    requester_id=requester_id, target_id=target_id)
                #self.telegram_service._send_notifications(
                #    f"🛡️ *SEGURANÇA:* Acesso administrativo do ID {user_id} revogado.")
                cleanup()
                return True

            if cmd.startswith("CONFIRM_ADD_"):
                new_id = int(cmd.replace("CONFIRM_ADD_", ""))
                self.telegram_service._add_admin_id(new_id)
                self._promote_user_to_admin(
                    requester_id=user_id, target_new_id=new_id)
                #self.telegram_service._send_notifications(
                #    f"🎊 *SISTEMA:* Permissões atualizadas.\n"
                #    f"O usuário ID `{new_id}` agora é um novo Administrador!")
                cleanup() 
                return True
        
        except Exception as e:
            self._logger_telegram.error(
                    f"❌ Error processing callback '{cmd}': {e}")
        return False
    
    def _handle_monitoring_callbacks(
            self, cmd: str, user_id: int,
            msg_id: Optional[int], user_name: str = 'Admin') -> bool:
        """
        Manages exclusive monitoring configuration callbacks 
        (Add/Remove sensors). Orchestrates the multi-step wizard for dynamic 
        PLC tag monitoring, handling category selection and removal 
        confirmations.

        Args:
            cmd (str): The callback command data.
            user_id (int): Telegram ID of the user.
            msg_id (Optional[int]): Message ID for UI cleanup.
            user_name (str): User display name for logging.

        Returns:
            bool: True if the command was part of the monitoring flow.
        """       
        def cleanup():
            if msg_id: self.telegram_service._delete_message(user_id, msg_id)

        if cmd.startswith("AV_TYPE_"):
            cleanup() 
            target_type = cmd.replace("AV_TYPE_", "")
            self._show_filtered_vars_menu(user_id, target_type)
            return True

        if cmd.startswith("AV_SEL_"):
            cleanup()
            self._ask_for_monitoring_details(
                user_id, cmd.replace("AV_SEL_", ""))
            return True

        if cmd == "RM_VAR_RETRY" or cmd.startswith("RM_TYPE_"):
            cleanup()
            if cmd == "RM_VAR_RETRY":
                self._send_rm_var_step1_type(user_id)
            else:
                self._send_rm_var_step2_list(
                    user_id, cmd.replace("RM_TYPE_", ""))
            return True

        if cmd.startswith("RM_CONFIRM_"):
            cleanup()
            self._ask_rm_confirmation(user_id, cmd.replace("RM_CONFIRM_", ""))
            return True

        if cmd.startswith("RM_EXECUTE_"):
            cleanup()
            var_name = cmd.replace("RM_EXECUTE_", "")
            self._remove_monitoring_var(
                var_name, user_id=user_id, user_name=user_name)
            return True

        if cmd == "CANCEL_ADD_VAR":
            self._user_states.pop(user_id, None)
            cleanup()
            self.telegram_service._send_notifications(
                "❌ Operação cancelada.", chat_id=user_id)
            return True

        return False

    def _telegram_command_handler(
            self, command: str, user_info: Dict[str, Any]) -> None:
        """
        Main entry point for all Telegram interactions.
        Routes input through a priority hierarchy:
        1. FSM (Active user states for data input)
        2. Callbacks (Inline button interactions)
        3. Text Commands (Direct slash commands or messages)

        Args:
            command (str): Raw string received from Telegram.
            user_info (Dict[str, Any]): Metadata about the sender and context.
        """
        if not command:
            return

        ctx: Dict[str, Any] = self._extract_user_context(user_info)
        raw_cmd: str = command.strip()
        
        try:
            if ctx['user_id'] in self._user_states:
                return self._handle_fsm_input(
                    ctx['user_id'], raw_cmd, ctx['name'])

            if self._handle_admin_callback(
                raw_cmd, ctx['user_id'], ctx['msg_id'], user_info):
                return

            self._dispatch_text_command(raw_cmd, ctx)

        except Exception as e:
            self._logger_telegram.error(
                f"❌ Dispatcher Error: {e}", exc_info=True)

    def _handle_fsm_input(self, user_id: int, text: str, name: str) -> None:
        """
        Processes user text input based on the current Finite State Machine 
        (FSM) context.

        Args:
            user_id (int): Telegram ID of the user.
            text (str): The text message sent by the user.
            name (str): User display name for logging and finalization.
        """
        state: Dict[str, Any] = self._user_states[user_id]
        
        if state['action'] == 'WAITING_DESCRIPTION':
            state['description'] = text
            state['action'] = 'WAITING_INTERVAL'
            self.telegram_service._send_notifications(
                "⏱️ Qual o **Intervalo de Leitura** (segundos)?\nEx: `1.0`", 
                chat_id=user_id
            )
        elif state['action'] == 'WAITING_INTERVAL':
            self._finalize_variable_addition(user_id, text, name)
        elif state['action'] == 'WAITING_CMD_DESCRIPTION':
            state['description'] = text.strip()
            self._finalize_command_addition(user_id)

    def _dispatch_text_command(self, raw_cmd: str, ctx: Dict[str, Any]) -> None:
        """
        Parses and routes slash commands to their respective public or admin 
        handlers.

        Args:
            raw_cmd (str): The raw text command (e.g., "/status@bot").
            ctx (Dict[str, Any]): Normalized user context.
        """
        parts: List[str] = raw_cmd.split()
        base_cmd: str = parts[0].split('@')[0].lower() if parts else ""

        public_cmds: Dict[str, Callable] = {
            "/help": lambda: self._send_help_message(ctx['chat_id']),
            "/status": lambda: self._send_dynamic_status(ctx['chat_id']),
            "/uptime": self._send_uptime_message,
            "/ping": self._send_plc_ping,
            "/cancel": self._send_cancel_command}

        admin_cmds: Dict[str, Callable] = {
            "/cycles": self._send_production_cycles,
            "/resources": self._send_resources_usage,
            "/maintenance": self._maintenance_mode,
            "/next_maintenance": self._next_maintenance,
            "/commands": self._send_boolean_commands_menu,
            "/scan": self._scan_network,
            "/graph": lambda: self._handle_graph_command(ctx['user_id']),
            "/stop_system": lambda: self.telegram_service._send_shutdown_confirmation(ctx['user_id']),
            "/add_var": lambda: self._send_add_var_step1_type(ctx['user_id']),
            "/rm_var": lambda: self._send_rm_var_step1_type(ctx['user_id']),
            "/add_cmd": lambda: self._send_add_cmd_step1_type(ctx['user_id']), 
            "/logs": lambda: self._send_logs_command(ctx),}

        if base_cmd in public_cmds:
            public_cmds[base_cmd]()
        
        elif base_cmd in admin_cmds:
            self._route_admin_command(base_cmd, admin_cmds[base_cmd], ctx)

    def _route_admin_command(
            self, cmd_name: str, func: Callable, ctx: Dict[str, Any]) -> None:
        """
        Validates admin permissions and executes commands with potential 
        private redirection.

        Args:
            cmd_name (str): The name of the command being executed.
            func (Callable): The function to be called if authorized.
            ctx (Dict[str, Any]): Normalized user context.
        """
        if not self.telegram_service._is_active_admin_in_group(ctx['user_id']):
            self.telegram_service._send_notifications(
                f"🚫 *ACESSO NEGADO*\nUsuário {ctx['name']} "
                "não possui privilégios de Administrador.",
                chat_id=ctx['chat_id'])
            return

        if not ctx['is_private'] and cmd_name == "/maintenance":
            status: str = "FINALIZOU" if getattr(
                self, 'maintenance_active', False) else "INICIOU"
            self.telegram_service._send_notifications(
                f"🔧 *MANUTENÇÃO:* @{ctx['name']} "
                f"{status} o modo de manutenção.")

        # Redirect certain commands to private chat for security and UX reasons
        is_menu: bool = cmd_name in [
            "/maintenance", "/stop_system", "/add_var", "/rm_var"]
        self._execute_with_private_redirect(
            ctx['user_id'], ctx['name'], ctx['chat_id'],
            func, force_silent=is_menu)

    def _handle_set_var(
            self, parts: List[str], user_name: str, user_id: int) -> bool:
        """
        Identifies the variable type and routes the command to the appropriate 
        PLC action. Distinguishes between remote pulse commands (typically 
        prefixed with 'ri_') and boolean toggles based on the command 
        configuration.

        Args:
            parts (List[str]): Command and arguments (e.g., ['/set_var', 'ri_start']).
            user_name (str): Name of the user for logging and auditing.
            user_id (int): Telegram ID of the user.

        Returns:
            bool: True if the PLC command was successfully executed.
        """
        if len(parts) < 2:
            self.telegram_service._send_notifications(
                "❓ Uso correto: `/set_var NOME_DA_VARIAVEL`",
                chat_id=user_id)
            return False

        var_target: str = parts[1].strip()
        
        commands_config: List[Dict[str, Any]] = self._config.get(
            'commands', [])
        var_cfg: Optional[Dict[str, Any]] = next(
            (item for item in commands_config 
            if item.get('variable') == var_target), None)

        if not var_cfg:
            self._logger_telegram.warning(
                f"⚠️ Attempted to set unmapped variable: {var_target}")
            self.telegram_service._send_notifications(
                f"⚠️ A variável `{var_target}` não está mapeada no "
                "arquivo de comandos.", chat_id=user_id)
            return False

        pou_file: str = var_cfg.get('pou', 'PLC_PRG')

        try:
            if var_target.startswith("ri_"):
                self._logger_telegram.info(
                    f"📱 [PULSE] Executing {pou_file}.{var_target} (User: {user_name})")
                return self._send_plc_pulse(
                var_name=var_target, pou_name=pou_file, target_id=user_id)
            
            self._logger_telegram.info(
                f"🔄 [TOGGLE] Switching {pou_file}.{var_target} (User: {user_name})")
            return self._toggle_boolean_variable(
            var_name=var_target, pou_name=pou_file, user_id=user_id)
                
        except Exception as e:
            self._logger_telegram.error(
                f"❌ Critical error targeting '{var_target}': {e}",
                exc_info=True)
            self.telegram_service._send_notifications(
                f"❌ Erro ao enviar comando para `{var_target}`.",
                chat_id=user_id)
            return False
    
    def _handle_set_var_with_cleanup(
            self, parts: List[str], user_name: str,
            user_id: int, msg_id: Optional[int]) -> bool:
        """
        Executes a variable change and removes the triggering message upon 
        success. This method acts as a middle layer that calls the PLC write 
        logic and, if successful, cleans up the Telegram UI by deleting the 
        message that contained the command buttons.

        Args:
            parts (List[str]): List of command arguments (e.g., ['/set_var', 
                'var_name']).
            user_name (str): Name of the user performing the action for 
                logging purposes.
            user_id (int): Telegram ID of the user (required for message 
                deletion).
            msg_id (Optional[int]): ID of the message to be deleted after 
                execution.

        Returns:
            bool: True if the variable was set successfully, False otherwise.
        """
        success: bool = self._handle_set_var(parts, user_name, user_id)
        
        if success and msg_id:
            try:
                self.telegram_service._delete_message(user_id, msg_id)
                self._logger_telegram.debug(
                    f"🗑️ Cleanup: Message {msg_id} removed "
                    "after successful /set_var.")
            except Exception as e:
                self._logger_telegram.warning(
                    f"⚠️ Could not delete message {msg_id}: {e}")
            
        return success
    
    def _check_plc_availability(self) -> bool:  
        """
        Verifies the operational health of the PLC using a heartbeat mechanism.
        
        Returns:
            bool: True if the PLC is pulsing and communicating, False otherwise.
        """
        pou_heartbeat: str = self._get_dynamic_heartbeat_pou() or "PLC_PRG"
        
        var_heartbeat: str = "ui_heartbeat_plc" 

        try:
            is_healthy: bool = self.plc._is_plc_healthy(
                heartbeat_pou=pou_heartbeat, 
                heartbeat_var=var_heartbeat)

            if not is_healthy:
                self._logger_mqtt.warning(
                    "⚠️ PLC Heartbeat Lost! Target: "
                    f"{pou_heartbeat}.{var_heartbeat}")
            
            return is_healthy

        except Exception as e:
            self._logger_telegram.error(
                f"❌ Error during PLC health check: {e}")
            return False
    
    def _send_add_var_step1_type(self, user_id: int) -> bool:
        """
        Starts the variable addition process by sending the type selection 
        menu to the administrator's private chat.

        Args:
            user_id (int): Administrator's Telegram ID.

        Returns:
            bool: True if the message was sent successfully.
        """
        if not self._check_plc_availability():
            warning_msg: str = (
                "⚠️ **OPERAÇÃO BLOQUEADA**\n\n"
                "O CLP está **OFFLINE** ou o ciclo de scan parou.\nVerifique a"
                " rede e o status da CPU antes de configurar novas tags.")
            self.telegram_service._send_notifications(
                warning_msg, chat_id=user_id)
            self._logger_telegram.warning(
                f"🚫 Blocked add_var attempt by {user_id} due to PLC downtime.")
            return False

        menu_data: Dict[str, Any] = self.formatter._get_add_variable_type_menu()
        
        try:
            self.telegram_service._send_notifications(
                menu_data["text"], chat_id=user_id, 
                keyboard=menu_data["reply_markup"])
            return True
            
        except Exception as e:
            self._logger_telegram.error(f"❌ Failed to send add_var menu: {e}")
            return False
    
    def _is_matching_type(self, value: Any, target_type: str) -> bool:
        """
        Strictly validates if a PLC value matches the expected category.
        Prevents type overlap (e.g., Python treating booleans as integers).

        Args:
            value (Any): The current value read from the PLC.
            target_type (str): The desired filter ('BOOL' or 'NUM').

        Returns:
            bool: True if the type strictly matches.
        """
        is_boolean_type: bool = isinstance(value, bool)
        is_numeric_type: bool = isinstance(
            value, (int, float)) and not is_boolean_type

        type_map: Dict[str, bool] = {
            "BOOL": is_boolean_type,
            "NUM": is_numeric_type}

        return type_map.get(target_type.upper(), False)

    def _get_available_plc_vars(self, target_type: str) -> List[str]:
        """
        Scans the PLC for tags that are not yet monitored and match the target 
        type.
        
        Args:
            target_type (str): Category filter ('NUM' or 'BOOL').
            
        Returns:
            List[str]: A list of available variable names for the wizard.
        """
        scan_pous: List[str] = list(self.plc._pou_node_cache.keys())
        if not scan_pous:
            scan_pous = [self._get_dynamic_heartbeat_pou() or "PLC_PRG"]

        all_plc_variables: Dict[str, Any] = self.plc.read_all_variables(scan_pous)
        
        monitored_tags: Set[str] = {
            item.get('variable') for item in 
            self._config.get('monitoring_list', [])}

        available_vars: List[str] = []
        for name, value in all_plc_variables.items():
            if name not in monitored_tags and self._is_matching_type(
                value, target_type):
                available_vars.append(name)

        self._logger_mqtt.debug(
            f"🔍 Scan complete. Found {len(available_vars)} new '{target_type}' "
            f"vars in {scan_pous}.")
        
        return sorted(available_vars)

    def _show_filtered_vars_menu(self, user_id: int, target_type: str) -> bool:
        """
        Renders an inline menu with available PLC variables of a specific type.
        
        Args:
            user_id (int): Telegram ID to receive the menu.
            target_type (str): Type filter ('BOOL' or 'NUM').

        Returns:
            bool: True if the menu was sent successfully, False otherwise.
        """
        try:
            available_vars: List[str] = self._get_available_plc_vars(target_type)
            
            if not available_vars:
                msg: str = f"❌ Nenhuma nova variável *{target_type}* encontrada."
                btn_data: List[Dict[str, str]] = [
                    {'text': "⬅️ Voltar", 'callback': "ADD_VAR_RETRY"}]
                markup = self.formatter._build_inline_keyboard(
                    btn_data, columns=1)
            else:
                industrial_name_types: Dict[str, str] = {
                    'bool': 'digitais',
                    'int': 'analógicas',
                    'float': 'analógicas'}
                
                formated_type: str = industrial_name_types.get(
                    target_type.lower(), 'específicas')
                
                msg: str = (
                    f"📋 *Variáveis {formated_type} "
                    "disponíveis:*\nSelecione uma:")
                
                btn_list: List[Dict[str, str]] = [
                    {'text': f"➕ {v}", 'callback': f"AV_SEL_{v}"} 
                    for v in available_vars]

                btn_list.append({'text': "❌ Cancelar Operação",
                                 'callback': "CANCEL_ADD_VAR"})
            
                markup = self.formatter._build_inline_keyboard(
                    btn_list, columns=2)

            return self.telegram_service._send_notifications(
                msg, chat_id=user_id, keyboard=markup)

        except Exception as e:
            self._logger_telegram.error(f"❌ Error to renderize menu: {e}")
            return False
    
    def _get_current_timestamp(self) -> str:
        """
        Generates a standardized ISO-8601 timestamp for logging and state 
        tracking.
        
        Returns:
            str: Current timestamp in "YYYY-MM-DD HH:MM:SS" format."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def _ask_for_monitoring_details(self, user_id: int, var_name: str) -> bool:
        """
        Transitions the user state to collect metadata for a specific tag.
        
        Args:
            user_id (int): Administrator's ID.
            var_name (str): The PLC tag selected in the previous step.
        
        Returns:
            bool: True if the prompt was sent successfully, False otherwise.
        """
        self._user_states[user_id] = {
            'action': 'WAITING_DESCRIPTION',
            'variable': var_name,
            'started_at': self._get_current_timestamp()}

        msg: str = (
            f"🎯 *Tag Selecionada:* `{var_name}`\n\n"
            "✍️ Digite agora uma **Descrição Amigável** para ela.\n"
            "Esta descrição aparecerá nos alertas e gráficos.\n\n"
            "💡 _Ex: Temperatura do Motor Principal_")

        try:
            self._logger_telegram.info(
                f"📝 Wizard started for {user_id}: waiting description for {var_name}.")
            
            return self.telegram_service._send_notifications(
                msg, chat_id=user_id)
            
        except Exception as e:
            self._user_states.pop(user_id, None)
            self._logger_telegram.error(
                f"❌ Failed to request description from {user_id}: {e}")
            return False
    
    def _parse_interval(self, raw_interval: str) -> Optional[float]:
        """
        Converts user input into a valid float interval, handling regional 
        separators.

        Args:
            raw_interval (str): String input from Telegram (e.g., "1,5" or 
                "1.5").

        Returns:
            Optional[float]: Valid interval in seconds or None if invalid.
        """
        if not raw_interval or not isinstance(raw_interval, str):
            return None

        try:
            clean_input: str = raw_interval.strip().replace(',', '.')
            interval: float = float(clean_input)
            
            if interval <= 0:
                self._logger_telegram.warning(
                    f"⚠️ Invalid interval attempted: {interval}s. Must be > 0.")
                return None
                
            return interval

        except (ValueError, TypeError):
            self._logger_telegram.debug(
                f"🔍 Failed to parse interval string: '{raw_interval}'")
            return None
    
    def _create_topic_from_add_var_method(self, variable: str) -> Tuple[str, str]:
        """
        Resolves the MQTT topic hierarchy by locating the variable's POU in 
        the PLC.
        Format: {POU_NAME}/{VARIABLE_NAME}

        Args:
            variable (str): The PLC tag name for which to create the topic.

        Returns:
            Tuple[str, str]: (POU Name, Full MQTT Topic).
        """
        target_pous: List[str] = list(self.plc._pou_node_cache.keys())
        if not target_pous:
            target_pous: List[str] = [
                self._get_dynamic_heartbeat_pou() or "PLC_PRG"]

        for pou in target_pous:
            vars_in_pou: List[str] = self.plc.read_all_variables(pou)
            
            if variable in vars_in_pou:
                topic = f"{pou}/{variable}"
                self._logger_mqtt.debug(f"📍 Topic resolved: {topic}")
                return pou, topic

        self._logger_mqtt.warning(
            f"⚠️ Variable '{variable}' not found in known POUs. Using fallback.")
        return "Unknown", f"Unknown/{variable}"

    def _persist_monitoring_var(
            self, variable: str, description: str, interval: float) -> bool:
        """
        Saves the variable to YAML and dynamically starts its monitoring task.

        Args:
            variable (str): PLC Tag name.
            description (str): User-friendly description.
            interval (float): Reading interval in seconds.

        Returns:
            bool: True if persisted and (if loop active) injected.
        """
        if 'monitoring_list' not in self._config:
            self._config['monitoring_list'] = []

        pou_name, new_topic = self._create_topic_from_add_var_method(variable)
        
        new_entry: Dict[str, Union[str, int, float]] = {
            'variable': variable, 
            'topic': new_topic,
            'description': description, 
            'interval': interval,
            'pou': pou_name}
        
        self._config['monitoring_list'].append(new_entry)
        
        # Initialize telemetry cache for the new topic
        if hasattr(self, '_history_cache'):
            self._history_cache[new_topic] = deque(maxlen=100)
        
        if not self._save_config_to_yaml():
            self._logger_mqtt.error(
                f"❌ Critical fail to persist YAML file to: {variable}")
            return False

        if self._loop and self._loop.is_running():
            try:
                future: asyncio.Future = asyncio.run_coroutine_threadsafe(
                    self.variable_monitoring_task(new_entry), 
                    self._loop)

                self._active_monitoring_tasks[variable] = future
                
                self._logger_mqtt.info(
                    f"✅ Dynamic task initiated by ThreadSafe for: {variable}")
                return True
            except Exception as e:
                self._logger_mqtt.error(
                    f"❌ Error to inject {variable} task on loop: {e}")
                return False
        else:
            self._logger_mqtt.warning(
                f"⚠️ YAML file saved, but loop not founded "
                f"to iniciate '{variable}' now.")
            return True

    def _finalize_variable_addition(
            self, user_id: int, raw_interval: str, user_name: str) -> bool:
        """
        Finalizes the variable addition wizard, persists data, and broadcasts
        success. This method is called after the user has provided the 
        description and interval. It handles the final validation, YAML 
        persistence, and user notifications for the new monitoring variable.

        Args:
            user_id (int): Telegram ID of the administrator.
            raw_interval (str): User-provided interval string.
            user_name (str): Name of the administrator.

        Returns:
            bool: True if the variable was successfully added and persisted, False otherwise.
        """
        state: Dict[str, str] = self._user_states.get(user_id)
        if not state:
            return False

        interval: float = self._parse_interval(raw_interval)
        if interval is None:
            self.telegram_service._send_notifications(
                "⚠️ *Intervalo Inválido!*\nUse um número (ex: `1.5`).", 
                chat_id=user_id)
            return False

        try:
            if not self._persist_monitoring_var(
                state['variable'], state['description'], interval):
                raise Exception("Erro na persistência YAML.")

            admin_ui: str = self.formatter._format_add_var_success_admin(
                state, interval)
            self.telegram_service._send_notifications(
                admin_ui["text"], chat_id=user_id)
            
            group_ui: str = self.formatter._format_add_var_broadcast_group(
                state, user_name)
            self.telegram_service._send_notifications(group_ui["text"])

            self._logger_telegram.info(
                f"✅ Variable '{state['variable']}' finalized by {user_name}")
            self._user_states.pop(user_id, None)
            return True

        except Exception as e:
            self._logger_telegram.error(f"❌ Error finalizing variable: {e}")
            self.telegram_service._send_notifications(
                "❌ Erro ao salvar configuração.", chat_id=user_id)
            return False
    
    def _send_rm_var_step1_type(self, user_id: int) -> None:
        """
        Starts the removal wizard by asking for the variable category.
        Administrators can choose to filter monitored variables by type (BOOL or NUM)
        before selecting which one to remove. This method sends the initial menu.

        Args:
            user_id (int): Telegram ID of the administrator to receive the menu.
        """
        try:
            menu_ui: str = self.formatter._get_remove_variable_type_menu()
            
            self._logger_telegram.debug(
                f"🗑️ Removal wizard started for Admin: {user_id}")

            return self.telegram_service._send_notifications(
                menu_ui["text"], chat_id=user_id,
                keyboard=menu_ui["reply_markup"])

        except Exception as e:
            self._logger_telegram.error(f"❌ Failed to send removal menu: {e}")
            self.telegram_service._send_notifications(
                "❌ Erro ao abrir menu de remoção.", 
                chat_id=user_id)

    def _send_rm_var_step2_list(self, user_id: int, target_type: str) -> None:
        """
        Filters currently monitored variables and displays them for removal selection.
        After the administrator selects a type (BOOL or NUM), this method 
        retrieves the list of currently monitored variables of that type and 
        sends an inline menu for selection. It uses the current values cache 
        to determine the type of each monitored variable.

        Args:
            user_id (int): Telegram ID of the administrator.
            target_type (str): The type filter selected in the previous step 
                ('BOOL' or 'NUM').
        """
        all_monitored: List[Dict[str, Any]] = self._config.get(
            'monitoring_list', [])
        filtered_vars: List[str] = []

        for item in all_monitored:
            cached_data: Dict[str, Any] = self._current_values_cache.get(
                item['topic'], {})
            current_val: Any = cached_data.get('value')
            
            if self._is_matching_type(current_val, target_type):
                filtered_vars.append(item['variable'])

        menu_ui: Dict[str, Any] = self.formatter._format_remove_list_menu(
            filtered_vars, target_type)

        try:
            self.telegram_service._send_notifications(
                menu_ui["text"], chat_id=user_id, 
                keyboard=menu_ui["reply_markup"])
        except Exception as e:
            self._logger_telegram.error(
                f"❌ Error displaying removal list: {e}")
    
    def _ask_rm_confirmation(self, user_id: int, var_name: str) -> None:
        """
        Displays a safety confirmation dialog before permanent variable removal.
        When an administrator selects a variable for removal, this method sends
        a confirmation message with inline buttons to confirm or cancel the 
        action. This step is crucial to prevent accidental deletions of 
        monitored variables, which could lead to data loss or monitoring gaps.

        Args:
            user_id (int): Telegram ID of the administrator.
            var_name (str): The name of the variable selected for removal.
        """
        try:
            confirmation_ui: Dict[str, Any] = self.formatter._get_remove_confirmation_menu(
                var_name)

            self.telegram_service._send_notifications(
                confirmation_ui["text"], chat_id=user_id, 
                keyboard=confirmation_ui["reply_markup"])
            
            self._logger_telegram.warning(
                f"⚠️ Removal confirmation requested by {user_id} for tag: {var_name}")

        except Exception as e:
            self._logger_telegram.error(
                f"❌ Failed to send removal confirmation: {e}")
            self.telegram_service._send_notifications(
                "❌ Erro ao processar solicitação de exclusão.", 
                chat_id=user_id)
    
    def _find_monitored_item(self, variable_name: str) -> Optional[Dict[str, Any]]:
        """
        Locates the full configuration dictionary for a specific variable.
        Essential for retrieving topics and metadata before removal or update.

        Args:
            variable_name (str): The PLC tag name to search for in the 
            monitoring list.

        Returns:
            Optional[Dict[str, Any]]: The full config item if found, or None 
            if not found.
        """
        monitoring_list: List[Dict[str, Any]] = self._config.get(
            'monitoring_list') or []

        target_item: Optional[Dict[str, Any]] = next(
            (item for item in monitoring_list if item.get(
            'variable') == variable_name), None)

        if not target_item:
            self._logger_mqtt.debug(
                f"🔍 Variable '{variable_name}' not found in monitoring_list.")
        
        return target_item

    def _delete_from_config(self, variable_name: str) -> bool:
        """
        Removes the variable from the in-memory configuration list. Returns 
        True if the list was actually modified.

        Args:
            variable_name (str): The PLC tag name to be removed from monitoring.

        Returns:
            bool: True if the variable was found and removed, False otherwise.
        """
        current_list: List[Dict[str, Any]] = self._config.get(
            'monitoring_list', [])
        initial_count: int = len(current_list)

        self._config['monitoring_list'] = [item for item in current_list 
            if item.get('variable') != variable_name]

        was_modified: bool = len(
            self._config['monitoring_list']) < initial_count

        if was_modified:
            self._logger_mqtt.info(
                f"💾 Memory config updated: '{variable_name}' removed.")
        else:
            self._logger_mqtt.warning(
                f"⚠️ Attempted to delete '{variable_name}', but it wasn't in config.")

        return was_modified

    def _clear_internal_caches(self, topic: str) -> None:
        """
        Purges all real-time and historical data associated with a specific 
        MQTT topic. Prevents memory leaks after a variable is removed from 
        monitoring.

        Args:
            topic (str): The MQTT topic associated with the variable being 
                removed.
        """
        if not topic:
            return

        removed_current: Optional[Dict[str, Any]] = self._current_values_cache.pop(topic, None)
        
        removed_history: bool = False
        if hasattr(self, '_history_cache'):
            if topic in self._history_cache:
                self._history_cache.pop(topic, None)
                removed_history = True

        if removed_current or removed_history:
            self._logger_mqtt.debug(
                f"🧹 Internal caches cleared for topic: {topic} "
                f"(Current: {bool(removed_current)}, History: {removed_history})"
            )
    
    def _remove_monitoring_var(
            self, variable_name: str, 
            user_name: str, 
            user_id: Optional[int] = None) -> bool:
        """
        Coordinates the full removal process: stops tasks, clears caches, and 
        persists changes. This method is called after the administrator 
        confirms the removal of a variable. It ensures that the monitoring 
        task is stopped, the variable is removed from the configuration, all 
        related caches are cleared, and the change is saved to YAML. It also 
        sends notifications about the successful removal.

        Args:
            variable_name (str): The PLC tag name to be removed from monitoring.
            user_name (str): Name of the administrator performing the action.
            user_id (Optional[int]): Telegram ID for sending a private 
                confirmation message.
        Returns:
            bool: True if the variable was successfully removed and changes 
                persisted, False otherwise.
        """
        try:
            friendly_name: str = self._get_friendly_name(variable_name)
            target_item: Optional[Dict[str, Any]] = self._find_monitored_item(
                variable_name)

            if not target_item:
                return False

            topic: str = target_item['topic']

            if hasattr(self, '_active_monitoring_tasks') and \
                variable_name in self._active_monitoring_tasks:
                task_future = self._active_monitoring_tasks.pop(variable_name)
                self._loop.call_soon_threadsafe(task_future.cancel)

                self._logger_mqtt.info(
                    f"🛑 Monitoring task stopped for: {variable_name}")

            self._delete_from_config(variable_name)
            success: bool = self._save_config_to_yaml()

            self._clear_internal_caches(topic)
            
            if topic in self.sensor_mapping:
                del self.sensor_mapping[topic]
            
            if success:
                group_ui = self.formatter._format_remove_var_broadcast_group(
                    friendly_name, user_name)
                self.telegram_service._send_notifications(group_ui["text"])
                
                if user_id:
                    admin_ui = self.formatter._format_remove_var_success_admin(
                        variable_name, friendly_name)
                    self.telegram_service._send_notifications(
                        admin_ui["text"], chat_id=user_id)
                    
                self._logger_plc.info(
                    f"🗑️ Removal of '{variable_name}' finalized system-wide.")
            
            return success

        except Exception as e:
            self._logger_telegram.error(
                "❌ Critical error during removal of "
                f"{variable_name}: {e}", exc_info=True)
            return False

    def _send_plc_ping(self, user_id: Optional[int] = None) -> None:
        """
        Executes a diagnostic ping using the centralized health check.
        Reports latency and operational status to the UI. This method uses the 
        existing PLC health check mechanism to determine if the PLC is 
        responsive. It measures the time taken for the check and formats a 
        user-friendly message with the results, which is then sent to the 
        Telegram interface.

        Args:
            user_id (Optional[int]): Telegram ID to receive the diagnostic 
                results. If None, sends to the group.
        """
        plc_conn: Dict[str, Any] = self._config.get('plc_connection', {})
        plc_name: str = plc_conn.get('name', 'CLP')
        
        self.telegram_service._send_notifications(
            f"🏓 Enviando ping para `{plc_name}`...", chat_id=user_id)
        
        start_time: float = time.perf_counter()
        
        with self.plc_lock:
            is_alive: bool = self._check_plc_availability()
            
        latency_ms: float = (time.perf_counter() - start_time) * 1000

        msg: str = self.formatter._format_plc_ping_result(
            is_alive=is_alive, latency_ms=latency_ms, plc_info=plc_conn)
        
        log_level: str = "INFO" if is_alive else "WARNING"
        log_info_msg: str = (f"📡 Ping Diagnostic: {plc_name} | Alive: "
                             f"{is_alive} | Latency: {latency_ms:.2f}ms")
        self._logger_plc.log(getattr(logging, log_level), log_info_msg)
        
        return self.telegram_service._send_notifications(
            msg, chat_id=user_id)
    
    def _perform_network_scan(
            self, ip_prefix: str, start: int = 1, end: int = 21) -> List[str]:
        """
        Scans a range of IP addresses on the local network to find active hosts.

        This method uses multi-threading to ping multiple IP addresses 
        concurrently. It automatically detects the operating system to apply 
        the correct ping parameters and returns a sorted list of reachable IPs.

        Args:
            ip_prefix (str): The first three octets of the network 
                (e.g., '192.168.0').
            start (int, optional): The starting value of the last octet. 
                Defaults to 1.
            end (int, optional): The end value (exclusive) of the last octet. 
                Defaults to 21.

        Returns:
            List[str]: A sorted list of active IP addresses found during the 
                scan.
        """
        is_windows: bool = platform.system().lower() == 'windows'
        count_param: str = '-n' if is_windows else '-c'

        timeout_param: List[str] = ['-w', '500'] if is_windows else ['-W', '1']

        def ping_ip(last_octet: int) -> Optional[str]:
            ip: str = f"{ip_prefix}.{last_octet}"
            command: List[str] = ['ping', count_param, '1'] + timeout_param + [ip]

            try:
                result: subprocess.CompletedProcess = subprocess.run(
                    command, stdout=subprocess.DEVNULL, 
                    stderr=subprocess.STDOUT, timeout=2)
                
                return ip if result.returncode == 0 else None
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=20) as executor:
            results: List[Optional[str]] = list(
                executor.map(ping_ip, range(start, end)))
        
        # Filtra Nones, remove duplicatas e ordena pelo último octeto
        active_hosts: List[str] = sorted(
            [ip for ip in results if ip], 
            key=lambda x: int(x.split('.')[-1]))
        
        return active_hosts

    def _scan_network(self, user_id: Optional[int] = None) -> None:
        """
        Orchestrates the network scan process and notifies the user. This 
        method extracts the IP prefix from the PLC configuration, initiates
        a high-speed multi-threaded scan, and sends the formatted results 
        back to the Telegram interface.

        Args:
            user_id (Optional[int]): Telegram ID to receive the scan results. 
            If None, sends to the group.
        """
        plc_ip: str = self._config.get('plc_connection', {}).get(
            'ip', '127.0.0.1')
        ip_prefix: str = ".".join(plc_ip.split(".")[:-1])
        
        self.telegram_service._send_notifications(
            "🚀 *Iniciando Varredura Multi-Thread*\n"
            f"Alvo: `{ip_prefix}.1` até `.20`",
            chat_id=user_id)

        try:
            active_hosts: List[str] = self._perform_network_scan(ip_prefix)

            msg: str = self.formatter._format_network_scan_result(
                active_hosts, ip_prefix)
            
            self._logger_plc.info(
                f"🛰️ Network scan completed by Admin. Hosts found: {len(active_hosts)}")
            return self.telegram_service._send_notifications(
                msg, chat_id=user_id)

        except Exception as e:
            self._logger_telegram.error(f"❌ Network scan failed: {e}")
            self.telegram_service._send_notifications(
                "❌ Falha ao executar varredura de rede.", chat_id=user_id)
  
    def _get_system_metrics(self) -> Dict[str, Any]:
        """
        Collects real-time hardware performance metrics from the host system.
        This method gathers CPU usage, RAM utilization, disk space, system 
        uptime, and attempts to retrieve CPU temperature if the operating 
        system is Linux.

        Returns:
            Dict[str, Any]: A dictionary containing:
                - 'cpu_percent' (float): Current CPU usage percentage.
                - 'ram' (svmem): Virtual memory statistics object.
                - 'disk' (sdiskusage): Disk usage statistics for the root directory.
                - 'cpu_temp' (Optional[float]): CPU temperature in Celsius, or None if unavailable.
                - 'uptime' (str): Formatted string of the host's total uptime.
        """
        metrics: Dict[str, Any] = {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'ram': psutil.virtual_memory(),
            'disk': psutil.disk_usage('/'),
            'cpu_temp': None,
            'uptime': self._get_host_uptime()}
        
        if platform.system().lower() == 'linux':
            try:
                temps = psutil.sensors_temperatures()
                for key in ['cpu_thermal', 'coretemp']:
                    if key in temps:
                        metrics['cpu_temp'] = temps[key][0].current
                        break
            except Exception:
                pass
        return metrics
    
    def _get_host_uptime(self) -> str:
        """
        Calculates the total time the host system has been running since the 
        last boot. This method uses the system's boot time provided by psutil 
        and compares it with the current epoch time to derive the duration in 
        hours and minutes.

        Returns:
            str: A formatted string representing the uptime (e.g., "48h 15m").
        """
        delta: float = time.time() - psutil.boot_time()
        hours, remainder = divmod(int(delta), 3600)
        minutes, _ = divmod(remainder, 60)
        
        if hours > 24:
            days, hours = divmod(hours, 24)
            return f"{days}d {hours}h {minutes}m"
        return f"{hours}h {minutes}m"

    def _send_resources_usage(self, user_id: Optional[int] = None) -> None:
        """
        Collects, formats, and sends the host system resource metrics to 
        Telegram. This method serves as the main handler for resource 
        monitoring. It orchestrates the data retrieval from the OS, formats it 
        into a human-readable message, and handles potential exceptions to 
        ensure the bot remains stable.

        Args:
            user_id (Optional[int]): Telegram ID to receive the resource usage 
                report.

        Returns:
            Any: The result of the Telegram notification (usually a message 
            object or None).
        """
        try:
            metrics: Dict[str, Any] = self._get_system_metrics()
            
            self._logger_telegram.debug(
                "📊 System metrics dispatched to Telegram.")
            
            msg: str = self.formatter._format_system_resources(metrics)
            
            return self.telegram_service._send_notifications(
                msg, chat_id=user_id)

        except Exception as e:
            self._logger_telegram.error(
                f"❌ Critical failure collecting system metrics: {e}")
            self.telegram_service._send_notifications(
                "❌ *Erro de Hardware*\nNão foi possível ler as métricas do processador.",
                chat_id=user_id)
            
   
    def _send_production_cycles(self, user_id: Optional[int] = None) -> None:
        """
        Filters current telemetry cache for production counters and sends a 
        report.This method scans the internal cache for topics or labels 
        containing specific production keywords (e.g., 'cycle', 'ciclo'). It 
        then aggregates these values and dispatches a formatted operational 
        report to Telegram. Any exceptions during this process are caught and 
        logged, with a user-friendly error message sent to the administrator 
        if the report generation fails.

        Args:
            user_id (Optional[int]): Telegram ID to receive the production 
                report.

        Returns:
            Any: The result of the Telegram notification (usually a message 
            object or None).
        """
        keywords: List[str] = ['count', 'contagem', 'cycle', 'ciclo',
                               'qtd', 'total', 'producao']
        
        cycle_data: Dict[str, Any] = {}

        for topic, data in self._current_values_cache.items():
            label: str = data.get('label', '')
            
            is_cycle_tag: bool = any(
                k in topic.lower() or k in label.lower() for k in keywords)
            
            if is_cycle_tag:
                display_name: str = label if label else topic.split('/')[-1]
                cycle_data[display_name] = data.get('value')

        try:
            self._logger_mqtt.info(
                f"📊 Production report generated with {len(cycle_data)} tags.")
            
            msg: str = self.formatter._format_cycles_report(cycle_data)

            return self.telegram_service._send_notifications(
                msg, chat_id=user_id)            

        except Exception as e:
            self._logger_telegram.error(
                f"❌ Failed to generate production report: {e}")
            self.telegram_service._send_notifications(
                "❌ Erro ao processar dados de produção.", 
                chat_id=user_id)
    
    def _toggle_maintenance_mode(self) -> bool:
        """
        Switches the system's maintenance state between active and inactive.
        When maintenance mode is active, the bot typically suppresses automated 
        alerts or PLC write commands to allow for safe physical intervention 
        on the industrial hardware.

        Returns:
            bool: The new state of the maintenance mode (True for Active, 
                False for Inactive).
        """
        current: bool = getattr(self, 'maintenance_active', False)
        self.maintenance_active = not current
        return self.maintenance_active

    def _maintenance_mode(self, user_id: Optional[int] = None) -> Any:
        """
        Handles the high-level logic for toggling the system's maintenance state.
        This method orchestrates the state change, generates the appropriate 
        notification for the Telegram interface, and logs the event for 
        audit purposes.

        Args:
            user_id (Optional[int]): Telegram ID to receive the maintenance 
                mode update.

        Returns:
            Any: The result of the Telegram notification (usually a message 
                object or None).
        """
        is_active: bool = self._toggle_maintenance_mode()
        
        status_txt: str = "ATIVADO" if is_active else "DESATIVADO"
        self._logger_telegram.info(f"🛠️ Maintenance mode {status_txt} via bot.")

        msg: str = self.formatter._format_maintenance_toggle(is_active)
        return self.telegram_service._send_notifications(msg, chat_id=user_id)
    
    def _calculate_maintenance_status(self) -> List[Dict[str, Any]]:
        """
        Calculates wear levels and remaining life for configured maintenance 
        targets. This method compares current PLC counter values from the 
        telemetry cache against predefined limits in the configuration. It 
        computes the percentage of life used and the remaining cycles/units 
        until maintenance is required.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, each containing:
                - 'label' (str): Human-readable name of the equipment/sensor.
                - 'current' (Union[int, float]): The current value from the PLC.
                - 'limit' (Union[int, float]): The threshold for maintenance.
                - 'percent' (float): Percentage of the limit reached (0-100+).
                - 'remaining' (Union[int, float]): Units left before reaching the limit.
        """
        results: List[Dict[str, Any]] = []
        targets: List[Dict[str, Any]] = self._config.get(
            'maintenance_targets', [])
        
        for target in targets:
            topic: str = target.get('topic')
            limit: Union[int, float] = target.get('limit', 0)
            
            cache_entry: Dict[str, Any] = self._current_values_cache.get(topic)
            
            if not cache_entry:
                self._logger_plc.warning(
                    f"⚠️ Maintenance target '{topic}' not founded on active "
                    "monitoring.")

                continue

            current_val: Union[int, float] = cache_entry.get('value', 0)
            percent_used: float = round(
                (current_val / limit * 100), 2) if limit > 0 else 0
            remaining: Union[int, float] = max(0, limit - current_val)
            
            results.append({
                'label': target.get(
                    'label', cache_entry.get('label', 'Equipamento')),
                'current': current_val,
                'limit': limit,
                'percent': percent_used,
                'remaining': remaining})
            
        return results

    def _next_maintenance(self, user_id: Optional[int] = None) -> Any:
        """
        Orchestrates the maintenance prediction workflow and notifies the user.
        This method triggers the calculation of wear levels for all configured 
        targets, processes the results into a human-readable report, and 
        dispatches the final dashboard to the Telegram interface.

        Args:
            user_id (Optional[int]): Telegram ID to receive the maintenance 
                report.

        Returns:
            Any: The result of the Telegram notification (usually a message 
                object or None).
        """
        try:            
            data: List[Dict[str, Any]] = self._calculate_maintenance_status()            
            self._logger_plc.info(
                f"📋 Maintenance report generated for {len(data)} itens.")
            msg: str = self.formatter._format_maintenance_report(data)            
            return self.telegram_service._send_notifications(msg, chat_id=user_id)
        except Exception as e:
            self._logger_telegram.error(
                f"❌ Fail to process maintenance command: {e}")
            self.telegram_service._send_notifications(
                "❌ *Erro de Processamento*\nNão foi possível calcular os níveis de desgaste.",
                chat_id=user_id)
    
    def _send_boolean_commands_menu(self, user_id: Optional[int] = None) -> None:
        """
        Identifies available boolean/pulse commands and sends a control panel menu.
        
        This method filters the configuration for interactive variables and 
        presents them as an inline keyboard in Telegram for remote PLC control.

        Args:
            user_id (Optional[int]): Telegram ID to receive the control panel menu.
        """
        try:
            all_commands: List[Dict[str, Any]] = self._config.get('commands', [])
            boolean_vars: List[Dict[str, Any]] = [
                v for v in all_commands 
                if v.get('type') in ('bool', 'pulse')]

            menu_data: Dict[str, Any] = self.formatter._format_commands_menu(
                boolean_vars)
            
            self._logger_telegram.info(
                f"🕹️ Control panel sent to user {user_id} with "
                f"{len(boolean_vars)} options.")

            return self.telegram_service._send_notifications(
                message=menu_data["text"], chat_id=user_id,
                keyboard=menu_data["keyboard"])

        except Exception as e:
            self._logger_telegram.error(
                f"❌ Error generating control panel: {e}")
            self.telegram_service._send_notifications(
                "❌ *Falha no Sistema*\nNão foi possível carregar o menu de comandos.",
                chat_id=user_id)
    
    def _get_friendly_name(self, var_name: str) -> str:
        """
        Translates a technical variable name into its descriptive 
        human-readable name. This method searches through the command 
        configurations in the YAML file to find a matching 'variable' key. If 
        found, it returns the 'description'; otherwise, it falls back to the 
        original technical name.

        Args:
            var_name (str): The technical name of the variable as defined in 
            the PLC/Config.

        Returns:
            str: The descriptive name for UI display or the original var_name 
            if not found.
        """
        for item in self._config.get('monitoring_list', []):
            if item.get('variable') == var_name:
                return item.get('description', var_name)
        
        for item in self._config.get('commands', []):
            if item.get('variable') == var_name:
                return item.get('description', var_name)
            
        return var_name

    def _get_dynamic_heartbeat_pou(self) -> str:
        """
        Identifies the most suitable POU for heartbeat/control operations.
        This method dynamically searches the PLC's POU cache for any POU names 
        containing "GVL", which are commonly used for global variables. If 
        found, it returns the first match; otherwise, it defaults to 
        "GVL_telegram". This allows the system to adapt to different PLC 
        programming structures without hardcoding a specific POU name.

        Returns:
            str: The name of the POU to be used for heartbeat and control 
                variable access.
        """
        pous: List[str] = list(self.plc._pou_node_cache.keys())
        gvl_pous: List[str] = [p for p in pous if "GVL" in p.upper()]
        return gvl_pous[0] if gvl_pous else "GVL_telegram"
    
    def _execute_physical_pulse(
            self, pou_name: str, var_name: str, initial_value: Any) -> bool:
        """
        Performs the physical state toggle on the PLC (Active -> Delay -> Restore).
        Crucial for simulating physical push-buttons.
        
        Args:
            pou_name (str): PLC POU name.
            var_name (str): PLC variable name.
            initial_value (Any): The original value to restore after the pulse.
            
        Returns:
            bool: True if both write operations succeeded.
        """
        active_value: bool = not bool(initial_value)
        
        try:
            if self.plc._set_value(pou_name, var_name, active_value):
                time.sleep(0.5)
                return self.plc._set_value(pou_name, var_name, initial_value)
            return False
        except Exception as e:
            self._logger_plc.error(
                f"❌ Physical pulse false of '{var_name}': {e}")
            return False
    
    def _send_plc_pulse(self, var_name: str, pou_name: str,
                        target_id: Optional[int] = None) -> bool:
        """
        Orchestrates a safety-checked pulse command to the PLC. Validates PLC 
        health, toggles the target variable, logs the operation, and notifies 
        the user via Telegram. Uses a reentrant lock for thread safety.

        Args:
            var_name (str): Technical name of the variable to pulse.
            pou_name (str): The POU where the variable is located.
            target_id (Optional[int]): Telegram ID to receive a private 
                notification about the pulse execution. If None, sends to 
                the group.

        Returns:
            bool: True if the pulse was fully executed and verified, 
                False otherwise.
        """
        hb_pou: str = self._get_dynamic_heartbeat_pou()
        friendly_name: str = self._get_friendly_name(var_name)

        with self.plc_lock:
            try:
                if not self.plc._is_plc_healthy(
                    heartbeat_pou=hb_pou, heartbeat_var="ui_heartbeat_plc"):
                    self._logger_plc.error(
                        "🚫 ABORTED PULSE: STOP or Unknown "
                        f"PLC for {var_name}.")
                    return False
                
                initial_value: Any = self.plc.get_value(pou_name, var_name)
                if initial_value is None:
                    return False

                success: bool = self._execute_physical_pulse(
                    pou_name, var_name, initial_value)

                if success:
                    if target_id:
                        msg: str = self.formatter._format_pulse_execution(
                            friendly_name, pou_name, bool(initial_value))
                        self.telegram_service._send_notifications(
                            msg, chat_id=target_id)
                    
                    self._logger_plc.info(
                        f"✅ PULSE successful: {friendly_name} "
                        f"({var_name}) at {pou_name}.")
                    return True
                
                return False

            except Exception as e:
                self._logger_plc.error(
                    f"❌ Critical error to send pulse for '{var_name}': {e}")
                return False

    def _toggle_boolean_variable(
            self, var_name: str, pou_name: str,
            user_id: Optional[int] = None) -> bool:
        """
        Toggles a PLC boolean variable and verifies the change (Closed-loop control).        
        This method reads the current state, inverts it, and writes the new value.
        It includes a verification step after a short delay to ensure the PLC 
        accepted the change (detecting physical interlocks or logic overrides).

        Args:
            var_name (str): Technical name of the boolean variable.
            pou_name (str): The POU where the variable is located.
            user_id (Optional[int]): Telegram ID to receive a private 
                notification about the toggle execution. If None, sends to 
                the group.

        Returns:
            bool: True if the state was successfully toggled and verified.
        """
        hb_pou: str = self._get_dynamic_heartbeat_pou()
        friendly_name: str = self._get_friendly_name(var_name)

        if not self.plc._is_plc_healthy(
            heartbeat_pou=hb_pou, heartbeat_var="ui_heartbeat_plc"):
            self._logger_plc.error(
                f"🚫 ABORTED TOGGLE: STOP or Unknown PLC for {var_name}.")
            return False

        with self.plc_lock:
            try:
                current_val: Any = self.plc.get_value(pou_name, var_name)
                if current_val is None:
                    return False

                new_value: bool = not bool(current_val)

                if not self.plc._set_value(pou_name, var_name, new_value):
                    return False

                time.sleep(0.3)
                check_val: Any = self.plc.get_value(pou_name, var_name)
                
                verified: bool = (
                    check_val is not None and bool(check_val) == new_value)

                if verified:
                    self._logger_plc.info(
                        f"🔄 Toggle OK: {friendly_name}({var_name}) -> {new_value}")
                else:
                    self._logger_plc.warning(
                        f"⚠️ Toggle sent to {friendly_name}({var_name}), "
                        "but value reverted or failed to "
                        "change (Interlock/Safety Logic?)")

                if user_id:
                    msg: str = self.formatter._format_toggle_status(
                        friendly_name, new_value, verified)
                    self.telegram_service._send_notifications(
                        msg, chat_id=user_id)

                return verified

            except Exception as e:
                self._logger_plc.error(
                f"❌ Error during toggle on '{var_name}': {e}")
                return False
    
    def _send_help_message(
            self, user_id: int, chat_id: Optional[int] = None) -> Any:
        """
        Sends the system help guide and resets user interaction state. If the 
        user had any pending multi-step interactions, they will be cleared to 
        prevent confusion. The help message content is dynamically generated 
        based on the user's admin status and the current maintenance mode of 
        the system.

        Args:
            user_id (int): Telegram ID of the user requesting help.
            chat_id (Optional[int]): Telegram chat ID to send the help message 
                to. If None, sends to the user's private chat.
        Returns:
            Any: The result of the Telegram notification (usually a message 
                object or None).
        """
        if user_id in self._user_states:
            del self._user_states[user_id]
            self._logger_telegram.info(
                f"State reset for user {user_id} via Help.")

        authorized_users: List[int] = self._config.get(
            'telegram_connection', {}).get('admin_ids', [])
        is_admin: bool = user_id in authorized_users

        maintenance_active: bool = getattr(self, 'maintenance_active', False)
        msg: str = self.formatter._format_help_message(
            is_admin, maintenance_active)

        target: int = chat_id if chat_id else user_id
        return self.telegram_service._send_notifications(msg, chat_id=target)
    
    def _create_log_archive(self, zip_base_path: Path) -> bool:
        """
        Compresses the logs directory into a ZIP file. This method checks if 
        the logs directory exists and contains files before attempting to 
        create an archive. If the directory is empty or missing, it returns 
        False to indicate that there are no logs to archive. If logs are 
        present, it creates a ZIP file at the specified base path and returns 
        True upon successful creation.

        Args:
            zip_base_path (Path): The base path (without extension) where the 
                ZIP file should be created.
        Returns:
            bool: True if the ZIP archive was created successfully, False if 
                there were no logs to archive.
        """
        if not self.LOGS_DIR.exists() or not any(self.LOGS_DIR.iterdir()):
            return False
            
        shutil.make_archive(str(zip_base_path), 'zip', str(self.LOGS_DIR))
        return True
    
    def _get_authorized_email(
            self, user_id: str, user_name: str) -> Optional[str]:
        """
        Validates if the user has permission and returns the mapped email.
        If the user ID is not found in the mapping, it logs a warning and 
        sends a denial message via Telegram.

        Args:
            user_id (str): The Telegram ID of the user requesting logs.
            user_name (str): The name of the user requesting logs.

        Returns:
            Optional[str]: The mapped email address if the user is authorized, 
                otherwise None.
        """
        mapping: Dict[str, str] = self._config.get('allowed_log_emails', {})
        email: Optional[str] = mapping.get(user_id)
        
        if not email:
            self._logger_email.warning(
                "🚫 Unauthorized log access attempt: "
                f"{user_name} (ID: {user_id})")
            msg: str = self.formatter._format_log_status("denied", user_name)
            self.telegram_service._send_notifications(msg, chat_id=user_id)
            return None
        return email
    
    def _execute_email_dispatch(
            self, recipient: str, user_name: str, file_path: Path) -> bool:
        """
        Bridge between content formatter and Email Service. Prepares the email 
        content and sends the logs archive. This method retrieves the PLC name 
        from the configuration to personalize the email content. It then uses 
        the EmailService to send the email with the logs attached. The result 
        of the email sending operation is returned to indicate success or 
        failure, which can be used for further UI notifications and logging.

        Args:
            recipient (str): The email address to send the logs to.
            user_name (str): The name of the user requesting logs.
            file_path (Path): The path to the ZIP file containing the logs.

        Returns:
            bool: True if the email was sent successfully, False otherwise.
        """
        plc_name: str = self._config.get(
            'plc_connection', {}).get('name', 'Sistema_Industrial')
        
        email_data: Dict[str, str] = self.formatter._prepare_log_email_content(
            user_name, plc_name)
        
        return self.email_service.send_email(
            recipient=recipient, subject=email_data["subject"],
            body=email_data["body"], attachment_path=str(file_path))
    
    def _send_logs_command(self, user_info: Dict[str, Any]) -> None:
        """
        Orchestrates the log backup process, emailing a ZIP archive to authorized users.
        This method validates the user's ID against an allowed email mapping, 
        compresses current logs, sends them via the EmailService, and cleans up 
        temporary files and old logs upon success.

        Args:
            user_info (Dict[str, Any]): Dictionary containing user 'id' and 'name'.

        Returns:
            Any: Result of the Telegram notification.
        """
        user_id: str = str(user_info.get('id'))
        user_name: str = user_info.get('name', 'User')
        
        target_email: Optional[str] = self._get_authorized_email(
            user_id, user_name)
        if not target_email:
            return

        self.telegram_service._send_notifications(
            self.formatter._format_log_status("processing"), chat_id=user_id)

        zip_path: Path = self.BASE_DIR / f"logs_backup_{int(time.time())}"
        zip_file: Path = Path(f"{zip_path}.zip")

        try:
            if not self._create_log_archive(zip_path):
                self.telegram_service._send_notifications(
                    self.formatter._format_log_status(
                        "no_logs"), chat_id=user_id)
                return

            success: bool = self._execute_email_dispatch(
                target_email, user_name, zip_file)
            
            status_key: str = "success" if success else "error"
            detail: str = self.formatter._mask_email(
                target_email) if success else ""
            
            return self.telegram_service._send_notifications(
                self.formatter._format_log_status(status_key, detail), 
                chat_id=user_id)

        finally:
            self._cleanup_temp_file(zip_file)
    
    def _cleanup_temp_file(self, file_path: Path) -> None:
        """
        Removes the temporary ZIP file safely. This method checks if the file 
        exists before attempting deletion and logs any exceptions that occur 
        during the cleanup process. It ensures that temporary files do not 
        accumulate on the disk after email dispatch operations, maintaining a 
        clean working environment for the application.

        Args:
            file_path (Path): The path to the temporary file that should be 
                deleted.
        """
        try:
            if file_path.exists():
                file_path.unlink()
        except Exception as e:
            self._logger_plc.error(
                f"⚠️ Fail to delete temporary file '{file_path}': {e}")
    
    def _collect_log_files(
            self, source: Union[Path, List[Path], str]) -> List[Path]:
        """
        Normalizes input and returns a list of Path objects. This method 
        accepts a single Path, a list of Paths, or a directory path as a string.
        If a directory is provided, it collects all .txt and .log files 
        within it.

        Args:
            source (Union[Path, List[Path], str]): The input source which can be:
                - A single Path object
                - A list or tuple of Path objects
                - A directory path as a string

        Returns:
            List[Path]: A list of Path objects representing the log files.

        """
        if isinstance(source, (list, tuple)):
            return [Path(f) for f in source]
        
        source_path: Path = Path(source)
        if source_path.is_dir():
            return list(
                source_path.glob("*.txt")) + list(source_path.glob("*.log"))
        
        return [source_path]

    def _wipe_log_file(self, file_path: Path) -> None:
        """
        Clears the content of a specific file and adds a header. This method 
        checks if the file exists and is a regular file before attempting to 
        write. It opens the file in write mode, which automatically truncates 
        it, and then writes a header line indicating that the log has been 
        reset, along with a timestamp. This is used during log rotation to 
        start fresh logs while keeping a record of when the reset occurred.

        Args:
            file_path (Path): The path to the log file that should be cleared.
        """
        if not file_path.exists() or not file_path.is_file():
            return

        with open(file_path, 'w', encoding='utf-8') as f:
            timestamp = time.strftime('%d/%m/%Y %H:%M:%S')
            f.write(f"--- Log resetado após backup em {timestamp} ---\n")
    
    def _rotate_logs(self, log_source: Union[Path, List[Path], str],
                     target_id: Optional[int] = None) -> None:
        """
        Orchestrates log rotation to start a new monitoring cycle.
        This method collects the relevant log files based on the provided 
        source, clears their content while adding a reset header, and sends a 
        notification to the user about the rotation status. It includes error 
        handling to ensure that any issues during the log rotation process are 
        logged appropriately, and the user is informed if the operation fails. 
        This helps maintain organized log files and provides feedback to 
        administrators about the system's logging status.

        Args:
            log_source (Union[Path, List[Path], str]): The source for log 
                files to rotate, which can be:
                - A single Path object
                - A list or tuple of Path objects
                - A directory path as a string
            target_id (Optional[int]): Telegram ID to receive a notification 
                about the rotation. If None, sends to the group.    
        """
        try:
            log_files: List[Path] = self._collect_log_files(log_source)
            
            if not log_files:
                self._logger_plc.warning(
                    "⚠️ None log file founded for rotation.")
                return

            for file_path in log_files:
                self._wipe_log_file(file_path)

            self._logger_plc.info(
                f"🔄 Rotation concluded for {len(log_files)} files.")
            
            if target_id:
                msg: str = self.formatter._format_log_rotation_message()
                return self.telegram_service._send_notifications(
                    msg, chat_id=target_id)

        except Exception as e:
            self._logger_plc.error(f"❌ Critical fail in logs rotation: {e}")
    
    def _send_cancel_command(self, user_id: int) -> None:
        """
        Aborts any interactive process for the user. This method checks if the 
        user has an active state in the interaction flow and removes it if 
        present. It logs the cancellation event and sends a confirmation 
        message to the user. If the user did not have an active state, it logs 
        that information as well. This allows users to gracefully exit from 
        any multi-step interactions they may have initiated with the bot, 
        ensuring a better user experience and preventing confusion in the 
        conversation flow.

        Args:
            user_id (int): The Telegram ID of the user who wants to cancel the 
                operation.
        """
        try:
            was_in_conversation: bool = user_id in self._user_states
            
            if was_in_conversation:
                del self._user_states[user_id]
                self._logger_telegram.info(
                    f"🚫 Operation canceled by user {user_id}.")
            else:
                self._logger_telegram.debug(
                    f"ℹ️ Command '/cancel' received by {user_id}, "
                    "but hasn't active state.")

            msg: str = self.formatter._format_cancel_message()

            return self.telegram_service._send_notifications(
                msg, chat_id=user_id)

        except Exception as e:
            self._logger_telegram.error(
                f"❌ Error to process '/cancel' command for {user_id}: {e}")
    
    def _persist_config_to_disk(self, file_path: Path) -> bool:
        """
        Serializes the current configuration dictionary to the YAML file.
         This method ensures that the directory structure exists before writing
         and handles any exceptions that may occur during the file operation. 
         It provides feedback through logging and returns a boolean status to 
         indicate success or failure of the persistence operation.

        Args:
            file_path (Path): The path to the YAML file where the 
            configuration will be saved.

        Returns:
            bool: True if the file was saved successfully, False otherwise.
        """
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)

            with open(file_path, 'w', encoding='utf-8') as file:
                yaml.dump(self._config, file, default_flow_style=False, 
                    allow_unicode=True, sort_keys=False)
            
            self._logger_telegram.info(
                f"💾 Settings saved successfully in: {file_path.name}")
            return True
        except Exception as e:
            self._logger_telegram.error(
                f"❌ Error to write in disc ({file_path.name}): {e}")
            return False
    
    def _update_admin_list_memory(
            self, admin_id: int, action: str = "add") -> bool:
        """
        Manages the admin ID list in the configuration dictionary. This method 
        allows adding or removing an admin ID from the in-memory 
        configuration. It checks for duplicates when adding and verifies 
        existence when removing, providing appropriate logging for each case. 
        The method returns a boolean indicating whether the operation resulted 
        in a change to the list, which can be used to determine if the 
        configuration needs to be persisted to disk.

        Args:
            admin_id (int): The Telegram User ID to be added or removed from 
                the admin list.
            action (str): The action to perform, either "add" to include the 
                ID or "remove" to exclude it. Defaults to "add".

        Returns:
            bool: True if the list was modified (ID added or removed), False 
                if no change was made.    
        """
        telegram_cfg: dict = self._config.setdefault('telegram_connection', {})
        admin_list: list = telegram_cfg.setdefault('admin_ids', [])

        if action == "add":
            if admin_id in admin_list:
                return False
            admin_list.append(admin_id)
            return True

        elif action == "remove":
            if admin_id not in admin_list:
                self._logger_telegram.warning(
                    f"⚠️ ID {admin_id} not founded to remotion.")
                return False
            admin_list.remove(admin_id)
            return True
        
        return False
    
    def _save_new_admin_to_yaml(self, new_id: int) -> bool:
        """
        Persists a new Telegram administrator ID directly into the YAML 
        configuration file. This method updates the in-memory configuration 
        dictionary and then serializes the entire configuration back to the 
        disk. It ensures that newly added administrators retain their 
        privileges after a system restart.

        Args:
            new_id (int): The unique Telegram User ID to be added to the admin 
                list.

        Returns:
            bool: True if the file was successfully updated, False if the ID 
                already exists or if a filesystem error occurred.
        """
        if not self._update_admin_list_memory(new_id, action="add"):
            self._logger_telegram.debug(f"ℹ️ ID {new_id} it is admin.")
            return False

        config_path: Path = self.CONFIG_DIR / self._config_filename
        return self._persist_config_to_disk(config_path)
    
    def _remove_admin_from_yaml(self, admin_id: int) -> bool:
        """
        Removes a Telegram administrator ID from the YAML configuration file.
        This method updates the in-memory configuration dictionary by removing the 
        specified ID and then synchronizes those changes with the physical disk. 
        If the ID is not found in the current list, the operation is skipped.

        Args:
            admin_id (int): The unique Telegram User ID to be removed from the 
                authorized admin list.

        Returns:
            bool: True if the ID was found and successfully removed from the 
                file, False otherwise (ID not found or filesystem error).
        """
        if not self._update_admin_list_memory(admin_id, action="remove"):
            return False

        config_path: Path = self.CONFIG_DIR / self._config_filename
        return self._persist_config_to_disk(config_path)

    def _determine_pou_hierarchy(self, var_name: str) -> List[str]:
        """
        Determines the ordered search priority for ALL available POUs 
        based on variable naming conventions. Logic for codesys projects 
        (image_0.png structure): 
        - Prefixes like 'i_', 'o_', 'ri', 'g_' are treated as global.
        - Other variables prioritize logic POUs.
        
        Args:
            var_name (str): The PLC variable name.
            
        Returns:
            List[str]: A full list of POU names ordered by search priority.
        """
        discovered_pous: List[str] = list(self.plc._pou_node_cache.keys())
        
        if not discovered_pous:
            return ["PLC_PRG", "GVL"]
        
        gvls: List[str] = [p for p in discovered_pous if "GVL" in p.upper()]
        logic_pous: List[str] = [p for p in discovered_pous if p not in gvls]
        
        var_lower: str = var_name.lower()
        is_global_candidate: bool = var_lower.startswith(
            ("i_", "o_", "ri", "g_", "gvl_"))

        sorted_gvls: List[str] = sorted(gvls)
        sorted_logic: List[str] = sorted(logic_pous)

        full_hierarchy: List[str] = []

        if is_global_candidate:
            full_hierarchy = sorted_gvls + sorted_logic
        else:
            full_hierarchy = sorted_logic + sorted_gvls

        self._logger_plc.debug(
            f"🔍 Hierarchy for '{var_name}': {full_hierarchy}")

        return full_hierarchy

    def _update_caches(
            self, topic: str, label: str, value: Any, pou: str) -> None:
        """
        Updates history and current value caches with the new PLC data.
        This method manages two separate caches: one for historical data 
        (a deque with a max length for each topic) and another for the most 
        recent value of each topic. It ensures that only numeric values are 
        stored in the history cache, while all values are updated in the 
        current values cache along with their metadata (label, POU, timestamp). 
        This structured caching allows for efficient retrieval of both current 
        states and historical trends for the monitored variables.

        Args:
            topic (str): The MQTT topic associated with the variable.
            label (str): The human-readable description of the variable.
            value (Any): The latest value read from the PLC.
            pou (str): The POU where the variable was found, for metadata 
                purposes.   
        """
        if topic not in self.sensor_mapping:
            return
        
        timestamp: str = datetime.now().strftime('%H:%M:%S')
        
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if topic not in self._history_cache:
                self._history_cache[topic] = deque(maxlen=100)
            self._history_cache[topic].append((timestamp, value))
        
        self._current_values_cache[topic] = {
            "label": label, "value": value,
            "pou": pou, "timestamp": timestamp}
    
    def _get_initial_hierarchy(self, config: Dict[str, Any]) -> List[str]:
        """
        Determines the initial POU hierarchy for a variable, prioritizing a 
        static POU if specified. This method checks if the variable 
        configuration includes a specific POU. If it does, that POU is placed 
        at the top of the hierarchy list, followed by the dynamically 
        determined hierarchy of POUs based on naming conventions. If no static 
        POU is specified, it simply returns the full hierarchy as determined 
        by the variable name. This allows for flexible configuration while 
        still providing a robust fallback mechanism for variable lookup across 
        multiple POUs.

        Args:
            config (Dict[str, Any]): The configuration dictionary for the 
                variable, which may include a 'pou' key for static POU 
                assignment.

        Returns:
            List[str]: An ordered list of POU names to be used for variable 
                lookup, with the static POU prioritized if specified. 
        """
        var_name: str = config['variable']
        hierarchy: List[str] = self._determine_pou_hierarchy(var_name)
        
        static_pou: str = config.get('pou')
        if static_pou:
            return [static_pou] + [p for p in hierarchy if p != static_pou]
        return hierarchy
    
    async def _perform_safe_plc_read(
            self, var_name: str, hierarchy: List[str],
            state: Dict[str, int]) -> Tuple[Any, str]:
        """
        Executes a thread-safe read with failover across the POU hierarchy.
        This method runs a synchronous PLC read operation in an executor to 
        avoid blocking the event loop. It first attempts to read from the POU 
        that was successful in the last cycle (using the index stored in the 
        state). If that read fails (returns None), it iterates through the 
        entire hierarchy of POUs to find the variable. If it finds a valid 
        value, it updates the state index to that POU for faster access in the 
        next cycle. If all attempts fail, it returns None along with the last 
        attempted POU for logging purposes. This approach ensures that the 
        monitoring task can recover from changes in variable locations within 
        the PLC without manual reconfiguration.

        Args:
            var_name (str): The name of the variable to read from the PLC.
            hierarchy (List[str]): An ordered list of POU names to attempt 
                the read from.
            state (Dict[str, int]): A dictionary containing the current index 
                of the POU that worked in the last cycle, allowing for 
                prioritized access.

        Returns:
            Tuple[Any, str]: A tuple containing the value read from the PLC 
            (or None if not found) and the name of the POU where it was found 
            or last attempted.
        """
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

        def _search_in_plcs():
            """
            Searches for the variable across the POU hierarchy with failover.
            """
            start_idx: int = state["current_idx"]
            
            with self.plc_lock:
                val: Any = self.plc.get_value(hierarchy[start_idx], var_name)
                if val is not None:
                    return val, hierarchy[start_idx]

            for i, pou in enumerate(hierarchy):
                if i == start_idx: continue
                with self.plc_lock:
                    val: Any = self.plc.get_value(pou, var_name)
                    if val is not None:
                        state["current_idx"] = i
                        return val, pou
            return None, hierarchy[start_idx]

        return await loop.run_in_executor(None, _search_in_plcs)
    
    def _clean_numeric_value(self, value: Any) -> Union[int, float, bool, Any]:
        """
        Sanitizes and converts raw PLC data into clean Python numeric types.
        This method handles decimal separator conversion (comma to dot), 
        ensures booleans are preserved, and attempts to cast strings/objects 
        into floats or integers. If conversion fails, the original value is 
        returned as a fallback.

        Args:
            value (Any): The raw value received from the PLC or another service.

        Returns:
            Union[int, float, bool, Any]: 
                - bool: If the input is a boolean.
                - int: If the value is a whole number.
                - float: If the value has decimal places.
                - Any: The original value if numeric conversion is impossible.
        """
        if isinstance(value, bool):
            return value
        try:
            num: Union[int, float] = float(str(value).replace(',', '.'))
            return int(num) if num.is_integer() else num
        except (ValueError, TypeError):
            return value 
    
    def _process_monitoring_result(
            self, raw_val: Any, config: Dict[str, Any], active_pou: str) -> None:
        """
        Sanitizes, caches, and publishes the PLC data. This method takes the 
        raw value read from the PLC, cleans it to ensure it's in a proper 
        numeric format, updates the internal caches for both historical data 
        and current values, and then formats and publishes the value to the 
        MQTT topic. It uses the variable's configuration to determine the 
        appropriate topic and label for logging and publishing. This 
        encapsulated processing ensures that all necessary steps are taken to 
        maintain accurate state and provide real-time updates to any 
        subscribed MQTT clients.

        Args:
            raw_val (Any): The original value read from the PLC before cleaning.
            config (Dict[str, Any]): The configuration for the variable.
            active_pou (str): The POU where the value was found.

        """
        topic: str = config['topic']
        label: str = config['description']
        
        val: Union[int, float, bool, Any] = self._clean_numeric_value(raw_val)
        
        self._update_caches(topic, label, val, active_pou)
        
        formatted: str = self.formatter._format_value(val)
        self.mqtt.publish_message(topic, f"{label}: {formatted}")
    
    async def variable_monitoring_task(
            self, var_config: Dict[str, Any]) -> None:
        """
        Asynchronous loop that monitors a specific PLC variable. This task 
        reads values from the PLC using an executor to avoid blocking the 
        event loop, manages automatic failover between POUs, updates internal 
        caches for history/UI, and publishes results via MQTT.

        Args:
            var_config (Dict[str, Any]): Configuration for this variable 
                (name, topic, interval, description).
        """
        var_name: str = var_config['variable']
        interval: float = float(var_config['interval'])

        pou_hierarchy: List[str] = self._get_initial_hierarchy(var_config)
        state: Dict[str, int] = {"current_idx": 0} 

        self._logger_plc.info(
            f"🚀 Monitorando: {var_name} | Intervalo: {interval}s")

        try:
            while True:
                try:
                    val, active_pou = await self._perform_safe_plc_read(
                        var_name, pou_hierarchy, state)

                    if val is not None:
                        self._process_monitoring_result(
                            val, var_config, active_pou)
                    else:
                        self._logger_plc.error(
                            f"⚠️ Lost signal: {var_name} not founded in PLC.")

                    await asyncio.sleep(interval)

                except (asyncio.CancelledError, BaseException):
                    raise
                except Exception as e:
                    self._logger_plc.error(
                        f"❌ Failed to monitor {var_name}: {e}")
                    await asyncio.sleep(interval * 2)

        except asyncio.CancelledError:
            self._logger_plc.info(f"🛑 Ended task monitoring: {var_name}")
            raise
    
    def _get_sensor_label(self, topic: str) -> str:
        """
        Searches for a description for the topic in the monitoring list.
        This method looks up the provided MQTT topic in the 'monitoring_list' 
        section of the configuration. If it finds a matching topic, it returns 
        the associated description. If no match is found, it defaults to 
        returning the topic itself as the label. This allows for more 
        user-friendly labels in alerts and logs while maintaining a fallback 
        to the raw topic name when no description is provided.

        Args:
            topic (str): The MQTT topic for which to find a human-readable label.

        Returns:
            str: The description associated with the topic if found, otherwise 
            the topic itself. 
        """
        monitoring_list: List[Dict[str, Any]] = self._config.get(
            'monitoring_list', [])
        for item in monitoring_list:
            if item.get('topic') == topic:
                return item.get('description', topic)
        return topic

    def _log_alert_configuration(
            self, topic: str, auto_action: Optional[Dict]) -> None:
        """
        Logs the status based on whether it's a simple alert or AutoAction. 
        This method checks if the alert rule includes an 'auto_action' 
        configuration. If it does, it logs that an AutoAction alert is 
        configured for the topic. If not, it logs that a simple MQTT alert is 
        configured. This provides clear and immediate feedback in the logs 
        about the nature of the alert rules that have been set up for each 
        topic, which can be helpful for debugging and monitoring purposes.
        
        Args:
            topic (str): The MQTT topic associated with the alert rule.
            auto_action (Optional[Dict]): The auto_action configuration if present, 
                which indicates that this alert has an automated response.
        """
        suffix: str = "+ AutoAction" if auto_action else ""
        self._logger_mqtt.info(f"🔔 MQTT alert {suffix} configured to: {topic}")
    
    def _register_single_alert_rule(self, rule: Dict[str, Any]) -> None:
        """
        Helper to process and bind a single rule to the MQTT service.
        This method takes an individual alert rule from the configuration, 
        extracts the necessary information, creates a partial callback 
        function with the appropriate parameters, and registers it with the 
        MQTT service. It also logs the configuration of the alert for 
        transparency. This encapsulation allows for cleaner code in the main 
        setup method and makes it easier to handle each rule independently, 
        including error handling for missing keys.

        Args:
            rule (Dict[str, Any]): A dictionary representing a single alert rule 
        """
        topic: str = rule['topic']
        sensor_label: str = self._get_sensor_label(topic)
        
        email_list: List[str] = rule.get('emails') or rule.get(
            'auto_action', {}).get('emails', [])

        callback: Callable = partial(
            self._alert_callback_handler, threshold=rule['threshold'],
            op_sign=rule['operator'], notify_via=rule['notify_via'],
            email_list=email_list, label=sensor_label,
            action_cfg=rule.get('auto_action'), trigger_topic=topic)

        self.mqtt.add_alert_rule(topic, None, callback)
        self.mqtt.subscribe_topic(topic)

        self._log_alert_configuration(topic, rule.get('auto_action'))
    
    def _setup_mqtt_alerts(self) -> None:
        """
        Parses and registers MQTT alert rules from the configuration file.
        This method retrieves the list of alert rules from the configuration, 
        validates that it is a list, and then iterates through each rule to 
        register it with the MQTT service. It uses a helper method to handle 
        the registration of each individual rule, which includes error handling 
        for missing keys. This setup allows for dynamic configuration of alerts 
        based on the YAML file without requiring code changes, making it easy 
        to add or modify alert rules as needed.
        """
        rules: List[Dict[str, Any]] = self._config.get('alert_rules', [])
        
        if not isinstance(rules, list):
            self._logger_mqtt.error("❌ 'alert_rules' in YAML must be a list.")
            return

        for rule in rules:
            try:
                self._register_single_alert_rule(rule)
            except KeyError as e:
                self._logger_mqtt.error(f"❌ Missing key in alert rule: {e}")
    
    def _update_mqtt_heartbeat(self) -> None:
        """
        Refreshes the last successful communication timestamp.
        This method should be called whenever a successful MQTT interaction occurs,
        such as receiving a message or successfully publishing. It updates the
        last successful communication timestamp. This timestamp is used by the 
        watchdog task to determine if the MQTT connection has been 
        unresponsive for too long and if a system restart is necessary.
        """
        self._last_mqtt_heartbeat = time.time()

    def _is_mqtt_threshold_exceeded(self) -> bool:
        """
        Checks if the duration since the last heartbeat is beyond safety limits.
        This method calculates the time elapsed since the last successful MQTT 
        interaction. If this duration exceeds the predefined maximum offline 
        time, it returns True, indicating that the MQTT connection is 
        considered unresponsive and that the watchdog should trigger a system 
        restart. Otherwise, it returns False, indicating that the connection 
        is still within acceptable limits.

        Returns:
            bool: True if the MQTT connection has been offline for too long, 
            False otherwise.
        """
        if self._last_mqtt_heartbeat == 0:
            return False
        
        time_offline: float = time.time() - self._last_mqtt_heartbeat
        return time_offline > self._max_offline_time

    async def _handle_mqtt_critical_failure(self) -> None:
        """
        Notifies administrators and executes an immediate system exit.
        This method is called when the MQTT connection has been unresponsive 
        for too long, as determined by the watchdog. It performs several 
        critical actions: it logs a detailed error message with the duration 
        of the offline period, sends an urgent alert message to the 
        administrators via Telegram, waits briefly to ensure the message is 
        sent, and then forces the system to exit. This exit is intended to 
        trigger a restart of the service by an external supervisor 
        (like systemd), allowing for recovery from the unresponsive state. The 
        method ensures that administrators are informed of the issue before 
        the restart occurs, providing context for the failure.
        """
        offline_secs: int = int(time.time() - self._last_mqtt_heartbeat)
        
        self._logger_mqtt.error(
            f"🚨 Watchdog: MQTT offline for {offline_secs}s. "
            f"Limit: {self._max_offline_time}s. Restarting system...")

        alert_msg: str = (
            "⚠️ *WATCHDOG CRÍTICO*\n"
            f"Conexão MQTT perdida por mais de {offline_secs}s.\n"
            "O serviço será reiniciado automaticamente.")

        try:
            self.telegram_service._send_notifications(alert_msg)
        except Exception as e:
            self._logger_mqtt.error(
                f"Cannot send notification via Telegram: {e}")

        await asyncio.sleep(2)
        
        os._exit(1)
    
    async def _mqtt_watchdog_task(self) -> None:
        """
        Monitors MQTT health and triggers recovery if thresholds are met.
        This asynchronous task runs in an infinite loop, periodically checking 
        the health of the MQTT connection by comparing the current time with 
        the last successful communication timestamp. If it detects that the 
        connection has been offline for longer than the allowed threshold, it 
        calls the handler to manage the critical failure. The task includes 
        error handling to log any exceptions that occur during its execution, 
        ensuring that issues within the watchdog itself are also recorded.
        """
        self._logger_mqtt.info("🐕 MQTT Watchdog active and monitoring.")

        while True:
            try:
                await asyncio.sleep(self._watchdog_interval)

                if getattr(self.mqtt, '_is_connected', False):
                    self._update_mqtt_heartbeat()
                    continue

                if self._is_mqtt_threshold_exceeded():
                    await self._handle_mqtt_critical_failure()

            except Exception as e:
                self._logger_mqtt.error(f"❌ Watchdog error: {e}")
                await asyncio.sleep(5)

    async def run(self) -> None:
        """
        Main orchestrator. Initializes and maintains all system tasks.
        This method performs the critical startup sequence, including 
        establishing connections to the PLC and MQTT broker, sending a 
        startup notification via Telegram, setting up MQTT alerts based on 
        the configuration, and then starting the monitoring engine which runs 
        all variable monitoring tasks concurrently. It includes error handling 
        to ensure that if critical connections cannot be established, the 
        system will not proceed to run, preventing it from entering an 
        unstable state. This method serves as the central point of control for 
        the entire monitoring system, coordinating the initialization and 
        execution of all components.
        """
        self._logger_plc.info("🎬 Initializing System Execution...")

        if not self._establish_initial_connections():
            raise RuntimeError(
                "Failed to establish critical connections during startup.")

        self._loop = asyncio.get_running_loop()
        self._setup_mqtt_alerts()
        self._notify_system_startup()

        try:
            await self._start_monitoring_engine()
        except Exception as e:
            self._logger_plc.critical(f"💥 Fatal engine failure: {e}")

    def _establish_initial_connections(self) -> bool:
        """
        Connects to PLC and MQTT, returning False if either fails.
        This method attempts to establish connections to both the PLC and the 
        MQTT broker. If the PLC connection fails, it logs a critical error and 
        returns False, preventing the system from starting. Similarly, if the 
        MQTT broker connection fails, it logs a critical error and returns 
        False. Only if both connections are successful does it return True, 
        allowing the system to proceed with its operations. This ensures that 
        the system does not run without its essential components being 
        properly connected, which could lead to errors and instability.

        Returns:
            bool: True if both connections are successful, False if either fails.
        """
        if not self.plc.connect():
            self._logger_plc.critical(
                "❌ Impossible to connect PLC. Aborting...")
            return False

        if not self.mqtt.connect_broker():
            self._logger_mqtt.critical(
                "❌ Impossible to connect MQTT Broker. Aborting...")
            return False
            
        return True

    def _notify_system_startup(self) -> None:
        """
        Sends the startup message via Telegram using config parameters.
        This method checks if both the PLC and MQTT connections are 
        established before attempting to send a startup notification. If 
        either connection is not established, it logs an error and skips 
        sending the notification to avoid false alerts. If both connections 
        are active, it retrieves the PLC connection details from the 
        configuration and sends a formatted startup message to the Telegram 
        service. It also logs whether the message was sent successfully or if 
        it failed, providing feedback on the notification process during 
        system startup.
        """
        if not self.plc._is_connected() or not self.mqtt._is_connected:
             self._logger_telegram.error(
                 "❌ Cannot notify startup: Critical services offline.")
             return
        
        plc_cfg: Dict[str, str] = self._config.get('plc_connection', {})
        success: bool = self.telegram_service._send_bot_startup_message(
            plc_name=plc_cfg.get('name', 'Unknown'),
            plc_ip=plc_cfg.get('ip', '0.0.0.0'))
        
        if success:
            self._logger_telegram.info("📢 Startup message sent to Telegram.")
        else:
            self._logger_telegram.error("❌ Failed to send startup message.")

    async def _start_monitoring_engine(self) -> None:
        """
        Initializes and executes all monitoring coroutines concurrently.
        This method retrieves the list of variables to monitor from the 
        configuration and creates an asynchronous task for each variable using 
        the `variable_monitoring_task` method. It also adds the MQTT watchdog 
        task to the list of tasks to be run concurrently. It logs the total 
        number of sensors being monitored and the activation of the watchdog. 
        The method then uses `asyncio.gather` to run all tasks concurrently, 
        allowing for efficient monitoring of multiple variables at their 
        specified intervals. It includes error handling to catch any 
        exceptions that occur during the execution of the tasks, logging 
        critical errors if the main loop fails unexpectedly. This method 
        serves as the core execution engine for the monitoring system, 
        coordinating the concurrent execution of all monitoring tasks and 
        ensuring that the system remains responsive and functional throughout 
        its operation.
        """
        monitoring_items: List[Dict[str, Any]] = self._config.get(
            'monitoring_list', [])
        
        if not monitoring_items:
            self._logger_mqtt.warning(
                "⚠️ Monitoring list is empty. No tasks to run.")
            return

        tasks: List[asyncio.Task] = [
            self.variable_monitoring_task(var) for var in monitoring_items]
        tasks.append(self._mqtt_watchdog_task())
        
        total_sensors: int = len(monitoring_items)
        self._logger_mqtt.info(
            f"🚀 System online. Monitoring {total_sensors} "
            "sensors + Watchdog active.")

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            self._logger_mqtt.info(
                "⚠️ System shutdown requested (CancelledError).")
        except Exception as e:
            self._logger_mqtt.error(f"❌ Critical error in main loop: {e}")
    
    def _shutdown_bot_system(self, user_id: Optional[int] = None) -> None:
        """
        Performs a controlled shutdown of the entire monitoring system.
        This method is designed to be called when a shutdown command is received
        via Telegram. It executes a series of steps to ensure a graceful 
        shutdown of the system.

        Args:
            user_id (Optional[int]): The Telegram User ID of the administrator 
            who initiated the shutdown, used for logging and notification 
            purposes.
        """
        self._send_final_shutdown_notification()

        self._cleanup_telegram_session()

        self._rotate_logs(self.LOGS_DIR, target_id=user_id)
        
        self._logger_telegram.warning(
            "⚠️ System shutdown sequence completed. Terminating...")
        
        time.sleep(1.5)
        os._exit(0)

    def _send_final_shutdown_notification(self) -> None:
        """
        Retrieves the formatted message and sends it via Telegram service.
        This method uses the formatter to create a shutdown message that 
        includes relevant information about the system's state at the time of 
        shutdown. It then sends this message to the configured Telegram chat 
        ID using the Telegram service. This provides administrators with a 
        clear notification that the system is shutting down, along with any 
        pertinent details included in the formatted message. 
        """
        closing_msg: str = self.formatter._format_shutdown_message()
        
        self.telegram_service._send_notifications(
            closing_msg, 
            chat_id=self.telegram_service._chat_id)

    def _cleanup_telegram_session(self) -> None:
        """
        Clears pending updates and signals the polling loop to stop.
        This method is responsible for cleaning up the Telegram session before 
        shutdown. It calls the Telegram service's method to clear any pending 
        updates, ensuring that there are no leftover messages or commands that 
        could cause issues when the system is restarted. Additionally, it sets 
        a flag to signal the polling loop in the Telegram service to stop 
        running, allowing for a graceful exit of the thread that handles 
        Telegram commands. This cleanup process helps maintain a clean state 
        for the Telegram bot and prevents potential conflicts or errors when 
        the system is restarted.
        """
        self._logger_telegram.info("🧹 Cleaning up Telegram session...")
        self.telegram_service._clear_pending_updates()
        self.telegram_service._pool_commands_running = False