import unittest
from unittest.mock import patch
import time
from src.utils.formatters import MonitoringFormatter

class TestMonitoringFormatter(unittest.TestCase):

    def setUp(self):
        self.formatter = MonitoringFormatter()
        self.plc_info = {"name": "CLP_LAB_01", "ip": "192.168.1.50"}

    def test_format_value(self):
        """Tests formatting of different data types (bool, float, int, None)."""
        self.assertEqual(self.formatter._format_value(True), "Ligado")
        self.assertEqual(self.formatter._format_value(False), "Desligado")
        self.assertEqual(self.formatter._format_value(12.3456, precision=2), "12.35")
        self.assertEqual(self.formatter._format_value(100), "100")
        self.assertEqual(self.formatter._format_value(None), "N/A")

    def test_get_operator_text(self):
        """Tests the translation of math operators to Portuguese."""
        self.assertEqual(self.formatter._get_operator_text(">="), "maior ou igual a")
        self.assertEqual(self.formatter._get_operator_text("=="), "igual a")
        self.assertEqual(self.formatter._get_operator_text("UNKNOWN"), "UNKNOWN")

    def test_get_alert_context_logic(self):
        """Tests if the context dictionary is built correctly."""
        ctx = self.formatter._get_alert_context(
            topic="sensor/temp", 
            value=85.5, 
            threshold=80.0, 
            op=">", 
            label="Forno"
        )
        self.assertEqual(ctx['sensor_display'], "Forno")
        self.assertEqual(ctx['op_text'], "acima de")
        self.assertEqual(ctx['val_fmt'], "85.50")
        self.assertIn(':', ctx['curr_time'])

    def test_get_alert_context_no_label(self):
        """Tests fallback to topic when label is missing."""
        ctx = self.formatter._get_alert_context("topic/raw", 1, 1, "==", None)
        self.assertEqual(ctx['sensor_display'], "topic/raw")

    def test_build_telegram_alert_structure(self):
        """Tests the final Markdown string composition for Telegram."""
        with patch('time.strftime') as mock_time:
            mock_time.return_value = "10:00:00"
            alert = self.formatter._build_telegram_alert(
                topic="ax/pos", value=150.0, threshold=100.0, 
                op=">", label="Eixo X", plc_info=self.plc_info)
            
            self.assertIn("*ALERTA DE SISTEMA*", alert)
            self.assertIn("`CLP_LAB_01`", alert)
            self.assertIn("`Eixo X`", alert)
            self.assertIn("(acima de `100.00`)", alert)
            self.assertIn("`10:00:00`", alert)
    
    def test_build_email_alert_content(self):
        """Validates the formal structure of the email alert."""
        email = self.formatter._build_email_alert(
            "factory/press", 4.5, 5.0, "<", "Prensa 1", self.plc_info
        )
        self.assertIn("Prezado(a),", email)
        self.assertIn("Prensa 1", email)
        self.assertIn("abaixo de", email)
        self.assertNotIn("*", email) # Email não deve ter negrito Markdown

    def test_build_telegram_normalization(self):
        """Checks if normalization message shows the 'RESOLVED' status."""
        msg = self.formatter._build_telegram_normalization(
            "tank/level", 50, 80, ">", "Nível Tanque", self.plc_info
        )
        self.assertIn("✅ *STATUS NORMALIZADO*", msg)
        self.assertIn("foi resolvida", msg)
        self.assertIn("`50`", msg)

    def test_build_email_normalization_stripping(self):
        """Ensures email normalization is just a clean version of telegram's."""
        msg = self.formatter._build_email_normalization(
            "temp", 25, 30, ">", "Temp", self.plc_info
        )
        self.assertNotIn("*", msg)
        self.assertNotIn("`", msg)
        self.assertIn("STATUS NORMALIZADO", msg)

    def test_auto_action_bool_true(self):
        """Tests the 'Set True' action formatting."""
        cfg = {
            "variable": "Cooler_Fan", 
            "type": "bool", 
            "value": True, 
            "name": "Ventilador",
            "reason": "Alta temperatura"
        }
        msg = self.formatter._build_auto_action_message(cfg, "sensor/temp")
        self.assertIn("⚙️ *Equipamento:* `Ventilador`", msg)
        self.assertIn("🟢 LIGADO", msg)
        self.assertIn("Alta temperatura", msg)

    def test_auto_action_pulse(self):
        """Tests the 'Pulse' action formatting."""
        cfg = {
            "variable": "GVL.Reset_Counter", 
            "type": "pulse", 
            "name": "Reset",
            "reason": "Final de ciclo"
        }
        msg = self.formatter._build_auto_action_message(cfg, "sensor/count")
        self.assertIn("⚡ *Comando:* `Reset`", msg)
        self.assertIn("`PULSO DISPARADO`", msg)
    
    def test_format_cache_line(self):
        """Checks the formatting of a single status line."""
        line = self.formatter._format_cache_line("Motor", True)
        self.assertEqual(line, "📍 *Motor:* `Ligado`")

    def test_group_by_pou_logic(self):
        """Ensures variables are correctly assigned to their respective POUs."""
        cache = {
            "sensors/temp1": {"value": 25.5, "pou": "POU_A", "label": "T1"},
            "sensors/temp2": {"value": 30.0, "pou": "POU_A", "label": "T2"},
            "sensors/press": {"value": 5.2, "pou": "POU_B", "label": "P1"}
        }
        grouped = self.formatter._group_by_pou(cache)
        
        self.assertEqual(len(grouped), 2)
        self.assertEqual(len(grouped["POU_A"]), 2)
        self.assertIn("T1", grouped["POU_A"][0])
        self.assertIn("P1", grouped["POU_B"][0])

    def test_group_by_pou_fallback_label(self):
        """Ensures that missing labels use the last part of the MQTT topic."""
        cache = {"factory/machine/axis_x": {"value": 100, "pou": "Motion"}}
        grouped = self.formatter._group_by_pou(cache)
        self.assertIn("axis_x", grouped["Motion"][0])

    def test_build_status_report_structure(self):
        """Validates the assembly of the final status report."""
        grouped = {
            "MainTask": ["📍 *Var1:* `10`"],
            "AlarmTask": ["📍 *Var2:* `False`"]
        }
        report = self.formatter._build_status_report(self.plc_info, grouped)
        
        self.assertIn("RELATÓRIO DE MONITORAMENTO", report)
        self.assertIn("ARQUIVO: MainTask", report)
        self.assertIn("ARQUIVO: AlarmTask", report)
        self.assertIn("`CLP_LAB_01`", report)

    def test_build_uptime_message(self):
        """Verifies the uptime message formatting."""
        msg = self.formatter._build_uptime_message("1d 05h", "2026-03-28")
        self.assertIn("TEMPO DE ATIVIDADE", msg)
        self.assertIn("`1d 05h`", msg)
        self.assertIn("`2026-03-28`", msg)
    
    def test_build_sensor_graph_keyboard(self):
        """Validates that each sensor gets its own row in the graph keyboard."""
        sensors = [
            {"topic": "t1", "label": "L1"},
            {"topic": "t2", "label": "L2"}
        ]
        kb = self.formatter._build_sensor_graph_keyboard(sensors)
        
        self.assertEqual(len(kb), 2) # Duas linhas
        self.assertEqual(kb[0][0]['text'], "📈 L1")
        self.assertEqual(kb[1][0]['callback_data'], "view_graph_t2")

    def test_build_inline_keyboard_columns(self):
        """Tests the chunking logic for multi-column keyboards."""
        data = [
            {"text": "B1", "callback": "C1"},
            {"text": "B2", "callback": "C2"},
            {"text": "B3", "callback": "C3"}
        ]

        kb = self.formatter._build_inline_keyboard(data, columns=2)
        self.assertEqual(len(kb['inline_keyboard']), 2)
        self.assertEqual(len(kb['inline_keyboard'][0]), 2)
        self.assertEqual(len(kb['inline_keyboard'][1]), 1)

    def test_get_add_variable_type_menu(self):
        """Checks if the static menu has all required options."""
        menu = self.formatter._get_add_variable_type_menu()
        self.assertIn("AV_TYPE_NUM", str(menu))
        self.assertIn("AV_TYPE_BOOL", str(menu))
        self.assertEqual(menu['reply_markup'][
            'inline_keyboard'][-1][0]['text'], "❌ Cancelar")

    def test_format_add_var_success_admin(self):
        """Ensures the admin success message includes tag and interval."""
        state = {"description": "Tanque 1", "variable": "GVL.T1"}
        res = self.formatter._format_add_var_success_admin(state, 5.0)
        self.assertIn("Tanque 1", res['text'])
        self.assertIn("`GVL.T1`", res['text'])
        self.assertIn("5.0s", res['text'])

    def test_format_add_var_broadcast_group(self):
        """Checks the broadcast message for the team."""
        state = {"description": "Pressão"}
        res = self.formatter._format_add_var_broadcast_group(state, "Eng. Fernando")
        self.assertIn("Eng. Fernando", res['text'])
        self.assertIn("*Pressão*", res['text'])
    
    def test_get_remove_variable_type_menu(self):
        """Checks if removal menu has types and cancel option."""
        res = self.formatter._get_remove_variable_type_menu()
        kb = res['reply_markup']['inline_keyboard']
        self.assertEqual(kb[0][0]['callback_data'], 'RM_TYPE_NUM')
        self.assertEqual(kb[1][0]['callback_data'], 'CANCEL_ADD_VAR')

    def test_format_remove_list_menu_empty(self):
        """Tests the 'empty state' when no variables of a type are found."""
        res = self.formatter._format_remove_list_menu([], "bool")
        self.assertIn("📭 Nenhuma variável **digital**", res['text'])
        self.assertIn("RM_VAR_RETRY", str(res['reply_markup']))

    def test_format_remove_list_menu_with_data(self):
        """Ensures variable list is correctly turned into buttons."""
        vars_list = ["Temp_01", "Temp_02"]
        res = self.formatter._format_remove_list_menu(vars_list, "float")

        self.assertIn("variável **analógica**", res['text'])
        self.assertIn("RM_CONFIRM_Temp_01", str(res['reply_markup']))
        self.assertEqual(res['reply_markup']['inline_keyboard'][-1][0]['text'], "⬅️ Voltar")

    def test_get_remove_confirmation_menu(self):
        """Validates critical confirmation structure (1-column layout)."""
        res = self.formatter._get_remove_confirmation_menu("GVL.Engine_Speed")
        self.assertIn("GVL.Engine_Speed", res['text'])
        kb = res['reply_markup']['inline_keyboard']
        self.assertEqual(len(kb), 2)
        self.assertIn("RM_EXECUTE_", kb[0][0]['callback_data'])

    def test_format_remove_var_success_admin(self):
        """Checks the success message formatting for admin."""
        res = self.formatter._format_remove_var_success_admin("TAG1", "Motor")
        self.assertIn("REMOÇÃO CONCLUÍDA", res['text'])
        self.assertIn("TAG1", res['text'])
    
    def test_format_plc_ping_success(self):
        """Tests the formatting of a successful PLC ping result."""
        info = {'name': 'Main_PLC', 'ip': '192.168.0.10'}
        result = self.formatter._format_plc_ping_result(True, 25.5, info)
        
        assert "🟢" in result
        assert "Main_PLC" in result
        assert "25.50 ms" in result

    def test_format_plc_ping_failure(self):
        """Tests the formatting of a failed PLC ping result."""
        info = {'name': 'Mixer_PLC', 'ip': '192.168.0.15'}
        result = self.formatter._format_plc_ping_result(False, 0.0, info)
        
        assert "❌" in result
        assert "Mixer_PLC" in result
        assert "Firewall" in result

    def test_format_network_scan_empty(self):
        """Tests the formatting of a network scan result with no responsive 
            hosts."""
        result = self.formatter._format_network_scan_result([], "192.168.0")
        assert "Nenhum host respondeu" in result

    def test_format_network_scan_with_hosts(self):
        """Tests the formatting of a network scan result with responsive 
            hosts."""
        hosts = ["192.168.0.1", "192.168.0.5"]
        result = self.formatter._format_network_scan_result(hosts, "192.168.0")
        assert "Encontrado(s) *2*" in result
        assert "✅ `192.168.0.5`" in result

    def test_format_system_resources(self):
        """Tests the formatting of system resource metrics, including edge 
            cases like zero total disk space."""
        from collections import namedtuple
        Resource = namedtuple('Resource', ['percent', 'used', 'total'])
        metrics = {
            'cpu_percent': 45.0,
            'ram': Resource(percent=50.0, used=8*1024**3, total=16*1024**3),
            'disk': Resource(percent=20.0, used=0, total=0),
            'cpu_temp': 55.5,
            'uptime': '2 dias'
        }
        
        result = self.formatter._format_system_resources(metrics)
        assert "🟢 *Uso de CPU:* `45.0%`" in result
        assert "8.0/16.0 GB" in result
        assert "55.5°C" in result

    def test_format_cycles_report(self):
        """Tests the formatting of a cycles report."""
        data = {'Line_1': 150.7, 'Total_Production': 3000}
        result = self.formatter._format_cycles_report(data)
        
        assert "150 ciclos" in result
        assert "3000 unidades" in result
        assert "─" * 22 in result

    def test_format_cycles_report_invalid_data(self):
        """Tests the formatting of a cycles report with invalid data."""
        data = {'Error_Counter': "invalid"}
        result = self.formatter._format_cycles_report(data)
        assert "---" in result
    
    def test_format_maintenance_toggle_enabled(self):
        """Tests the formatting of the maintenance toggle when enabled."""
        result = self.formatter._format_maintenance_toggle(True)
        assert "🛠️ *MODO MANUTENÇÃO ATIVADO*" in result
        assert "Bloqueado" in result

    def test_format_maintenance_toggle_disabled(self):
        """Tests the formatting of the maintenance toggle when disabled."""
        result = self.formatter._format_maintenance_toggle(False)
        assert "✅ *MODO MANUTENÇÃO DESATIVADO*" in result
        assert "Ativo" in result

    def test_format_maintenance_report_success(self):
        """Tests the formatting of a maintenance report with successful results."""
        data = [
            {'label': 'Motor A', 'percent': 85.0, 'remaining': 150},
            {'label': 'Filtro B', 'percent': 95.0, 'remaining': 10}
        ]
        result = self.formatter._format_maintenance_report(data)
        assert "🟡 *Motor A*" in result
        assert "🔴 *Filtro B*" in result
        assert "85.0%" in result

    def test_format_commands_menu_structure(self):
        """Tests the formatting of the commands menu structure."""
        vars_config = [
            {'label': 'Start Pump', 'variable': 'GVL.start', 'type': 'pulse'}
        ]
        result = self.formatter._format_commands_menu(vars_config)
        assert "*PAINEL DE CONTROLE REMOTO*" in result['text']
        assert result['keyboard']['inline_keyboard'][0][0]['text'] == "⚡ Start Pump"
        assert "callback_data" in result['keyboard']['inline_keyboard'][0][0]

    def test_format_pulse_execution_nc(self):
        """Tests the formatting of a pulse execution for a Normally Closed 
            contact."""
        result = self.formatter._format_pulse_execution(
            "E-Stop", "Main_POU", True)
        assert "🔴 NF" in result
        assert "E-Stop" in result

    def test_format_pulse_execution_no(self):
        """Tests the formatting of a pulse execution for a Normally Open 
            contact."""
        # Test for Normally Open contact (initial_value=False)
        result = self.formatter._format_pulse_execution(
            "Start", "Main_POU", False)
        assert "🟢 NA" in result
    
    def test_format_toggle_status_verified(self):
        """Tests the formatting of a toggle status when verified."""
        result = self.formatter._format_toggle_status(
            "Pump_01", True, verified=True)
        assert "🔄 *ALTERAÇÃO DE ESTADO*" in result
        assert "🟢 `LIGADO`" in result
        assert "Comando confirmado pelo CLP" in result

    def test_format_toggle_status_failed(self):
        """Tests the formatting of a toggle status when the operation fails."""
        result = self.formatter._format_toggle_status(
            "Valve_02", False, verified=False)
        assert "⚠️ *TENTATIVA DE ALTERAÇÃO*" in result
        assert "O estado pode ter sido revertido por intertravamento local" in result

    def test_format_help_message_admin(self):
        """Tests the formatting of the help message for an admin user."""
        result = self.formatter._format_help_message(
            is_admin=True, maintenance_active=True)
        assert "Você tem acesso total." in result
        assert "(⚠️ ATIVO)" in result
        # Check if a restricted command is clickable (not inside backticks)
        assert "• /maintenance" in result

    def test_format_help_message_user(self):
        """Tests the formatting of the help message for a regular user."""
        result = self.formatter._format_help_message(is_admin=False)
        assert "restrito" in result
        # Restricted command should be inside backticks for non-admins
        assert "• `/maintenance`" in result

    def test_mask_email_valid(self):
        """Tests the masking of email addresses."""
        assert self.formatter._mask_email("fernando@domain.com") == "fer***@domain.com"
        assert self.formatter._mask_email("abc@test.com") == "abc***@test.com"

    def test_mask_email_invalid(self):
        """Tests the masking of invalid email addresses."""
        assert self.formatter._mask_email(None) == "***@***.com"
        assert self.formatter._mask_email("invalid-email") == "***@***.com"
    
    def test_format_log_status_processing(self):
        """Tests the formatting of the log status when processing."""
        result = self.formatter._format_log_status("processing")
        assert "PROCESSANDO LOGS" in result
        assert "compactando" in result

    def test_format_log_status_success(self):
        """Tests the formatting of the log status when successful."""
        email = "admin@company.com"
        result = self.formatter._format_log_status("success", detail=email)
        assert "BACKUP ENVIADO" in result
        assert email in result

    def test_prepare_log_email_content(self):
        """Tests the preparation of log email content."""
        content = self.formatter._prepare_log_email_content("John Doe", "Main_PLC")
        
        assert "Backup de Logs: Main_PLC" in content['subject']
        assert "Prezado(a) John Doe" in content['body']
        assert "Industrial Monitoring Bot" in content['body']
        assert "Python" in content['body']

    def test_format_log_rotation_message(self):
        """Tests the formatting of the log rotation message."""
        result = self.formatter._format_log_rotation_message()
        assert "ROTAÇÃO DE LOGS" in result
        assert "limpos" in result

    def test_format_cancel_message(self):
        """Tests the formatting of the cancel message."""
        result = self.formatter._format_cancel_message()
        assert "OPERAÇÃO CANCELADA" in result
        assert "pronto para novas instruções" in result

    def test_format_admin_update_success(self):
        """Tests the formatting of the admin update status when successful."""
        result = self.formatter._format_admin_update_status(True, 12345678)
        assert "NOVO ADMINISTRADOR REGISTRADO" in result
        assert "12345678" in result
        assert "YAML" in result

    def test_format_admin_update_failure(self):
        """Tests the formatting of the admin update status when failed."""
        result = self.formatter._format_admin_update_status(False, 12345678)
        assert "AVISO" in result
        assert "já consta na lista de administradores" in result

    def test_format_admin_removal_status(self):
        """Tests the formatting of the admin removal status."""
        result = self.formatter._format_admin_removal_status(98765432)
        assert "ACESSO REVOGADO" in result
        assert "98765432" in result
        assert "não pode mais executar" in result

    def test_format_shutdown_message(self):
        """Tests the formatting of the shutdown message."""
        from datetime import datetime
        result = self.formatter._format_shutdown_message()
        current_year = str(datetime.now().year)
        
        assert "MONITORAMENTO ENCERRADO" in result
        assert current_year in result
        assert "desconectado com sucesso" in result
    
if __name__ == '__main__':
    unittest.main(verbosity=2)