import unittest
import logging
from unittest.mock import patch, MagicMock, ANY, call, mock_open, AsyncMock
from pathlib import Path
import time
from collections import deque
import io
import asyncio

# Disable logs during tests to keep console clean
logging.disable(logging.CRITICAL)

class StopLoopException(BaseException):
    pass


class TestIndustrialMonitoringSystem(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """
        Environment setup for each test case.
        Mocks all external IO dependencies.
        """
        self.mock_config = {
            'plc_connection': {
                'ip': '127.0.0.1',
                'name': 'TestPLC',
                'pou_name': 'PRG',
                'remote_pou_name': 'GVL_telegram',
                'root': 'Root'},
            'mqtt_connection': {
                'broker': '127.0.0.1',
                'port': 1883},
            'telegram_connection': {
                'bot_token': '123456789:ABCDEF',
                'bot_chat_id': '-987654321',
                'admin_ids': [111222333, 444555666]
            },
            'email': {
                'sender': 'test@gmail.com',
                'password': 'abcd efgh ijkm lmno'
            },
            'commands': [
                {'variable': 'ri_start_button',
                 'description': 'Botão Remoto Ligar',
                 'type': 'pulse'}
            ],
            'monitoring_list': [
                {'variable': 'test_var', 'topic': 'test/topic',
                 'description': 'Desc', 'interval': 0.1}],
            'alert_rules': [
                {'topic': 'test/topic', 'threshold': 10,
                 'operator': '>', 'notify_via': ['telegram', 'email'],
                 'emails': ['user1@test.com'], 'auto_action': {'variable': 'valve_off', 'type': 'toggle'}}]
        }

        # Target for patches (adjust 'src.monitoring_main' to your actual file path)
        self.target_path = 'src.monitoring_main'

        with patch(f'{self.target_path}.setup_logging'), \
             patch(f'{self.target_path}.OPCUAClient'), \
             patch(f'{self.target_path}.MQTTManager'), \
             patch(f'{self.target_path}.TelegramBotService'), \
             patch(f'{self.target_path}.ConfigLoader._load_yaml', return_value=self.mock_config), \
             patch(f'{self.target_path}.IndustrialMonitoringSystem._setup_email_service'):
            
            from src.monitoring_main import IndustrialMonitoringSystem
            self.system = IndustrialMonitoringSystem("config.yaml")

            self.system.formatter = MagicMock()
            self.system.rules = MagicMock()
            self.system.email_service = MagicMock()
            self.system.telegram_service = MagicMock()
            self.system._logger_telegram = MagicMock()

    def test_initialization_success(self):
        """Tests if all internal attributes are correctly initialized."""
        self.assertEqual(self.system.maintenance_active, False)
        self.assertIn('test/topic', self.system.sensor_mapping)
        self.assertEqual(self.system.sensor_mapping['test/topic'], 'Desc')
        self.assertIsInstance(self.system._active_alerts, set)

    def test_history_cache_creation(self):
        """Verifies if the history deque is correctly allocated for topics."""
        self.assertIn('test/topic', self.system._history_cache)

        cache = self.system._history_cache['test/topic']
        self.assertEqual(cache.maxlen, 100)

    def test_paths_assignment(self):
        """Ensures system paths are instances of Path."""
        self.assertIsInstance(self.system.BASE_DIR, Path)
        self.assertIsInstance(self.system.CONFIG_DIR, Path)
        self.assertTrue(self.system.CONFIG_DIR.name == "config")
    
    def test_save_config_to_yaml_success(self):
        """Tests if configuration is correctly synced and saved."""
        with patch('src.monitoring_main.ConfigLoader._save_yaml', return_value=True) as mock_save:
            result = self.system._save_config_to_yaml()
            
            self.assertTrue(result)
            self.assertEqual(self.system.sensor_mapping['test/topic'], 'Desc')
            mock_save.assert_called_once()

    def test_save_config_to_yaml_failure(self):
        """Tests behavior when file saving fails."""
        with patch('src.monitoring_main.ConfigLoader._save_yaml', return_value=False):
            result = self.system._save_config_to_yaml()
            self.assertFalse(result)

    def test_setup_email_service_critical_failure(self):
        """Tests if a failure in email init raises RuntimeError."""
        with patch('src.monitoring_main.EmailService') as mock_email:
            mock_email.side_effect = Exception("Auth Timeout")
            
            with self.assertRaises(RuntimeError):
                self.system._setup_email_service("test@test.com", "pass")

    def test_show_plc_info_success(self):
        """Verifies if PLC info is correctly bridged from the client."""
        mock_data = {'name': 'PLC_01', 'ip': '192.168.0.10', 'port': '4840'}
        self.system.plc._get_info.return_value = mock_data
        
        info = self.system._show_plc_info()
        self.assertEqual(info['name'], 'PLC_01')
        self.system._logger_plc.info.assert_called()

    def test_show_plc_info_connection_error(self):
        """Tests if ConnectionError is properly propagated."""
        self.system.plc._get_info.side_effect = ConnectionError("PLC Offline")
        
        with self.assertRaises(ConnectionError):
            self.system._show_plc_info()
    
    def test_format_alert_texts_plc_offline(self):
        """Tests if alert text generation handles PLC connection errors gracefully."""
        with patch.object(self.system, '_show_plc_info', side_effect=ConnectionError()):
            self.system.formatter._build_telegram_alert.return_value = "Telegram Body"
            self.system.formatter._build_email_alert.return_value = "Email Body"

            result = self.system._format_alert_texts(
                "temp/01", 55.0, 50.0, ">", "Temp Sensor")

            self.assertEqual(
                result['subject'],
                "⚠️ ALERTA: Temp Sensor fora dos limites")
            self.system.formatter._build_telegram_alert.assert_called_with(
                "temp/01", 55.0, 50.0, ">",
                "Temp Sensor", {"name": "Offline", "ip": "N/A"})

    def test_execute_alert_notifications_both_channels(self):
        """Verifies that both email and telegram dispatchers are called."""
        mock_alerts = {
            "email": "body email", 
            "telegram": "body tg", 
            "subject": "sub"}
        
        with patch.object(self.system, '_format_alert_texts', return_value=mock_alerts):
            self.system._execute_alert_notifications(
                topic="test/topic", value=10.0,
                threshold=5.0, op=">",
                channels=["email", "telegram"], email_list=["admin@test.com"],
                label="Sensor Test")

            self.system.telegram_service._send_notifications.assert_called_with("body tg")
            self.system.email_service.send_email.assert_called_with(
                "admin@test.com", "sub", "body email")

    def test_dispatch_emails_partial_failure(self):
        """Ensures one email failure doesn't stop other deliveries."""
        recipients = ["fail@test.com", "success@test.com"]
        
        self.system.email_service.send_email.side_effect = [
            Exception("SMTP Error"), None]

        self.system._dispatch_emails(recipients, "Subject", "Body")

        self.assertEqual(self.system.email_service.send_email.call_count, 2)
        self.system._logger_email.error.assert_called()
        self.system._logger_email.info.assert_called()
    
    def test_format_normalization_texts_success(self):
        """Tests normalization text generation structure."""
        self.system.formatter._build_telegram_normalization.return_value = "TG Recovery"
        self.system.formatter._build_email_normalization.return_value = "Email Recovery"
        
        with patch.object(self.system, '_show_plc_info', return_value={"name": "Test"}):
            res = self.system._format_normalization_texts(
                "t", 10, 20, "<", "Sensor")
            self.assertEqual(res['telegram'], "TG Recovery")
            self.assertIn("NORMALIZAÇÃO", res['subject'])

    def test_alert_callback_handler_trigger_logic(self):
        """Tests if trigger handler is called when condition is met."""
        # Setup logic: if current_value (15) > threshold (10) -> Met
        self.system.rules.get_operation.return_value = lambda a, b: a > b
        
        with patch.object(self.system, '_get_clean_value_from_mqtt_publisher', return_value=15.0), \
             patch.object(self.system, '_handle_alert_trigger') as mock_trigger:
            
            self.system._alert_callback_handler(
                topic="temp", payload="15.0", threshold=10.0, 
                op_sign=">", notify_via=["telegram"], email_list=[], label="Temp"
            )
            
            mock_trigger.assert_called_once()

            args, _ = mock_trigger.call_args
            self.assertEqual(args[0], "temp_>_10.0")

    def test_alert_callback_handler_unsupported_operator(self):
        """Ensures handler aborts gracefully with an invalid operator."""
        self.system.rules.get_operation.return_value = None
        
        with patch.object(self.system, '_logger_mqtt') as mock_log:
            self.system._alert_callback_handler(
                topic="temp", payload="10", threshold=5, 
                op_sign="INVALID", notify_via=[], email_list=[], label="L")
            mock_log.warning.assert_called()
    
    def test_handle_alert_trigger_debounce(self):
        """Ensures notifications are not sent twice for the same active alert."""
        alert_id = "sensor_01_high"
        
        with patch.object(self.system, '_execute_alert_notifications') as mock_notify:
            self.system._handle_alert_trigger(
                alert_id, "t", 50, 40, ">", [], [], "Sensor")
            self.system._handle_alert_trigger(
                alert_id, "t", 51, 40, ">", [], [], "Sensor")

            mock_notify.assert_called_once()
            self.assertIn(alert_id, self.system._active_alerts)

    def test_handle_alert_maintenance_suppression(self):
        """Verifies alerts are logged but not sent via notification during maintenance."""
        self.system.maintenance_active = True
        alert_id = "maint_test"
        
        with patch.object(self.system, '_execute_alert_notifications') as mock_notify:
            self.system._handle_alert_trigger(
                alert_id, "t", 100, 50, ">", ["telegram"], [], "Sensor")
            
            mock_notify.assert_not_called()
            self.assertIn(alert_id, self.system._active_alerts)

    def test_handle_alert_normalization_logic(self):
        """Ensures normalization clears state and notifies correctly."""
        alert_id = "norm_test"
        self.system._active_alerts.add(alert_id) # Start with active alert
        
        with patch.object(self.system, '_execute_normalization_notifications') as mock_norm:
            self.system._handle_alert_normalization(
                alert_id, "t", 30, 50, ">", ["email"], [], "Sensor")
            
            mock_norm.assert_called_once()
            self.assertNotIn(alert_id, self.system._active_alerts)

    def test_auto_action_execution_on_trigger(self):
        """Verifies that auto-actions are executed even if notification fails."""
        action_cfg = {"type": "pulse", "variable": "stop_btn"}
        
        with patch.object(self.system, '_execute_auto_action') as mock_action:
            self.system._handle_alert_trigger(
                "action_id", "t", 10, 5, ">", [],
                [], "Sensor", action_cfg=action_cfg)
            mock_action.assert_called_once_with(
                action_cfg, trigger_topic="Sensor")
    
    def test_execute_auto_action_bool_success(self):
        """Tests standard boolean variable write on PLC."""
        action_cfg = {
            'variable': 'motor_stop', 'type': 'bool',
            'value': True, 'pou': 'GVL'}
        
        self.system._execute_auto_action(action_cfg, "High Temp")
        
        self.system.plc._set_value.assert_called_with('GVL', 'motor_stop', True)
        self.system.telegram_service._send_notifications.assert_called()

    def test_execute_auto_action_pulse_failure(self):
        """Tests behavior when a physical pulse failure occurs."""
        action_cfg = {'variable': 'reset_btn', 'type': 'pulse'}
        
        with patch.object(self.system, '_send_plc_pulse', return_value=False):
            self.system._execute_auto_action(action_cfg)
            
            self.system._logger_plc.error.assert_called()

            args, _ = self.system.telegram_service._send_notifications.call_args
            self.assertIn("reset_btn", args[0])

    def test_send_dynamic_status_empty_cache(self):
        """Ensures a waiting message is sent if no data has been received yet."""
        self.system._current_values_cache = {}
        
        self.system._send_dynamic_status(chat_id=12345)
        
        args, kwargs = self.system.telegram_service._send_notifications.call_args
        self.assertIn("Aguardando primeiras leituras", args[0])
        self.assertEqual(kwargs['chat_id'], 12345)

    def test_format_status_report_structure(self):
        """Verifies if report generation combines PLC info and grouped variables."""
        self.system._current_values_cache = {"tag1": 10.5}
        self.system.formatter._group_by_pou.return_value = {
            "POU1": ["tag1: 10.5"]}
        self.system.formatter._build_status_report.return_value = "Final Report"
        
        with patch.object(self.system, '_show_plc_info', return_value={"name": "PLC01"}):
            report = self.system._format_status_report()
            self.assertEqual(report, "Final Report")
            self.system.formatter._build_status_report.assert_called()
    
    def test_send_uptime_message_calculation(self):
        """Checks if uptime string is correctly formatted (e.g., 1h 1m 0s)."""
        # Mocking 3660 seconds (1 hour, 1 minute)
        self.system._start_time = time.time() - 3660
        self.system.formatter._build_uptime_message.return_value = "Mocked Uptime"

        self.system._send_uptime_message(chat_id=123)
        
        # Verify calculation results passed to formatter
        args, _ = self.system.formatter._build_uptime_message.call_args
        self.assertIn("0d 1h 1m", args[0])
        self.system.telegram_service._send_notifications.assert_called_with(
            "Mocked Uptime", chat_id=123)

    def test_build_graph_keyboard_filtering(self):
        """Ensures only numeric (graphable) sensors appear in the keyboard."""
        self.system._config = {
            'monitoring_list': [
                {'topic': 'temp', 'description': 'Temperature'},
                {'topic': 'status', 'description': 'Machine Status'}
            ]
        }
        # Cache contains a float (graphable) and a string (not graphable)
        self.system._current_values_cache = {
            'temp': 25.5,
            'status': 'RUNNING'
        }
        
        # Mock ConfigLoader._is_graphable behavior
        with patch('src.monitoring_main.ConfigLoader._is_graphable',
        side_effect=lambda x: isinstance(x, (int, float))):
            self.system._build_graph_keyboard()
            
            # Formatter should only receive 'temp' in the graphable_list
            args, _ = self.system.formatter._build_sensor_graph_keyboard.call_args
            graphable_list = args[0]
            
            self.assertEqual(len(graphable_list), 1)
            self.assertEqual(graphable_list[0]['topic'], 'temp')

    def test_handle_graph_command_no_data(self):
        """Verifies warning message when cache is empty."""
        self.system._current_values_cache = {}
        
        self.system._handle_graph_command(chat_id=999)
        
        args, kwargs = self.system.telegram_service._send_notifications.call_args
        self.assertIn("Nenhum dado disponível", args[0])
        self.assertEqual(kwargs['chat_id'], 999)
    
    def test_extract_sensor_info_valid(self):
        """Tests successful extraction of topic and label from command."""
        self.system.sensor_mapping = {"factory/temp": "Temperature"}
        command = "view_graph_factory/temp"
        
        topic, label = self.system._extract_sensor_info(command)
        self.assertEqual(topic, "factory/temp")
        self.assertEqual(label, "Temperature")

    def test_extract_sensor_info_invalid_topic(self):
        """Tests behavior when topic is not found in mapping."""
        self.system.sensor_mapping = {}
        command = "view_graph_unknown/topic"
        
        topic, label = self.system._extract_sensor_info(command)
        self.assertEqual(topic, "unknown/topic")
        self.assertIsNone(label)

        self.system._logger_telegram.warning.assert_called()

    @patch('src.services.chart_bot_service.ChartGeneratorService.generate_line_chart')
    def test_process_graph_request_success(self, mock_gen_method):
        """Tests the full lifecycle of processing a graph request with valid 
        data, including: data retrieval, graph generation, and Telegram 
        notification."""

        topic = "test/topic"
        label = "Desc"
        command = f"view_graph_{topic}"
        
        self.system.sensor_mapping = {topic: label}
        self.system._history_cache = {topic: deque([10.0, 20.0], maxlen=10)}
        
        mock_buf = io.BytesIO(b"fake_image_content")
        mock_gen_method.return_value = mock_buf

        self.system._process_graph_request(command, "Fernando", 123)

        mock_gen_method.assert_called_once_with(label, [10.0, 20.0])
        
        self.system.telegram_service._send_image.assert_called_once()

    def test_process_graph_request_insufficient_data(self):
        """Tests handling of requests with insufficient data."""
        self.system.sensor_mapping = {"t": "Sensor Teste"}
        from collections import deque
        self.system._history_cache = {"t": deque([10.0])}
        
        self.system._process_graph_request("view_graph_t", "Fernando", 123)
        
        notifications = self.system.telegram_service._send_notifications.call_args_list
        
        insufficient_data_call = next(
            (c for c in notifications if "Dados insuficientes" in c[0][0]), 
            None)

        self.assertIsNotNone(insufficient_data_call, "Mensagem de 'Dados insuficientes' não encontrada.")
        
        kwargs = insufficient_data_call[1]
        self.assertEqual(kwargs.get('chat_id'), 123)

    def test_process_graph_request_critical_error(self):
        """Tests broad exception handling in graph generation lifecycle."""
        self.system.sensor_mapping = {"t": "Label"}
        self.system._history_cache = {"t": deque([10.0, 20.0])}
        
        # Simulate ChartGeneratorService crash (e.g., matplotlib error)
        with patch(f'{self.target_path}.ChartGeneratorService.generate_line_chart', side_effect=Exception("Render Fail")):
            self.system._process_graph_request("view_graph_t", "John", 123)
            
            self.system._logger_telegram.error.assert_called()
            args, kwargs = self.system.telegram_service._send_notifications.call_args
            self.assertIn("erro técnico", args[0])
    
    def test_extract_user_context_normalization(self):
        """Tests if user data is correctly normalized."""
        raw_data = {
            'id': 12345,
            'first_name': 'Fernando',
            'chat_id': 67890,
            'type': 'private',
            'message_id': 999
        }

        context = self.system._extract_user_context(raw_data)
        
        self.assertEqual(context['name'], 'Fernando')
        self.assertEqual(context['user_id'], 12345)
        self.assertEqual(context['chat_id'], 67890)
        self.assertTrue(context['is_private'], "O contexto deveria identificar como chat privado")

    def test_execute_with_private_redirect_flow(self):
        """Tests the chat_id switching logic during private redirection."""
        user_id = 111222
        group_id = -555666

        self.system.telegram_service._chat_id = str(group_id)

        mock_cmd = MagicMock(return_value=True)

        self.system._execute_with_private_redirect(
            user_id=user_id, user_name="Op_Fernando",
            chat_id=group_id, command_func=mock_cmd)

        mock_cmd.assert_called_once()

        self.assertEqual(self.system.telegram_service._chat_id, str(group_id))
        
        notifications = self.system.telegram_service._send_notifications.call_args_list
        
        found_notification = any(
            "por segurança" in (call[0][0] if call[0] else call[1].get(
                'msg', '')) for call in notifications)
        
        self.assertTrue(found_notification, "A notificação de redirecionamento não foi encontrada nas chamadas do mock.")

    def test_promote_user_to_admin_success(self):
        """Tests the admin promotion orchestration."""
        self.system._save_new_admin_to_yaml = MagicMock(return_value=True)
        self.system.formatter._format_admin_update_status = MagicMock(
            return_value="Success Msg")
        
        self.system._promote_user_to_admin(requester_id=100, target_new_id=200)
        
        self.system._save_new_admin_to_yaml.assert_called_once_with(200)
        self.system.telegram_service._send_notifications.assert_called_with(
            "Success Msg", chat_id=100)
    
    def test_handle_admin_callback_view_graph(self):
        """Tests if 'view_graph_' command triggers graph processing and UI cleanup."""
        self.system._process_graph_request = MagicMock()
        cmd = "view_graph_sensor/temp"
        user_info = {'first_name': 'Fernando'}
        
        result = self.system._handle_admin_callback(cmd, 123, 999, user_info)
        
        self.assertTrue(result)
        self.system.telegram_service._delete_message.assert_called_with(
            123, 999)
        self.system._process_graph_request.assert_called_once()

    def test_handle_admin_callback_confirm_add(self):
        """Tests if 'CONFIRM_ADD_' correctly promotes a new user."""
        self.system._promote_user_to_admin = MagicMock()
        cmd = "CONFIRM_ADD_888777"
        user_info = {'first_name': 'Admin_Master'}
        
        result = self.system._handle_admin_callback(cmd, 111, 555, user_info)

        self.assertTrue(result)
        self.system.telegram_service._add_admin_id.assert_called_with(888777)
        self.system._promote_user_to_admin.assert_called_with(
            requester_id=111, target_new_id=888777)
        self.system.telegram_service._delete_message.assert_called_with(
            111, 555)

    def test_execute_admin_revocation_logic(self):
        """Tests the revocation chain: YAML removal and notification."""
        self.system._remove_admin_from_yaml = MagicMock()
        self.system.formatter._format_admin_removal_status = MagicMock(
            return_value="Revoked")
        self.system.telegram_service._admin_ids = [123, 456]
        
        self.system._execute_admin_revocation(requester_id=123, target_id=456)

        self.system._remove_admin_from_yaml.assert_called_with(456)
        self.assertNotIn(456, self.system.telegram_service._admin_ids)
        self.system.telegram_service._send_notifications.assert_called_with(
            "Revoked", chat_id=123)

    def test_command_handler_prioritizes_fsm(self):
        """Checks if active user state blocks other command processing."""
        self.system._user_states = {123: {'step': 'WAITING_VAL'}}
        self.system._handle_fsm_input = MagicMock()
        self.system._handle_admin_callback = MagicMock()
        
        user_info = {'id': 123, 'first_name': 'Fernando'}
        self.system._telegram_command_handler("Random Input", user_info)
        
        # FSM must be called, and Callback handler must NOT be called
        self.system._handle_fsm_input.assert_called_once()
        self.system._handle_admin_callback.assert_not_called()

    def test_handle_monitoring_callbacks_cancel(self):
        """Tests if CANCEL_ADD_VAR clears the user state and notifies."""
        self.system._user_states = {123: {'data': 'some_temp_state'}}
        
        result = self.system._handle_monitoring_callbacks(
            "CANCEL_ADD_VAR", 123, 444)
        
        self.assertTrue(result)
        self.assertNotIn(123, self.system._user_states)
        self.system.telegram_service._delete_message.assert_called_with(
            123, 444)
        self.system.telegram_service._send_notifications.assert_called()

    def test_handler_ignores_empty_commands(self):
        """Ensures the system doesn't crash or process empty strings."""
        self.system._extract_user_context = MagicMock()
        self.system._telegram_command_handler("", {})
        self.system._extract_user_context.assert_not_called()
    
    def test_handle_fsm_input_updates_state(self):
        """Tests if FSM correctly transitions from description to interval."""
        user_id = 123
        self.system._user_states = {user_id: {'action': 'WAITING_DESCRIPTION'}}
        
        self.system._handle_fsm_input(
            user_id, "Temperatura Tanque 1", "Fernando")
        
        state = self.system._user_states[user_id]
        self.assertEqual(state['description'], "Temperatura Tanque 1")
        self.assertEqual(state['action'], 'WAITING_INTERVAL')
        self.system.telegram_service._send_notifications.assert_called()

    def test_dispatch_text_command_public(self):
        """Tests if a public command like /status is routed correctly."""
        self.system._send_dynamic_status = MagicMock()
        ctx = {'chat_id': 555, 'user_id': 123, 'name': 'User'}
        
        self.system._dispatch_text_command("/status", ctx)
        self.system._send_dynamic_status.assert_called_with(555)

    def test_route_admin_command_denied(self):
        """Tests if non-admin users are blocked from admin commands."""
        self.system.telegram_service._is_active_admin_in_group = MagicMock(return_value=False)
        ctx = {'chat_id': 555, 'user_id': 999,
               'name': 'Guest', 'is_private': False}
        
        self.system._route_admin_command("/maintenance", MagicMock(), ctx)
        
        self.system.telegram_service._send_notifications.assert_called_with(
            ANY, chat_id=555)
    
    def test_handle_set_var_unmapped_variable(self):
        """Tests if system rejects variables not present in config."""
        self.system.config['commands'] = [{'variable': 'ri_start'}]
        
        result = self.system._handle_set_var(
            ['/set_var', 'var_desconhecida'], "Admin", 123)
        
        self.assertFalse(result)
        self.system.telegram_service._send_notifications.assert_called_with(
            ANY, chat_id=123)

    def test_handle_set_var_pulse_routing(self):
        """Tests if variables starting with 'ri_' are routed to _send_plc_pulse."""
        self.system._config['commands'] = [
            {'variable': 'ri_start_button', 'pou': 'MainPOU'}]
        self.system._send_plc_pulse = MagicMock(return_value=True)
        
        result = self.system._handle_set_var(
            ['/set_var', 'ri_start_button'], "Admin", 123)
        
        self.assertTrue(result)
        self.system._send_plc_pulse.assert_called_with(
            var_name='ri_start_button', pou_name='MainPOU', target_id=123)

    def test_check_plc_availability_failure(self):
        """Tests health check behavior when PLC is offline."""
        self.system.plc._is_plc_healthy = MagicMock(return_value=False)
        
        result = self.system._check_plc_availability()
        
        self.assertFalse(result)
        self.system._logger_mqtt.warning.assert_called()
    
    def test_is_matching_type_strict_check(self):
        """Ensures BOOL and NUM types are strictly separated using underscores."""
        # Test Boolean
        self.assertTrue(self.system._is_matching_type(True, "BOOL"))
        self.assertFalse(self.system._is_matching_type(1, "BOOL")) 
        
        # Test Numeric
        self.assertTrue(self.system._is_matching_type(25.5, "NUM"))
        self.assertTrue(self.system._is_matching_type(100, "NUM"))
        self.assertFalse(self.system._is_matching_type(False, "NUM"))

    def test_get_available_plc_vars_filtering(self):
        """Tests if scan excludes already monitored tags using self._config."""
        # Setup: PLC has 3 vars, but 'test_var' is already monitored (from your mock_config)
        self.system.plc.read_all_variables = MagicMock(return_value={
            'test_var': 45.0,        # Already in monitoring_list
            'pressure_tank': 2.1,     # New Numeric
            'motor_active': True      # New Boolean
        })

        available = self.system._get_available_plc_vars("NUM")

        self.assertIn('pressure_tank', available)
        self.assertNotIn('test_var', available)     # Excluded because it's already monitored
        self.assertNotIn('motor_active', available)  # Excluded due to type (is BOOL)

    def test_handle_set_var_unmapped_variable(self):
        """Tests rejection of variables not present in self._config['commands']."""
        result = self.system._handle_set_var(
            ['/set_var', 'unknown_sensor'], "Admin", 123)

        self.assertFalse(result)
        self.system._logger_telegram.warning.assert_called()
        self.system.telegram_service._send_notifications.assert_called_with(
            ANY, chat_id=123)
    
    def test_show_filtered_vars_menu_empty(self):
        """Tests menu behavior when no variables match the filter."""
        self.system._get_available_plc_vars = MagicMock(return_value=[])
        
        result = self.system._show_filtered_vars_menu(123, "NUM")
        
        self.assertTrue(result)

        self.system.formatter._build_inline_keyboard.assert_called()
        args, kwargs = self.system.formatter._build_inline_keyboard.call_args
        self.assertEqual(args[0][0]['callback'], "ADD_VAR_RETRY")

    def test_ask_for_monitoring_details_locks_state(self):
        """Ensures the FSM state is correctly set and includes a timestamp."""
        user_id = 444555666
        var_name = "AI_Tank_Level"
        
        self.system._ask_for_monitoring_details(user_id, var_name)
        
        self.assertIn(user_id, self.system._user_states)
        state = self.system._user_states[user_id]
        self.assertEqual(state['action'], 'WAITING_DESCRIPTION')
        self.assertEqual(state['variable'], var_name)
        self.assertIn('started_at', state)

    def test_wizard_state_rollback_on_failure(self):
        """Tests if state is cleared if sending the message fails."""
        user_id = 123
        self.system.telegram_service._send_notifications = MagicMock(
            side_effect=Exception("API Error"))
        
        result = self.system._ask_for_monitoring_details(user_id, "test_var")
        
        self.assertFalse(result)
        self.assertNotIn(user_id, self.system._user_states)
    
    def test_parse_interval_formats(self):
        """Tests parsing of numeric intervals with different separators."""
        self.assertEqual(self.system._parse_interval("1,5"), 1.5)
        self.assertEqual(self.system._parse_interval("2.0"), 2.0)
        self.assertIsNone(self.system._parse_interval("invalid"))
        self.assertIsNone(self.system._parse_interval("-1.0"))

    def test_persist_monitoring_var_success(self):
        """Tests if var is added to config and YAML save is triggered."""
        self.system._save_config_to_yaml = MagicMock(return_value=True)
        self.system._create_topic_from_add_var_method = MagicMock(
            return_value=("MainPOU", "MainPOU/tag1"))
        
        self.system._loop = MagicMock()
        self.system._loop.is_running.return_value = True
        
        with patch('asyncio.run_coroutine_threadsafe') as mock_run:
            result = self.system._persist_monitoring_var("tag1", "Desc", 1.0)
            
            self.assertTrue(result)
            self.system._save_config_to_yaml.assert_called()
            self.assertEqual(self.system._config[
                'monitoring_list'][-1]['variable'], "tag1")
            mock_run.assert_called()

    def test_create_topic_resolution(self):
        """Tests if variable is correctly mapped to its POU topic."""
        self.system.plc._pou_node_cache = {'MixerPOU': {}}
        self.system.plc.read_all_variables = MagicMock(
            return_value={'temp_sensor': 25.0})
        
        pou, topic = self.system._create_topic_from_add_var_method(
            "temp_sensor")
        
        self.assertEqual(pou, "MixerPOU")
        self.assertEqual(topic, "MixerPOU/temp_sensor")
    
    def test_finalize_variable_addition_success(self):
        """Tests full finalization: persistence, UI calls, and state cleanup."""
        user_id = 111222333
        self.system._user_states[user_id] = {
            'variable': 'new_tag',
            'description': 'Test Description'}
        
        self.system._persist_monitoring_var = MagicMock(return_value=True)
        
        self.system.formatter._format_add_var_success_admin.return_value = {
            "text": "Admin OK"}
        self.system.formatter._format_add_var_broadcast_group.return_value = {
            "text": "Group OK"}

        result = self.system._finalize_variable_addition(user_id, "2.5", "AdminUser")

        self.assertTrue(result)
        self.assertNotIn(user_id, self.system._user_states)
        self.system.telegram_service._send_notifications.assert_any_call(
            "Admin OK", chat_id=user_id)
        self.system.telegram_service._send_notifications.assert_any_call(
            "Group OK")

    def test_send_rm_var_step2_filtering(self):
        """Tests if removal list only shows variables of the selected type."""

        self.system._config['monitoring_list'] = [
            {'variable': 'btn_active', 'topic': 'POU/btn_active'},
            {'variable': 'tank_level', 'topic': 'POU/tank_level'}
        ]

        self.system._current_values_cache = {
            'POU/btn_active': {'value': True},    # BOOL
            'POU/tank_level': {'value': 45.5}     # NUM
        }
        
        self.system._send_rm_var_step2_list(123, "BOOL")
        
        self.system.formatter._format_remove_list_menu.assert_called_with(
            ['btn_active'], "BOOL")
    
    def test_find_monitored_item_exists(self):
        """Tests retrieval of an existing variable configuration."""

        item = self.system._find_monitored_item("test_var")
        
        self.assertIsNotNone(item)
        self.assertEqual(item['variable'], "test_var")
        self.assertEqual(item['topic'], "test/topic")

    def test_find_monitored_item_not_found(self):
        """Tests behavior when searching for a non-existent variable."""
        item = self.system._find_monitored_item("ghost_variable")
        self.assertIsNone(item)

    def test_delete_from_config_success(self):
        """Tests if the variable is effectively removed from self._config."""
        self.assertEqual(len(self.system._config['monitoring_list']), 1)
        
        result = self.system._delete_from_config("test_var")
        
        self.assertTrue(result)
        self.assertEqual(len(self.system._config['monitoring_list']), 0)

    def test_delete_from_config_no_action(self):
        """Tests that deletion returns False if the variable wasn't there."""
        result = self.system._delete_from_config("non_existent_var")
        self.assertFalse(result)

    def test_clear_internal_caches(self):
        """Checks if all traces of a topic are removed from memory."""
        topic = "PRG/test_topic"
        self.system._current_values_cache[topic] = {'value': 10}
        self.system._history_cache[topic] = deque([1, 2, 3])
        
        self.system._clear_internal_caches(topic)
        
        self.assertNotIn(topic, self.system._current_values_cache)
        self.assertNotIn(topic, self.system._history_cache)

    def test_remove_monitoring_var_orchestration(self):
        """Verifies full removal flow: task cancellation and disk save."""
        var_name = "test_var"
        mock_future = MagicMock()
        self.system._active_monitoring_tasks[var_name] = mock_future
        self.system._save_config_to_yaml = MagicMock(return_value=True)
        self.system._loop = MagicMock()
        
        result = self.system._remove_monitoring_var(var_name, "AdminUser", 123)
        
        self.assertTrue(result)
        self.system._loop.call_soon_threadsafe.assert_called_with(
            mock_future.cancel)
        self.assertNotIn(var_name, self.system._active_monitoring_tasks)

    def test_send_plc_ping_alive(self):
        """Tests diagnostic reporting when PLC is healthy."""
        self.system._check_plc_availability = MagicMock(return_value=True)
        self.system.formatter._format_plc_ping_result = MagicMock(
            return_value="Ping OK")
        
        self.system._send_plc_ping(123)
        
        self.system.telegram_service._send_notifications.assert_called_with(
            "Ping OK", chat_id=123)
    
    @patch('subprocess.run')
    def test_perform_network_scan_logic(self, mock_run):
        """Tests if the scan correctly identifies active vs inactive IPs."""
        def side_effect(cmd, **kwargs):
            ip = cmd[-1]
            mock_res = MagicMock()
            mock_res.returncode = 0 if ip.endswith('.10') else 1
            return mock_res
        
        mock_run.side_effect = side_effect
        
        results = self.system._perform_network_scan(
            "192.168.0", start=1, end=15)
        
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], "192.168.0.10")

    def test_get_system_metrics_structure(self):
        """Verifies if the metrics dictionary contains all required keys."""
        metrics = self.system._get_system_metrics()
        
        self.assertIn('cpu_percent', metrics)
        self.assertIn('ram', metrics)
        self.assertIn('disk', metrics)
        self.assertIn('uptime', metrics)
        self.assertTrue(isinstance(metrics['cpu_percent'], float))
    
    @patch('psutil.sensors_temperatures', create=True)
    @patch('platform.system')
    def test_get_system_metrics_linux_temperature(
        self, mock_system, mock_temp):
        """Tests CPU temperature extraction on Linux simulation."""
        mock_system.return_value = "Linux"
        
        mock_temp.return_value = {
            'coretemp': [MagicMock(current=45.0)]}

        with patch('psutil.cpu_percent'), \
             patch('psutil.virtual_memory'), \
             patch('psutil.disk_usage'), \
             patch.object(self.system, '_get_host_uptime'):
            
            metrics = self.system._get_system_metrics()

            self.assertEqual(metrics['cpu_temp'], 45.0)
    
    def test_get_host_uptime_formatting(self):
        """Tests the logic for days/hours/minutes conversion."""
        # Mock de 25 horas e 10 minutos (90600 segundos)
        with patch('time.time', return_value=100000), \
             patch('psutil.boot_time', return_value=9400):
            uptime = self.system._get_host_uptime()
            self.assertEqual(uptime, "1d 1h 10m")

    def test_send_production_cycles_filtering(self):
        """Ensures only relevant tags are included in the production report."""
        self.system._current_values_cache = {
            'POU/count_total': {'value': 150, 'label': 'Peças Produzidas'},
            'POU/temp_motor': {'value': 45.5, 'label': 'Temp Motor'},
            'POU/ciclo_ativo': {'value': True, 'label': 'Status Ciclo'}
        }
        
        self.system.formatter._format_cycles_report = MagicMock(
            return_value="Report Text")
        
        self.system._send_production_cycles(123)
        
        args, _ = self.system.formatter._format_cycles_report.call_args
        sent_data = args[0]
        
        self.assertIn('Peças Produzidas', sent_data)
        self.assertIn('Status Ciclo', sent_data)
        self.assertNotIn('Temp Motor', sent_data)

    def test_send_production_cycles_empty(self):
        """Tests behavior when no keywords match the current cache."""
        self.system.telegram_service._send_notifications.reset_mock()
        
        self.system._current_values_cache = {
            'sensor/vibracao': {'value': 0.5, 'label': 'Nivel de Vibracao'}
        }

        msg_vazia = "ℹ️ *Informação:* Nenhum contador de ciclos ou produção identificado no cache atual."
        
        with patch.object(self.system.formatter, '_format_cycles_report',
                          return_value=msg_vazia) as mock_fmt:
            
            self.system._send_production_cycles(123)

            mock_fmt.assert_called_once_with({})
            
            self.system.telegram_service._send_notifications.assert_called_once_with(
                msg_vazia, chat_id=123)
    
    def test_next_maintenance_success(self):
        """Tests the generation of the maintenance wear report."""
        self.system._config['maintenance_targets'] = [
            {'topic': 'plc/counters/cycle_count',
             'limit': 1000, 'label': 'Prensa Hidráulica'}
        ]
        self.system._current_values_cache = {
            'plc/counters/cycle_count': {'value': 750, 'label': 'Ciclos Totais'}
        }
        
        self.system.telegram_service._send_notifications.reset_mock()
        report_msg = "🛠️ *RELATÓRIO DE MANUTENÇÃO*\n- Prensa: 75% usado."

        with patch.object(self.system.formatter, '_format_maintenance_report',
                          return_value=report_msg) as mock_fmt:
            
            self.system._next_maintenance(user_id=123)

            mock_fmt.assert_called_once() 

            args, _ = mock_fmt.call_args
            data_sent = args[0][0]
            self.assertEqual(data_sent['percent'], 75.0)
            
            self.system.telegram_service._send_notifications.assert_called_once_with(
                report_msg, chat_id=123)

    def test_toggle_maintenance_mode_state(self):
        """Verifies if the toggle correctly flips the boolean state."""
        self.system.maintenance_active = False
        
        new_state = self.system._toggle_maintenance_mode()
        self.assertTrue(new_state)
        self.assertTrue(self.system.maintenance_active)
        
        new_state = self.system._toggle_maintenance_mode()
        self.assertFalse(new_state)
    
    def test_get_friendly_name_lookup(self):
        """Checks if descriptive names are retrieved from both monitoring and 
            command lists."""
        self.system._config = {
            'monitoring_list': [
                {'variable': 'temp_01', 'description': 'Sensor de Temperatura'}
                ],
            'commands': [
                {'variable': 'btn_start', 'description': 'Botão Iniciar'}
                ]}
        
        self.assertEqual(
            self.system._get_friendly_name('temp_01'), 'Sensor de Temperatura')
        self.assertEqual(
            self.system._get_friendly_name('btn_start'), 'Botão Iniciar')
        self.assertEqual(self.system._get_friendly_name('unknown'), 'unknown')

    def test_execute_physical_pulse_cycle(self):
        """Ensures the pulse writes the active value, waits, and restores the 
            initial value."""
        self.system.plc._set_value = MagicMock(return_value=True)
        
        with patch('time.sleep', return_value=None): # Evita esperar 0.5s no teste
            success = self.system._execute_physical_pulse(
                "GVL", "var_test", False)
            
            self.assertTrue(success)
            # Method should be called twice: once to set True, once to restore False
            self.assertEqual(self.system.plc._set_value.call_count, 2)
            
            calls = [
                call("GVL", "var_test", True),
                call("GVL", "var_test", False)
            ]
            self.system.plc._set_value.assert_has_calls(calls)

    def test_send_boolean_commands_menu_empty(self):
        """Ensures the correct error message is sent when no commands are found."""
        self.system._config = {'commands': []}
        self.system.telegram_service._send_notifications.reset_mock()

        expected_error_data = {
            "text": "❌ *ERRO DE CONFIGURAÇÃO*\nNenhum comando interativo foi encontrado no arquivo YAML.",
            "keyboard": None}

        with patch.object(self.system.formatter, '_format_commands_menu',
                          return_value=expected_error_data):
            
            self.system._send_boolean_commands_menu(user_id=123)

            self.system.telegram_service._send_notifications.assert_called_once()
            _, kwargs = self.system.telegram_service._send_notifications.call_args
            
            sent_message = kwargs.get('message', '')
            self.assertIn("ERRO DE CONFIGURAÇÃO", sent_message)
            self.assertIn("Nenhum comando interativo", sent_message)
    
    def test_toggle_boolean_variable_reversion(self):
        """Tests if toggle detects when PLC logic reverts a value (Verification failure)."""
        self.system.plc._is_plc_healthy = MagicMock(return_value=True)

        self.system.plc.get_value = MagicMock(side_effect=[False, False])
        self.system.plc._set_value = MagicMock(return_value=True)
        
        with patch('time.sleep', return_value=None), \
             patch.object(self.system.formatter, '_format_toggle_status',
                          return_value="Status Msg") as mock_fmt:
            
            result = self.system._toggle_boolean_variable("var_x", "POU_Y", user_id=123)

            self.assertFalse(result)

            mock_fmt.assert_called_once_with("var_x", True, False)
            
            self.system.telegram_service._send_notifications.assert_called_with(
                "Status Msg", chat_id=123)
    
    def test_get_authorized_email_denied(self):
        """Ensures unauthorized users are blocked and notified."""
        self.system._config['allowed_log_emails'] = {'999': 'admin@test.com'}
        self.system.telegram_service._send_notifications = MagicMock()
        
        result = self.system._get_authorized_email("123", "User Test")
        
        self.assertIsNone(result)
        self.system.telegram_service._send_notifications.assert_called_once()
        
    def test_send_logs_command_full_success(self):
        """Tests the complete workflow: auth -> zip -> email -> cleanup."""
        user_info = {'id': 123, 'name': 'Fernando'}
        target_email = "fernando@empresa.com.br"
        
        self.system._get_authorized_email = MagicMock(return_value=target_email)
        self.system._create_log_archive = MagicMock(return_value=True)
        self.system._execute_email_dispatch = MagicMock(return_value=True)
        self.system.telegram_service._send_notifications = MagicMock()
        
        with patch.object(self.system, '_cleanup_temp_file') as mock_cleanup:
            self.system._send_logs_command(user_info)

            mock_cleanup.assert_called_once()

            self.assertEqual(
                self.system.telegram_service._send_notifications.call_count, 2)

    def test_create_log_archive_empty_dir(self):
        """Ensures it returns False if no logs exist."""
        with patch('pathlib.Path.exists', return_value=True), \
             patch('pathlib.Path.iterdir', return_value=iter([])): # Simula dir vazio
            
            result = self.system._create_log_archive(Path("temp"))
            self.assertFalse(result)
    
    def test_collect_log_files_from_dir(self):
        """Tests if glob finds both .txt and .log files in a directory."""
        with patch('pathlib.Path.is_dir', return_value=True), \
             patch('pathlib.Path.glob') as mock_glob:
            
            mock_glob.side_effect = [
                [Path("mqtt.txt")], # retorno para *.txt
                [Path("plc.log")]   # retorno para *.log
            ]
            
            files = self.system._collect_log_files("fake_dir")
            
            self.assertEqual(len(files), 2)
            self.assertIn(Path("mqtt.txt"), files)
            self.assertIn(Path("plc.log"), files)

    def test_cleanup_temp_file_success(self):
        """Ensures unlink is called when the file exists."""
        mock_file = MagicMock(spec=Path)
        mock_file.exists.return_value = True
        
        self.system._cleanup_temp_file(mock_file)
        
        mock_file.unlink.assert_called_once()

    def test_wipe_log_file_execution(self):
        """Tests if the file is opened for writing with a header."""
        m = mock_open()
        with patch('builtins.open', m), \
             patch('pathlib.Path.exists', return_value=True), \
             patch('pathlib.Path.is_file', return_value=True):
            
            self.system._wipe_log_file(Path("test.log"))
            
            m.assert_called_once_with(Path("test.log"), 'w', encoding='utf-8')
            handle = m()
            handle.write.assert_called()
    
    def test_update_admin_list_memory_add(self):
        """Tests adding a new admin to the internal dictionary."""
        self.system._config = {'telegram_connection': {'admin_ids': [100]}}

        result = self.system._update_admin_list_memory(200, action="add")
        self.assertTrue(result)
        self.assertIn(200, self.system._config[
            'telegram_connection']['admin_ids'])

        result_repeat = self.system._update_admin_list_memory(100, action="add")
        self.assertFalse(result_repeat)

    def test_save_new_admin_to_yaml_persistence(self):
        """Ensures that adding an admin triggers the disk save."""
        self.system._update_admin_list_memory = MagicMock(return_value=True)
        self.system._persist_config_to_disk = MagicMock(return_value=True)
        
        result = self.system._save_new_admin_to_yaml(12345)
        
        self.assertTrue(result)
        self.system._persist_config_to_disk.assert_called_once()

    def test_persist_config_to_disk_error(self):
        """Tests handling of filesystem errors during YAML save."""
        with patch('builtins.open', side_effect=IOError("Disk Full")):
            result = self.system._persist_config_to_disk(Path("config.yaml"))
            self.assertFalse(result)
    
    def test_determine_pou_hierarchy_global_priority(self):
        """Tests if global variables prioritize GVLs."""
        self.system.plc._pou_node_cache = {
            "Main_PRG": {}, "GVL_Sensors": {}, "GVL_Alarms": {}
        }

        # Variável com prefixo 'g_'
        hierarchy = self.system._determine_pou_hierarchy("g_system_status")
        
        # Deve começar com GVLs em ordem alfabética
        self.assertEqual(hierarchy[0], "GVL_Alarms")
        self.assertEqual(hierarchy[1], "GVL_Sensors")
        self.assertEqual(hierarchy[2], "Main_PRG")

    @patch("asyncio.get_running_loop")
    async def test_perform_safe_plc_read_failover(self, mock_get_loop):
        """Tests failover when the primary POU returns None."""
        hierarchy = ["POU_A", "POU_B"]
        state = {"current_idx": 0}
        
        self.system.plc.get_value = MagicMock(side_effect=[None, 42.5])
        
        mock_loop = MagicMock()
        future = asyncio.Future()
        future.set_result((42.5, "POU_B"))
        mock_loop.run_in_executor.return_value = future
        mock_get_loop.return_value = mock_loop

        val, found_pou = await self.system._perform_safe_plc_read("test_var", hierarchy, state)
        
        self.assertEqual(val, 42.5)
        self.assertEqual(found_pou, "POU_B")
    
    def test_clean_numeric_value_conversion(self):
        """Tests various PLC string inputs and their conversion."""
        self.assertEqual(self.system._clean_numeric_value("12,5"), 12.5)
        self.assertEqual(self.system._clean_numeric_value("100"), 100)
        self.assertEqual(self.system._clean_numeric_value(True), True)
        self.assertEqual(self.system._clean_numeric_value("invalid"), "invalid")

    @patch("asyncio.sleep", return_value=None)
    async def test_variable_monitoring_task_no_freeze(self, mock_sleep):
        """Tests that the monitoring task doesn't freeze."""
        var_config = {'variable': 'T', 'interval': 1,
                      'topic': 't', 'description': 'd'}
        
        async def mock_read(*args): return (25.4, "GVL")
        self.system._perform_safe_plc_read = mock_read

        self.system._process_monitoring_result = MagicMock(
            side_effect=StopLoopException())
        
        try:
            await self.system.variable_monitoring_task(var_config)
        except StopLoopException:
            pass

        self.system._process_monitoring_result.assert_called_once()
    
    def test_setup_mqtt_alerts_registration(self):
        """Tests if alert rules are correctly parsed and registered in MQTT 
            service."""        
        self.system.mqtt.add_alert_rule = MagicMock()
        
        self.system._setup_mqtt_alerts()
        
        args, _ = self.system.mqtt.add_alert_rule.call_args
        self.assertEqual(args[0], 'test/topic')
        self.assertIsNone(args[1])
        self.assertTrue(callable(args[2]))

    def test_get_sensor_label_fallback(self):
        """Ensures topic is returned if no description exists."""
        self.system._config = {'monitoring_list': []}
        label = self.system._get_sensor_label("unknown/topic")
        self.assertEqual(label, "unknown/topic")
    
    def test_mqtt_heartbeat_update(self):
        """Ensures the heartbeat timestamp is updated correctly."""
        initial_time = self.system._last_mqtt_heartbeat
        time.sleep(0.01)
        self.system._update_mqtt_heartbeat()
        self.assertGreater(self.system._last_mqtt_heartbeat, initial_time)

    def test_is_mqtt_threshold_exceeded(self):
        """Tests the safety limit detection for MQTT connectivity."""
        self.system._max_offline_time = 30

        self.system._last_mqtt_heartbeat = time.time() - 10
        self.assertFalse(self.system._is_mqtt_threshold_exceeded())
        
        self.system._last_mqtt_heartbeat = time.time() - 40
        self.assertTrue(self.system._is_mqtt_threshold_exceeded())

    @patch("os._exit")
    @patch("asyncio.sleep")
    async def test_handle_mqtt_critical_failure_triggers_exit(
        self, mock_sleep, mock_exit):
        """Verifies if critical failure calls os._exit."""
        self.system.telegram_service._send_notifications = MagicMock()
        
        await self.system._handle_mqtt_critical_failure()
        
        self.system.telegram_service._send_notifications.assert_called_once()
        mock_exit.assert_called_once_with(1)
    
    @patch("src.monitoring_main.IndustrialMonitoringSystem._establish_initial_connections")
    async def test_run_aborts_on_connection_failure(self, mock_connect):
        """Ensures the system raises RuntimeError if connections fail."""
        mock_connect.return_value = False
        self.system._start_monitoring_engine = AsyncMock()
        
        with self.assertRaises(RuntimeError) as cm:
            await self.system.run()
        
        self.assertIn("Failed to establish critical connections",
                      str(cm.exception))

        self.system._start_monitoring_engine.assert_not_called()

    def test_establish_initial_connections_success(self):
        """Verifies successful connection sequence."""
        self.system.plc.connect = MagicMock(return_value=True)
        self.system.mqtt.connect_broker = MagicMock(return_value=True)
        
        result = self.system._establish_initial_connections()
        
        self.assertTrue(result)
        self.system.plc.connect.assert_called_once()
        self.system.mqtt.connect_broker.assert_called_once()
    
    @patch("asyncio.gather", new_callable=AsyncMock)
    async def test_start_monitoring_engine_orchestration(self, mock_gather):
        """Checks if all sensors + watchdog are gathered into the event loop."""
        self.system._config = {
            'monitoring_list': [
                {'variable': 'Temp', 'interval': 1, 'topic': 't1'},
                {'variable': 'Press', 'interval': 1, 'topic': 't2'}
            ]
        }
        
        self.system.variable_monitoring_task = MagicMock()
        self.system._mqtt_watchdog_task = MagicMock()
        
        await self.system._start_monitoring_engine()
        
        # Validation: 2 sensors + 1 watchdog = 3 tasks no gather
        self.assertEqual(mock_gather.call_count, 1)
        args = mock_gather.call_args[0]
        self.assertEqual(len(args), 3)

    @patch("os._exit")
    def test_shutdown_sequence(self, mock_exit):
        """Verifies if cleanup and notifications are triggered during shutdown."""
        self.system.telegram_service._send_notifications = MagicMock()
        self.system.telegram_service._clear_pending_updates = MagicMock()
        self.system._rotate_logs = MagicMock()

        self.system._shutdown_bot_system()

        self.system.telegram_service._clear_pending_updates.assert_called_once()
        self.system._rotate_logs.assert_called_once()
        mock_exit.assert_called_once_with(0)

if __name__ == '__main__':
    unittest.main(verbosity=2)