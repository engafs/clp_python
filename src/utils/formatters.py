import time
import platform
import os
from typing import Dict, Any, Optional, List
from datetime import datetime

class MonitoringFormatter:
    """
    Specialized class for formatting industrial monitoring alerts 
    across different communication channels.
    """
    @staticmethod
    def _format_value(value: Any, precision: int = 2) -> str:
        """
        Formats a raw sensor value into a standardized string representation.

        Args:
            value (Any): The raw data value (float, int, bool, etc.) to format.
            precision (int): Number of decimal places for float values.

        Returns:
            str: The formatted string (e.g., 'Ligado', '12.50', 'N/A').
        """
        if isinstance(value, bool):
            return "Ligado" if value else "Desligado"

        if isinstance(value, float):
            return f"{value:.{precision}f}"

        return str(value) if value is not None else "N/A"
    
    @staticmethod
    def _get_operator_text(op: str) -> str:
        """
        Translates mathematical operators to human-readable Portuguese text.

        Args:
            op (str): Mathematical operator string (e.g., '>=', '==').

        Returns:
            str: Translated text or the original operator if not found.
        """
        op_map: Dict[str, str] = {
            ">": "acima de", ">=": "maior ou igual a", "<": "abaixo de", 
            "<=": "menor ou igual a", "==": "igual a", "!=": "diferente de"
        }
        return op_map.get(op, op)

    def _get_alert_context(self, topic: str, value: Any, threshold: Any, 
                           op: str, label: Optional[str]) -> Dict[str, str]:
        """
        Prepares a standardized dictionary of formatted strings for alerts.

        Args:
            topic (str): MQTT sensor topic.
            value (Any): Current sensor reading.
            threshold (Any): Limit/Setpoint value.
            op (str): Mathematical operator used for comparison.
            label (Optional[str]): Friendly sensor name for display.

        Returns:
            Dict[str, str]: Dictionary containing pre-formatted alert strings.
        """
        return {
            'sensor_display': label if label else topic,
            'op_text': self._get_operator_text(op),
            'val_fmt': self._format_value(value),
            'lim_fmt': self._format_value(threshold),
            'curr_time': time.strftime('%H:%M:%S'),
            'full_date': time.strftime('%d/%m/%Y')
        }

    def _build_telegram_alert(
            self, topic: str, value: Any, threshold: Any, 
            op: str, label: Optional[str], plc_info: Dict[str, str]) -> str:
        """
        Constructs a Telegram Markdown alert message using the prepared context.

        Args:
            topic (str): MQTT sensor topic.
            value (Any): Current reading.
            threshold (Any): Setpoint limit.
            op (str): Operator string.
            label (Optional[str]): Friendly name.
            plc_info (Dict[str, str]): Dictionary with 'name' and 'ip' of the PLC.

        Returns:
            str: Formatted Markdown string for Telegram.
        """
        text: Dict[str, str] = self._get_alert_context(
            topic, value, threshold, op, label)

        return (
            f"🚨 *ALERTA DE SISTEMA*\n\n"
            f"📟 *CLP:* `{plc_info['name']}`\n🌐 *IP:* `{plc_info['ip']}`\n\n"
            f"🖥️ *Sensor:* `{text['sensor_display']}`\n"
            f"📊 *Leitura:* `{text['val_fmt']}` ({text['op_text']} `{text['lim_fmt']}`)\n"
            f"⏰ *Hora:* `{text['curr_time']}`"
        )

    def _build_email_alert(
            self, topic: str, value: Any, threshold: Any, 
            op: str, label: Optional[str], plc_info: Dict[str, str]) -> str:
        """
        Constructs a formal plain-text Email body for alert notifications.

        Args:
            topic (str): MQTT sensor topic.
            value (Any): Current sensor reading.
            threshold (Any): Limit/Setpoint value.
            op (str): Mathematical operator string.
            label (Optional[str]): Friendly sensor name.
            plc_info (Dict[str, str]): PLC metadata (name, ip).

        Returns:
            str: Plain-text email body.
        """
        text: Dict[str, str] = self._get_alert_context(
            topic, value, threshold, op, label)

        return (
            f"Prezado(a),\n\nO sistema identificou uma ocorrência:\n\n"
            f"📟 CLP: {plc_info['name']} | 🌐 IP: {plc_info['ip']}\n"
            f"📌 SENSOR: {text['sensor_display']}\n"
            f"📢 EVENTO: Valor ({text['val_fmt']}) está {text['op_text']} do limite ({text['lim_fmt']}).\n"
            f"🕒 DATA/HORA: {text['full_date']} {text['curr_time']}\n"
            f"🛠 TÓPICO (MQTT): {topic}\n\n"
            f"Por favor, verifique o estado do equipamento conforme os protocolos de segurança.\n"
            f"Este é um e-mail automático gerado pelo Sistema de Monitoramento Industrial. Favor não responder.")
    
    def _build_telegram_normalization(
            self, topic: str, value: Any, threshold: Any, 
            op: str, label: Optional[str], plc_info: Dict[str, str]) -> str:
        """
        Constructs a normalization message for Telegram using Markdown formatting.

        Args:
            topic (str): MQTT sensor topic.
            value (Any): Current sensor reading (now within normal range).
            threshold (Any): Limit/Setpoint that was previously breached.
            op (str): Mathematical operator string.
            label (Optional[str]): Friendly sensor name.
            plc_info (Dict[str, str]): PLC metadata (name, ip).

        Returns:
            str: Formatted Markdown string for Telegram.
        """
        text: Dict[str, str] = self._get_alert_context(
            topic, value, threshold, op, label)

        return (
            f"✅ *STATUS NORMALIZADO*\n\n"
            f"📟 *CLP:* `{plc_info['name']}`\n🌐 *IP:* `{plc_info['ip']}`\n\n"
            f"🖥️ *Sensor:* `{text['sensor_display']}`\n"
            f"📍 *Tópico (MQTT):* `{topic}`\n"
            f"⚙️ *Condição:* Valor `{text['op_text']} {text['lim_fmt']}` foi resolvida.\n"
            f"📈 *Valor Atual:* `{text['val_fmt']}`\n"
            f"🕒 *Horário:* `{text['curr_time']}`"
        )

    def _build_email_normalization(
            self, topic: str, value: Any, threshold: Any, 
            op: str, label: Optional[str], plc_info: Dict[str, str]) -> str:
        """
        Constructs a plain-text normalization body for Email by stripping Markdown.

        Returns:
            str: Plain-text email body without stars or backticks.
        """
        email_text: str = self._build_telegram_normalization(
            topic, value, threshold, op, label, plc_info)
        return email_text.replace("*", "").replace("`", "")

    def _build_auto_action_message(
            self, action_cfg: Dict[str, Any], trigger_topic: str) -> str:
        """
        Formats a Markdown message describing an automated PLC action.
        
        Args:
            action_cfg (Dict[str, Any]): Parameters like variable, type, value, reason.
            trigger_topic (str): The MQTT topic or sensor that caused the action.
            
        Returns:
            str: Formatted Markdown string for Telegram.
        """
        var_name: str = action_cfg.get('variable', 'N/A')
        action_type: str = action_cfg.get('type', 'bool')
        target_value: Any = action_cfg.get('value', False)
        reason: str = action_cfg.get('reason', 'Desconhecido')
        friendly_var_name: str = action_cfg.get('name', var_name)

        if action_type == 'pulse':
            icon, label, status = "⚡", "Comando", "PULSO DISPARADO"
        else:
            icon = "⚙️"
            label = "Equipamento"
            status = "🟢 LIGADO" if target_value else "🔴 DESLIGADO"

        return (
            f"🤖 *AÇÃO AUTOMÁTICA EXECUTADA*\n\n"
            f"{icon} *{label}:* `{friendly_var_name}`\n"
            f"🎯 *Tag:* `{var_name}`\n"
            f"🔹 *Status:* `{status}`\n"
            f"📍 *Origem:* `{trigger_topic}`\n"
            f"📝 *Motivo:* `{reason}`")
    
    def _format_cache_line(self, label: str, value: Any) -> str:
        """
        Formats a single sensor reading line for status reports using Markdown.

        Args:
            label (str): Friendly name or tag of the sensor/variable.
            value (Any): The current value to be formatted.

        Returns:
            str: A formatted line (e.g., '📍 *Temperature:* `25.50`').
        """
        display_value: str = self._format_value(value)
        return f"📍 *{label}:* `{display_value}`"

    def _group_by_pou(self, cache_items: dict) -> Dict[str, List[str]]:
        """
        Categorizes cached items by their PLC Program Organization Unit (POU).

        Args:
            cache_items (Dict[str, Any]): The internal values cache where keys are 
                topics and values are dictionaries containing 'value', 'pou', etc.

        Returns:
            Dict[str, List[str]]: A dictionary where keys are POU names and values 
                are lists of formatted strings for each variable.
        """
        grouped_data: Dict[str, List[str]] = {}

        for topic, data in cache_items.items():
            if not isinstance(data, dict):
                continue

            val: Any = data.get("value")
            label: str = data.get("label") or topic.split('/')[-1]
            pou: str = data.get("pou", "Global/Unknown")

            line: str = self._format_cache_line(label, val)

            if pou not in grouped_data:
                grouped_data[pou] = []
            grouped_data[pou].append(line)

        return grouped_data
    
    def _build_status_report(self, plc_info: Dict[str, str], 
                            grouped_vars: Dict[str, List[str]]) -> str:
        """
        Constructs the final monitoring report Markdown message for Telegram.

        Args:
            plc_info (Dict[str, str]): PLC metadata containing 'name' and 'ip'.
            grouped_vars (Dict[str, List[str]]): Variables already grouped by POU.

        Returns:
            str: Complete Markdown report with headers and sections.
        """
        msg_lines: List[str] = [
            "📊 *RELATÓRIO DE MONITORAMENTO*",
            f"🔲 *CLP:* `{plc_info['name']}`",
            f"🌐 *IP:* `{plc_info['ip']}`",
            "─" * 15 + "\n"
        ]

        for pou_name, lines in grouped_vars.items():
            msg_lines.append(f"📄 *ARQUIVO: {pou_name}*")
            msg_lines.extend(lines)
            msg_lines.append("")

        msg_lines.append(f"🕒 _Atualizado às: {time.strftime('%H:%M:%S')}_")
        
        return "\n".join(msg_lines)
    
    def _build_uptime_message(
            self, uptime_str: str, start_date_str: str) -> str:
        """
        Formats a clean uptime report for system health checks.

        Args:
            uptime_str (str): Human-readable duration (e.g., '2 days, 04:20:00').
            start_date_str (str): The date when the service started.

        Returns:
            str: Formatted Markdown string.
        """
        return (
            "⏱️ *TEMPO DE ATIVIDADE (UPTIME)*\n\n"
            f"🕒 *Online há:* `{uptime_str}`\n"
            f"📅 *Desde:* `{start_date_str}`")
    
    def _build_sensor_graph_keyboard(
            self,
            graphable_sensors: List[Dict[str, str]]) -> List[List[Dict[str, str]]]:
        """
        Constructs a Telegram Inline Keyboard for sensors that support charting.

        Args:
            graphable_sensors: List of dicts, each containing 'topic' and 'label'.

        Returns:
            List[List[Dict[str, str]]]: A list of rows, each containing a button.
        """
        keyboard: List[List[Dict[str, str]]] = []

        for sensor in graphable_sensors:
            button: List[Dict[str, str]] = [{
                "text": f"📈 {sensor['label']}",
                "callback_data": f"view_graph_{sensor['topic']}"
            }]
            keyboard.append(button)

        return keyboard
    
    @staticmethod
    def _get_add_variable_type_menu() -> Dict[str, Any]:
        """
        Returns the structure for the variable type selection menu in Telegram.

        Returns:
            Dict[str, Any]: Object containing 'text' and 'reply_markup'.
        """
        message: str = (
            "❓ *CONFIGURAÇÃO DE MONITORAMENTO*\n\n"
            "Selecione o tipo de dado que deseja monitorar no CLP:")
        
        buttons: List[List[Dict[str, str]]] = [
            [{"text": "🔢 Numérica (Analógica/Contadores)",
              "callback_data": "AV_TYPE_NUM"}],
            [{"text": "🔘 Booleana (Digital)",
              "callback_data": "AV_TYPE_BOOL"}],
            [{"text": "❌ Cancelar",
              "callback_data": "CANCEL_ADD_VAR"}]]
        
        return {"text": message, "reply_markup": {"inline_keyboard": buttons}}
    
    @staticmethod
    def _build_inline_keyboard(
            buttons_data: List[List[Dict[str, str]]], 
            columns: int = 2) -> Dict[str, List[List[Dict[str, str]]]]:
        """
        Generates a standardized Telegram inline keyboard with a fixed number of columns.

        Args:
            buttons_data: List of dicts with {'text': '...', 'callback': '...'}.
            columns: Number of buttons per row. Defaults to 2.

        Returns:
            Dict[str, List[List[Dict[str, str]]]]: Formatted inline_keyboard structure.
        """
        keyboard: List[List[Dict[str, str]]] = []
        
        for i in range(0, len(buttons_data), columns):
            row = []
            for item in buttons_data[i:i + columns]:
                row.append({
                    "text": item['text'], "callback_data": item['callback']
                })
            keyboard.append(row)
            
        return {"inline_keyboard": keyboard}
    
    @staticmethod
    def _format_add_var_success_admin(
        state: Dict[str, Any], interval: float) -> Dict[str, Any]:
        """
        Formats a detailed confirmation message for the Administrator.

        Args:
            state (Dict[str, Any]): The temporary state of the variable being added.
            interval (float): The configured polling interval.

        Returns:
            Dict[str, Any]: Formatted message object.
        """
        message: str = (
            "✅ *Monitoramento Ativado!*\n\n"
            f"📝 *Descrição:* {state['description']}\n"
            f"🏷️ *Tag CLP:* `{state['variable']}`\n"
            f"⏱️ *Intervalo:* {interval}s\n\n"
            "A variável já está sendo processada em tempo real."
        )
        return {"text": message, "parse_mode": "Markdown"}

    @staticmethod
    def _format_add_var_broadcast_group(
        state: Dict[str, Any], user_name: str) -> Dict[str, Any]:
        """
        Formats a summary broadcast message for the operational group.

        Args:
            state (Dict[str, Any]): The state of the newly added variable.
            user_name (str): The name of the user who performed the action.

        Returns:
            Dict[str, Any]: Formatted message object.
        """
        message: str = (
            f"📢 *SISTEMA ATUALIZADO*\n"
            f"A variável *{state['description']}* foi adicionada ao "
            f"monitoramento por `{user_name}`.\n"
            "Use `/status` para visualizar os novos dados.")
        
        return {"text": message, "parse_mode": "Markdown"}
    
    @staticmethod
    def _get_remove_variable_type_menu() -> Dict[str, Any]:
        """
        Generates the visual structure for the variable removal start menu.

        Returns:
            Dict[str, Any]: Telegram message object with type selection buttons.
        """
        message: str = (
            "🗑️ **REMOVER MONITORAMENTO**\n\n"
            "Para listar as tags configuradas, selecione o tipo da variável "
            "que deseja excluir do sistema:")
        
        buttons_data: List[Dict[str, str]] = [
            {'text': '🔢 Numérica', 'callback_data': 'RM_TYPE_NUM'},
            {'text': '🔘 Booleana', 'callback_data': 'RM_TYPE_BOOL'},
            {'text': '❌ Cancelar', 'callback_data': 'CANCEL_ADD_VAR'}]
        
        markup: Dict[str, List[Dict[str, str]]] = {
            'inline_keyboard': [
                [buttons_data[0], buttons_data[1]], [buttons_data[2]]]}
        
        return {"text": message, "reply_markup": markup}
    
    @staticmethod
    def _format_remove_list_menu(filtered_vars: List[str], target_type: str) -> Dict[str, Any]:
        """
        Generates a selection menu for removing specific variables in a 2-column grid.

        Args:
            filtered_vars (List[str]): List of variable names found in the system.
            target_type (str): The data type category (bool, int, float).

        Returns:
            Dict[str, Any]: Telegram message with the list of variables or empty state.
        """
        industrial_name_types: Dict[str, str] = \
            {'bool': 'digital', 'int': 'analógica', 'float': 'analógica'}
                
        formated_type = industrial_name_types.get(
            target_type.lower(), 'específica')
        if not filtered_vars:
            return {
                "text":
                f"📭 Nenhuma variável **{formated_type}** encontrada no monitoramento.",
                "reply_markup":
                {"inline_keyboard": [[{"text": "⬅️ Voltar", "callback_data": "RM_VAR_RETRY"}]]}
            }

        message: str = f"📋 Selecione a variável **{formated_type}** para remover do sistema:"
        
        btn_list: List[Dict[str, str]] = [
            {'text': f"➖ {v}", 'callback': f"RM_CONFIRM_{v}"} 
            for v in filtered_vars]
        
        markup: Dict[str, List[List[Dict[str, str]]]] = \
            MonitoringFormatter._build_inline_keyboard(btn_list, columns=2)
        markup['inline_keyboard'].append(
            [{"text": "⬅️ Voltar", "callback_data": "RM_VAR_RETRY"}])
        
        return {"text": message, "reply_markup": markup}
    
    @staticmethod
    def _get_remove_confirmation_menu(var_name: str) -> Dict[str, Any]:
        """
        Generates a critical confirmation menu for variable deletion.

        Args:
            var_name (str): The name of the variable to be removed.

        Returns:
            Dict[str, Any]: Telegram message object with confirmation buttons.
        """
        message: str = (
            f"⚠️ **CONFIRMAÇÃO DE EXCLUSÃO**\n\n"
            f"Você tem certeza que deseja remover a variável `{var_name}`?\n\n"
            "❗ *Impacto:* O registro será apagado do arquivo YAML e o "
            "monitoramento em tempo real será interrompido imediatamente.")
        
        buttons_data: List[Dict[str, str]] = [
            {'text': '✅ Sim, Confirmar Exclusão',
             'callback': f"RM_EXECUTE_{var_name}"},
            {'text': '❌ Não, Abortar',
             'callback': 'CANCEL_ADD_VAR'}]
        
        markup: Dict[str, List[List[Dict[str, str]]]] = \
            MonitoringFormatter._build_inline_keyboard(
            buttons_data, columns=1)
        
        return {"text": message, "reply_markup": markup}
    
    @staticmethod
    def _format_remove_var_success_admin(
        variable: str, friendly_name: str) -> Dict[str, Any]:
        """
        Formats a final removal confirmation for the Administrator.

        Args:
            variable (str): PLC Tag name.
            friendly_name (str): The label/description of the variable.
        
        Returns:
            Dict[str, Any]: Formatted message object confirming the removal.
        """
        message: str = (
            f"✅ *REMOÇÃO CONCLUÍDA*\n\n"
            f"A tag `{variable}` ({friendly_name}) foi completamente removida "
            "do loop de execução e do arquivo de configuração.")
        
        return {"text": message, "parse_mode": "Markdown"}

    @staticmethod
    def _format_remove_var_broadcast_group(
        friendly_name: str, user_name: str) -> Dict[str, Any]:
        """
        Formats a removal broadcast alert for the operational team.

        Args:
            friendly_name (str): Label of the removed variable.
            user_name (str): User who executed the removal.
        """
        message: str = (
            f"🗑️ *VARIÁVEL REMOVIDA*\n\n"
            f"A variável *{friendly_name}* foi excluída do sistema por `{user_name}`.\n"
            "Use `/status` para visualizar as variáveis ativas no momento.")

        return {"text": message, "parse_mode": "Markdown"}
    
    @staticmethod
    def _format_plc_ping_result(
        is_alive: bool, latency_ms: float, plc_info: Dict[str, Any]) -> str:
        """
        Formats the diagnostic response for a PLC ping test.
        
        Args:
            is_alive: Connectivity status.
            latency_ms: Response time in milliseconds.
            plc_info: Metadata containing 'name' and 'ip'.
        
        Returns:
            str: Formatted Markdown string for Telegram.
        """
        plc_name: str = plc_info.get('name', 'CLP')
        plc_ip: str = plc_info.get('ip', '0.0.0.0')

        if not is_alive:
            return (
                "❌ *FALHA DE COMUNICAÇÃO*\n\n"
                f"O dispositivo `{plc_name}` ({plc_ip}) não respondeu.\n\n"
                "⚠️ *Sugestões:*\n"
                "• Verifique cabos de rede (RJ45).\n"
                "• Confirme se o Servidor OPC UA está rodando.\n"
                "• Verifique regras de Firewall no Windows.")

        status_icon: str = "🟢" if latency_ms < 50 else "🟡" \
            if latency_ms < 150 else "🔴"

        return (
            f"{status_icon} *PONG: CONEXÃO ATIVA*\n\n"
            f"📟 *Dispositivo:* `{plc_name}`\n"
            f"🌐 *Endereço:* `{plc_ip}`\n"
            f"⏱️ *Latência:* `{latency_ms:.2f} ms`\n"
            f"📡 *Protocolo:* `OPC UA`")
    
    @staticmethod
    def _format_network_scan_result(
        active_hosts: List[str], ip_prefix: str) -> str:
        """
        Formats the network scan results into a human-readable Telegram message.

        This method takes a list of reachable IP addresses and generates a 
        Markdown-formatted string. It includes different status indicators 
        for when hosts are found versus when the scan returns empty.

        Args:
            active_hosts (List[str]): A list of active IP address strings 
                found during the network scan.
            ip_prefix (str): The common prefix of the scanned IP range (e.g., '192.168.1').

        Returns:
            str: A formatted Markdown string ready for the Telegram UI.
        """
        if not active_hosts:
            return (
                f"⚠️ *VARREDURA CONCLUÍDA*\n\n"
                f"Nenhum host respondeu na faixa `{ip_prefix}.1-21`.\n"
                "Verifique se o bot está na mesma sub-rede dos dispositivos."
            )
        
        count: int = len(active_hosts)
        hosts_str: str = "\n".join([f"✅ `{h}`" for h in active_hosts])
        
        return (
            f"🛰️ *VARREDURA CONCLUÍDA*\n\n"
            f"Encontrado(s) *{count}* host(s) ativos na rede `{ip_prefix}.x`:\n\n"
            f"{hosts_str}\n\n"
            f"🕒 _Scan finalizado em tempo real._")
    
    @staticmethod
    def _format_system_resources(metrics: Dict[str, Any]) -> str:
        """
        Formats the collected system metrics into a human-readable Telegram message.

        This method applies logic to assign status icons (Green, Yellow, Red) based 
        on resource utilization thresholds and handles optional data like CPU 
        temperature.

        Args:
            metrics (Dict[str, Any]): A dictionary containing 'cpu_percent', 'ram', 
                'disk', 'cpu_temp', and 'uptime' data.

        Returns:
            str: A formatted Markdown string representing the system resource report.
        """
        cpu: float = metrics['cpu_percent']
        ram: Any = metrics['ram']
        disk: Any = metrics['disk']
        temp: Any = metrics['cpu_temp']
        uptime = metrics['uptime']

        cpu_icon = "🔴" if cpu > 85 else "🟡" if cpu > 60 else "🟢"
        ram_icon = "🔴" if ram.percent > 90 else "🟡" if ram.percent > 70 else "🟢"
        disk_icon = "⚠️" if disk.percent > 90 else "💾"

        ram_used_gb = ram.used / (1024**3)
        ram_total_gb = ram.total / (1024**3)

        temp_msg: str = f"\n🌡️ *Temperatura CPU:* `{temp:.1f}°C`" if temp else ""

        message: str = (
            "🖥️ *RECURSOS DO SISTEMA*\n\n"
            f"{cpu_icon} *Uso de CPU:* `{cpu}%`\n"
            f"{ram_icon} *Memória RAM:* `{ram.percent}%` (`{ram_used_gb:.1f}/{ram_total_gb:.1f} GB`)\n"
            f"{disk_icon} *Espaço em Disco:* `{disk.percent}%` ocupado\n"
            f"{temp_msg}\n"
            f"⏱️ *Uptime do Host:* `{uptime}`")

        return message
    
    @staticmethod
    def _format_cycles_report(cycle_data: Dict[str, Any]) -> str:
        """
        Formats operational cycle counters from the PLC into a readable Telegram report.

        This method takes a dictionary of counters, sanitizes the numeric values 
        to ensure they are displayed as integers, and adds a timestamp for 
        operational synchronization.

        Args:
            cycle_data (Dict[str, Union[int, float, str, None]]): A dictionary where 
                keys are counter labels (e.g., 'Palletizer') and values are the 
                current counts from the PLC.

        Returns:
            str: A formatted Markdown string containing the operation counters 
                report and a synchronization timestamp.
        """
        if not cycle_data:
            return "ℹ️ *Informação:* Nenhum contador de ciclos ou produção identificado no cache atual."

        header: str = "🔄 *RELATÓRIO DE PRODUÇÃO*\n" + "─" * 22 + "\n"
        
        lines = []
        for label in sorted(cycle_data.keys()):
            value = cycle_data[label]
            try:
                val_fmt = f"{int(float(value))}" if value is not None else "0"
            except (ValueError, TypeError):
                val_fmt = "---"
                
            lines.append(
                f"📦 *{label}:* `{val_fmt} unidades`" if "total" in \
                label.lower() else f"🔢 *{label}:* `{val_fmt} ciclos`")
        
        actual_time = time.strftime('%d/%m/%Y %H:%M:%S')
        footer = f"\n\n🕒 _Sincronizado com o CLP às {actual_time}_"
        
        return header + "\n".join(lines) + footer
    
    @staticmethod
    def _format_maintenance_toggle(is_active: bool) -> str:
        """
        Generates a formatted status message for the system's maintenance mode.

        This method provides visual feedback to the user, explaining the 
        implications of the current state (e.g., whether alarms are silenced 
        or active monitoring has resumed).

        Args:
            is_active (bool): The current state of the maintenance mode. 
                True if enabled, False if disabled.

        Returns:
            str: A formatted Markdown string containing the status update 
                and operational warnings.
        """
        if is_active:
            return (
                "🛠️ *MODO MANUTENÇÃO ATIVADO*\n"
                "───────────────────\n"
                "⚠️ *Status:* Alarmes Silenciados\n"
                "⚠️ *Status:* Comandos de Escrita Bloqueados\n\n"
                "Não esqueça de desativar ao finalizar o serviço para retomar o monitoramento!"
            )
        return (
            "✅ *MODO MANUTENÇÃO DESATIVADO*\n"
            "───────────────────\n"
            "🔔 *Status:* Monitoramento Ativo\n"
            "🔔 *Status:* Alertas de Segurança Habilitados")

    @staticmethod
    def _format_maintenance_report(data_list: List[Dict[str, Any]]) -> str:
        """
        Formats the maintenance prediction data into a structured Telegram report.

        This method generates a visual dashboard using Markdown, color-coded 
        status icons based on usage thresholds (70% and 90%), and detailed 
        remaining life metrics for each monitored component.

        Args:
            data_list (List[Dict[str, Any]]): A list of dictionaries containing 
                maintenance KPIs (label, percent, remaining, etc.) from the 
                calculation engine.

        Returns:
            str: A formatted Markdown string for the maintenance dashboard UI.
        """
        if not data_list:
            return "ℹ️ *Informação:* Nenhum alvo de manutenção configurado no YAML."

        header = "🛠️ *PREVISÃO DE MANUTENÇÃO*\n" + "─" * 22 + "\n"
        
        lines = []
        for item in data_list:
            # Lógica de cores: Verde < 70% | Amarelo < 90% | Vermelho >= 90%
            percent = item['percent']
            icon = "🟢" if percent < 70 else "🟡" if percent < 90 else "🔴"
            
            lines.append(
                f"{icon} *{item['label']}*\n"
                f"└ Uso: `{percent:.1f}%` do limite\n"
                f"└ Restante: `{int(item['remaining'])}` unidades"
            )
        
        footer = "\n\n💡 _Dica: Programe a parada ao atingir 90%._"
        return header + "\n\n".join(lines) + footer
    
    @staticmethod
    def _format_commands_menu(variables: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Builds the interactive Inline Keyboard menu for PLC commands.

        This method dynamically generates buttons based on the provided variable
        configurations, allowing users to send real-time commands to the PLC.
        Each button is labeled with an appropriate icon and description for clarity.

        Args:
            variables (List[Dict[str, Any]]): A list of variable configurations from the YAML
            file, each containing 'variable', 'type', 'description', etc. 
        Returns:
            Dict[str, Any]: A structured message object containing the text and
            keyboard layout for Telegram. If no variables are provided,
            it returns an error message.
        """
        if not variables:
            return {
                "text": "❌ *ERRO DE CONFIGURAÇÃO*\nNenhum comando interativo foi encontrado no arquivo YAML.",
                "keyboard": None}

        inline_keyboard: List[List[Dict[str, Any]]] = []
        
        for var in variables:
            label: str = var.get('description') or var.get('label') or var.get('variable', 'Unknown')
            var_name: str = var.get('variable', '')
            var_type: str = var.get('type', 'bool')

            icon: str = "⚡" if var_type == 'pulse' else "🔘"
            
            button: Dict[str, Any] = {
                "text": f"{icon} {label}",
                "callback_data": f"/set_var {var_name}"}
            
            inline_keyboard.append([button])
        
        cancel_button: Dict[str, str] = {
        "text": "❌ Cancelar Operação",
        "callback_data": "CANCEL_CMD_MENU"}
        
        inline_keyboard.append([cancel_button])

        message: str = (
            "🕹️ *PAINEL DE CONTROLE REMOTO*\n"
            "────────────────────────\n"
            "Selecione um comando abaixo para interagir com o CLP em tempo real:")

        return {
            "text": message,
            "keyboard": {"inline_keyboard": inline_keyboard},
            "parse_mode": "Markdown"}
    
    @staticmethod
    def _format_pulse_execution(
        friendly_name: str, pou_name: str, initial_value: bool) -> str:
        """
        Formats the Telegram notification for a successful pulse command.
        
        Args:
            friendly_name (str): Human-readable name of the variable.
            pou_name (str): The PLC Program Organization Unit name.
            initial_value (bool): The state of the variable before the pulse.
            
        Returns:
            str: A formatted Markdown message.
        """
        contact_type: str = "🔴 NF (Emergência/Parada)" if initial_value \
            else "🟢 NA (Comando/Partida)"
        
        return (
            f"⚡ *COMANDO EXECUTADO COM SUCESSO*\n"
            f"────────────────────────\n"
            f"🎯 *Alvo:* `{friendly_name}`\n"
            f"📂 *POU:* `{pou_name}`\n"
            f"🎛️ *Tipo de Contato:* {contact_type}\n\n"
            f"✅ _O estado original foi restaurado após 500ms._")
    
    @staticmethod
    def _format_toggle_status(
        friendly_name: str, new_value: bool, verified: bool = True) -> str:
        """
        Formats the Telegram notification for a successful state toggle.
        
        Args:
            friendly_name (str): Human-readable name of the actuator/variable.
            new_value (bool): The new state applied to the variable.
            verified (bool): Whether the new state was confirmed by the PLC read-back.
            
        Returns:
            str: A formatted Markdown message with status icons.
        """
        status_icon: str = "🟢" if new_value else "🔴"
        status_text: str = "LIGADO" if new_value else "DESLIGADO"
        
        header: str = "🔄 *ALTERAÇÃO DE ESTADO*"
        if not verified:
            header: str = "⚠️ *TENTATIVA DE ALTERAÇÃO*"
            status_text += " (Falha na Verificação)"

        return (
            f"{header}\n"
            f"────────────────────────\n"
            f"📍 *Atuador:* `{friendly_name}`\n"
            f"📊 *Status:* {status_icon} `{status_text}`\n\n"
            f"{'✅ Comando confirmado pelo CLP.' if verified else '❌ O estado pode ter sido revertido por intertravamento local.'}"
        )
    
    @staticmethod
    def _format_help_message(is_admin: bool, maintenance_active: bool = False) -> str:
        """
        Constructs a comprehensive Markdown help guide for the system commands.

        This method generates a structured menu divided into public operational 
        commands and restricted administrative functions. It dynamically checks 
        the maintenance mode status to alert admins if notifications are currently 
        suppressed.

        Args:
            is_admin (bool): Determines if the user has administrative
            privileges to access certain commands.
            maintenance_active (bool): Indicates if the maintenance mode is 
            currently active, which silences alerts and blocks write commands.

        Returns:
            str: A formatted Markdown string containing the command list, 
                access levels, and operational tips.
        """
        man_status: str = " (⚠️ ATIVO)" if maintenance_active else ""
        
        header: str = "📖 *GUIA DE COMANDOS DO SISTEMA*\n"
        separator: str = "─" * 22 + "\n"
        
        public_section: str = (
            "🌐 *OPERACIONAL (Público)*\n"
            "• /status — Estado atual dos sensores\n"
            "• /ping — Latência OPC UA\n"
            "• /uptime — Tempo de atividade\n"
            "• /cancel — Cancela operação em curso 🔄\n\n")
        
        # Admins commands (Clickable only admins)
        def fmt(cmd: str) -> str:
            if is_admin:
                return cmd.replace("_", r"\_")
            else:
                return f"`{cmd}`"

        admin_section = (
            "🛡️ *GERENCIAMENTO (Restrito)*\n"
            f"• {fmt('/maintenance')} — Modo silencioso do bot{man_status}\n"
            f"• {fmt('/add_var')} — Adicionar variável do sensor/autuador para monitoramento\n"
            f"• {fmt('/rm_var')} — Remover variável do sensor/autuador para monitoramento\n"
            f"• {fmt('/next_maintenance')} — Vida útil de componentes\n"
            f"• {fmt('/resources')} — Saúde do servidor (CPU/RAM)\n"
            f"• {fmt('/cycles')} — Contadores de acionamento\n"
            f"• {fmt('/graph')} — Tendência histórica\n"
            f"• {fmt('/commands')} — Painel de Controle\n"
            f"• {fmt('/scan')} — Varredura de rede industrial\n"
            f"• {fmt('/logs')} — Exportar logs via e-mail\n"
            f"• {fmt('/stop_system')} — Encerrar monitoramento\n\n")
        
        footer = (
            f"⚠️ *Acesso:* {'Você tem acesso total.' if is_admin else 'Comandos em 🛡️ são restritos.'}\n"
            "💡 _Dica: Comandos azuis são clicáveis para execução rápida._")
        
        return header + separator + public_section + admin_section + footer
    
    @staticmethod
    def _mask_email(email: str) -> str:
        """
        Masks an email address for privacy in Telegram notifications.
        Example: user@test.com -> use***@test.com

        Args:
            email (str): The email address to be masked.
        Returns:
            str: The masked email address.
        """
        try:
            user_part, domain_part = email.split('@')
            if len(user_part) <= 3:
                return f"{user_part}***@{domain_part}"
            return f"{user_part[:3]}***@{domain_part}"
        except (ValueError, AttributeError):
            return "***@***.com"

    @staticmethod
    def _format_log_status(status: str, detail: str = "") -> str:
        """Formats the status messages for the log export process, providing 
        clear feedback on each step.
        Args:
            status (str): The current status of the log export process 
            (e.g., 'processing', 'success', 'no_logs', 'denied', 'error').
            detail (str): Additional information to include in the message,
            such as the recipient's email or error details.
        Returns:
            str: The formatted status message.
        """
        messages: Dict[str, str] = {
            "processing": (
                "📦 *PROCESSANDO LOGS*\n───────────────────\n"
                "Aguarde, estou compactando os arquivos do servidor..."),
            "success": (
                "✅ *BACKUP ENVIADO*\n───────────────────\n"
                f"O arquivo foi disparado com sucesso para:\n`{detail}`"),
            "no_logs": (
                "⚠️ *AVISO*\nNenhum arquivo de log foi encontrado na "
                "pasta do sistema."),
            "denied": (
                f"🚫 *ACESSO NEGADO*\nOlá {detail}, você não tem permissão "
                "para receber os logs do CLP via e-mail."),
            "error": (
                "❌ *ERRO NO BACKUP*\nFalha interna ao processar ou enviar o "
                "e-mail de logs.")
        }
        return messages.get(status, "Ocorreu um erro desconhecido.")
    
    @staticmethod
    def _prepare_log_email_content(
        user_name: str, plc_name: str) -> Dict[str, str]:
        """
        Prepares the subject and body content for the log export email,
        including dynamic information about the PLC and the requestor for
        better traceability.
        Args:
            user_name (str): The name of the user requesting the logs.
            plc_name (str): The name of the PLC for which logs are being exported.
        Returns:
            Dict[str, str]: A dictionary containing the 'subject' and 'body'
            of the email.
        """
        timestamp: str = time.strftime('%d/%m/%Y às %H:%M:%S')
        
        subject: str = (
            f"🛠️ Backup de Logs: {plc_name} ({time.strftime('%d/%m/%Y')})")
        
        body: str = (
            f"Prezado(a) {user_name},\n\n"
            f"Conforme sua solicitação via Telegram, segue em anexo o pacote "
            f"de logs do sistema industrial '{plc_name}'.\n\n"
            f"📅 Gerado em: {timestamp}\n"
            f"🖥️ Servidor: {os.name.upper()} ({platform.node()})\n"
            f"🐍 Runtime: Python {platform.python_version()}\n\n"
            "--- \n"
            "Esta é uma mensagem automática gerada pelo Industrial Monitoring Bot."
        )
        
        return {"subject": subject, "body": body}
    
    @staticmethod
    def _format_log_rotation_message() -> str:
        """
        Formats the confirmation message for successful log rotation, 
        indicating that the system is ready for a new cycle of log entries.
        Returns:
            str: A formatted Markdown message confirming log rotation.
        """
        return (
            "♻️ *ROTAÇÃO DE LOGS*\n"
            "───────────────────\nArquivos de log "
            "limpos com sucesso para um novo ciclo de registros.")
    
    @staticmethod
    def _format_cancel_message() -> str:
        """
        Formats the cancellation confirmation message, providing clear feedback
        that the current operation has been aborted and the system is ready 
        for new instructions.
        Returns:
            str: A formatted Markdown message confirming the cancellation.
        """
        return (
            "🔄 *OPERAÇÃO CANCELADA*\n"
            "───────────────────\nA configuração ou "
            "comando em curso foi abortado com sucesso.\n\nO sistema limpou "
            "seu cache de estado e está pronto para novas instruções. 🕹️")
    
    @staticmethod
    def _format_admin_update_status(success: bool, admin_id: int) -> str:
        """
        Format the message for the result of adding a new administrator, 
        providing clear feedback on the operation's outcome.
        Args:
            success (bool): Indicates whether the admin was successfully added.
            admin_id (int): The ID of the administrator.
        Returns:
            str: A formatted Markdown message indicating the result of the operation.
        """
        if success:
            return (
                "🛡️ *NOVO ADMINISTRADOR REGISTRADO*\n"
                "──────────────────────────\n"
                f"👤 *ID:* `{admin_id}`\n"
                "✅ *Status:* Privilégios concedidos e salvos no YAML.\n\n"
                "💡 _O novo admin já pode utilizar comandos restritos._")
        return (
            f"⚠️ *AVISO*\nO ID `{admin_id}` já consta na lista de "
            "administradores ou ocorreu um erro na persistência dos dados.")
    
    @staticmethod
    def _format_admin_removal_status(admin_id: int) -> str:
        """
        Format the message for the result of removing an administrator, 
        providing clear feedback on the operation's outcome.
        Args:
            admin_id (int): The ID of the administrator.
        Returns:
            str: A formatted Markdown message indicating the result of the operation.
        """
        return (
            "🛡️ *ACESSO REVOGADO*\n"
            "──────────────────────────\n"
            f"👤 *ID:* `{admin_id}`\n"
            "❌ *Status:* Privilégios administrativos removidos.\n\n"
            "⚠️ _Este usuário não pode mais executar comandos restritos._")
    
    def _format_shutdown_message(self) -> str:
        """
        Generates a formatted string for the system shutdown notification.
        Returns:
            str: A formatted Markdown message indicating that the monitoring has been stopped.
        """
        now = datetime.now().strftime('%d/%m/%Y às %H:%M:%S')
        
        return (
            "🏁 *MONITORAMENTO ENCERRADO*\n\n"
            f"📅 *Data:* `{now}`\n"
            "🔌 *Status:* O bot foi desconectado com sucesso.\n\n"
            "👋 Até a próxima operação!")