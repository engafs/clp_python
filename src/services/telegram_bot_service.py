from src.utils.logging_config import setup_logging
from typing import Callable, Dict, List, Optional, Any, Tuple, Union
import requests
import io
import json
import threading
import os
import time
from logging import Logger


class TelegramBotService:
    """
    Service to manage Telegram Bot interactions, including command menus,
    polling for updates, and administrative task handling.
    """

    def __init__(self, token: str, chat_id: str,
                 admin_ids: Optional[List[Union[int, str]]] = None) -> None:
        """
        Initializes the TelegramBotService.

        Args:
            token: Telegram Bot API Token.
            chat_id: Target Chat/Group ID where the bot will operate.
            admin_ids: Initial list of user IDs with administrative privileges.
                       If None, tries to load from TELEGRAM_ADMIN_IDS env var.

        Raises:
            ValueError: If token or chat_id are missing.
        """
        self._token: str = token
        self._chat_id: str = chat_id
        self._base_url: str = f"https://api.telegram.org/bot{self._token}"
        self._last_update_id: int = 0
        self._logger: Logger = setup_logging(
            file_name="telegram_log.txt", logger_name="TELEGRAM_LOG")

        if not self._token or not self._chat_id:
            raise ValueError(
                "❌ Erro crítico: TELEGRAM_TOKEN e/ou TELEGRAM_CHAT_ID não"
                " encontrado(s) no arquivo de configuração geral.")

        # Admin Loading Logic
        self._admin_ids: Tuple[int, ...] = self._load_admins(admin_ids)
        
        self._session: requests.Session = requests.Session()
        self._is_ready: bool = self._check_bot_connection()
        self._pool_commands_running: bool = False

        if self._is_ready:
            self._post_commands_menu()

    def _load_admins(
            self,admin_ids: Optional[List[Union[int, str]]]) -> Tuple[int, ...]:
        """
        Loads admin IDs from arguments or environment variables.

        Args:
            admin_ids: List of IDs provided during initialization.

        Returns:
            A tuple of unique integer admin IDs.
        """
        if admin_ids and isinstance(admin_ids, list):
            return tuple(int(i) for i in admin_ids)
        
        env_admins: str = os.getenv("TELEGRAM_ADMIN_IDS", "")
        if env_admins:
            return tuple(
                int(i.strip()) for i in env_admins.split(",") if i.strip())
        
        self._logger.warning("⚠️ No admins loaded.")
        return ()

    def _is_active_admin_in_group(self, user_id: int) -> bool:
        """
        Checks with Telegram API if a user is still an administrator in the group.

        Args:
            user_id: The Telegram user ID to verify.

        Returns:
            True if the user is a creator or administrator, False otherwise.
        """
        if user_id not in self._admin_ids:
            return False

        url: str = f"{self._base_url}/getChatMember"
        params: Dict[str, Union[str, int]] = {
            "chat_id": self._chat_id, "user_id": user_id}
        
        try:
            response: requests.Response = self._session.get(
                url, params=params, timeout=5)
            if response.status_code == 200:
                data: Dict[str, Any] = response.json()
                status: Optional[str] = data.get("result", {}).get("status")
                return status in ["creator", "administrator"]
            
            self._logger.warning(
                f"⚠️ Can't possible validate {user_id} user status in group.")
            return False
        except Exception as e:
            self._logger.error(f"❌ Erro validate member in group: {e}")
            return False

    def _check_bot_connection(self) -> bool:
        """
        Validates the Telegram Bot token by calling the getMe method.

        Returns:
            True if connection is successful, False otherwise.
        """
        try:
            response: requests.Response = requests.get(
                f"{self._base_url}/getMe", timeout=5)
            if response.status_code == 200:
                self._logger.info("✅ Telegram Token validated.")
                return True
            self._logger.error(
                f"❌ Invalid Telegram Token: {response.status_code}")
            return False
        except Exception as e:
            self._logger.error(f"❌ Telegram connection failed: {e}")
            return False
    
    def _get_public_commands(self) -> List[Dict[str, str]]:
        """
        Returns the list of commands available to all group members.

        Returns:
            List of dictionaries containing 'command' and 'description'.
        """
        return [
            {"command": "status",
             "description": "📊 Ver informações dos sensores."},
            {"command": "uptime",
             "description": "⏱️ Tempo de atividade do sistema."},
            {"command": "ping",
             "description": "📡 Latência OPC UA."},
            {"command": "help",
             "description": "❓ Ajuda e informações."},
             {"command": "cancel",
             "description": "❌ Cancelar comando em execução."},
        ]

    def _get_admin_commands(self) -> List[Dict[str, str]]:
        """
        Returns the list of commands available only to administrators.

        Returns:
            Combined list of public and administrative commands.
        """
        return self._get_public_commands() + [
            {"command": "add_var",
             "description": "📥 Adicionar variável para monitoramento."},
            {"command": "rm_var",
             "description": "📤 Remover variável do monitoramento."},
            {"command": "resources",
             "description": "🖥️ Informações da máquina/host."},
            {"command": "maintenance",
             "description": "🛠️ Configurar notificações/alertas."},
            {"command": "next_maintenance",
             "description": "⏳ Mostrar tempo de vida útil dos sensores."},
            {"command": "cycles",
             "description":"⏲️ Mostrar contagem de eventos."},
            {"command": "commands",
             "description": "🕹️ Painel de Controle (Botoeiras/Autuadores)."},
            {"command": "scan",
             "description": "🔄 Scan de rede CLP."},
            {"command": "graph",
             "description": "📈 Gerar gráficos."},
            {"command": "logs",
             "description": "📄 Enviar logs por e-mail."},
            {"command": "stop_system",
             "description": "🛑 Encerrar monitoramento."},
        ]
    
    def _set_commands_api(self, commands: List[Dict[str, str]],
                          scope: Dict[str, Any]) -> bool:
        """
        Internal helper to call the setMyCommands Telegram API.

        Args:
            commands: List of command dictionaries.
            scope: Dictionary defining the scope (e.g., {'type': 'all_group_chats'}).

        Returns:
            True if API returned 200 OK.
        """
        url: str = f"{self._base_url}/setMyCommands"
        payload: Dict[str, Any] = {"commands": commands, "scope": scope}
        try:
            res: requests.Response = self._session.post(
                url, json=payload, timeout=5)
            return res.status_code == 200
        except Exception as e:
            self._logger.error(f"❌ API Error setMyCommands: {e}")
            return False

    def _post_commands_menu(self) -> bool:
        """
        Updates the bot's command menu for all relevant scopes.
        Includes Global, Group Admins, and Private Chat for specific Admins.

        Returns:
            True if all updates were triggered without exceptions.
        """
        try:
            # 1. Reset current admin commands in group
            self._session.post(
                f"{self._base_url}/deleteMyCommands",
                json={"scope": {"type": "all_chat_administrators"}},
                timeout=5)

            # 2. Set Admin Commands for Group Administrators
            self._set_commands_api(
                self._get_admin_commands(), 
                {"type": "all_chat_administrators"}
            )

            # 3. Set Public Commands for all group members
            self._set_commands_api(
                self._get_public_commands(), 
                {"type": "all_group_chats"}
            )

            # 4. Set Private Commands for specific Admin IDs
            for admin_id in self._admin_ids:
                self._set_commands_api(
                    self._get_admin_commands(), 
                    {"type": "chat", "chat_id": admin_id}
                )

            self._logger.info("✅ Telegram menus updated successfully!")
            return True
        except Exception as e:
            self._logger.error(f"❌ Error updating menus: {e}")
            return False

    def _send_bot_startup_message(self, plc_name: str, plc_ip: str) -> bool:
        """
        Sends a formatted startup notification to the main group.

        Args:
            plc_name: Name of the PLC being monitored.
            plc_ip: IP address of the PLC.

        Returns:
            True if sent successfully.
        """
        msg: str = (
            "🚀 *SISTEMA INICIALIZADO*\n"
            f"🔲 *CLP:* `{plc_name}`\n"
            f"🌐 *IP:* `{plc_ip}`\n"
            "✅ O bot está ativo e monitorando os sensores.")
        return self._send_notifications(msg)

    def _send_notifications(self, message: str,
                            chat_id: Optional[Union[int, str]] = None,
                            keyboard: Optional[Dict[str, Any]] = None) -> bool:
        """
        Sends a text message to the configured target chat.

        Args:
            message: Text to be sent (supports Markdown).
            chat_id: Optional target chat ID. If None, uses self._chat_id.
            keyboard: Optional dictionary representing an inline keyboard markup.

        Returns:
            True if delivered successfully.
        """
        target_chat = chat_id if chat_id is not None else self._chat_id

        if not target_chat:
            self._logger.error("❌ Error: chat_id is empty!")
            return False

        payload: Dict[str, Any] = {
            "chat_id": target_chat,
            "text": message,
            "parse_mode": "Markdown",
            "reply_markup": json.dumps(keyboard) if keyboard else None}

        try:
            response: requests.Response = self._session.post(
                f"{self._base_url}/sendMessage", data=payload, timeout=10)

            if response.status_code == 200:
                self._logger.info("✅ Telegram message delivered.")
                return True

            self._logger.error(
                "❌ Telegram API Error: "
                f"{response.status_code} - {response.text}")
            return False

        except Exception as e:
            self._logger.error(f"❌ Exception sending Telegram: {e}")
            return False

    def _execute_image_upload(
            self, image_buf: io.BytesIO, caption: str, chat_id: int) -> None:
        """
        Performs the actual image upload to Telegram API. Should be called within a thread.

        Args:
            image_buf: BytesIO buffer containing the image data.
            caption: Markdown text to accompany the image.
        """
        url: str = f"{self._base_url}/sendPhoto"
        image_buf.seek(0)

        files: Dict[str, Tuple[str, io.BytesIO, str]] = {
            'photo': ('chart.png', image_buf, 'image/png')}
        data: Dict[str, str] = {
            'chat_id': chat_id,
            'caption': caption,
            'parse_mode': 'Markdown'
        }

        try:
            response: requests.Response = self._session.post(
                url, data=data, files=files, timeout=20)
            if response.status_code == 200:
                self._logger.info("✅ Chart sent successfully.")
            else:
                self._logger.error(f"❌ Upload error: {response.status_code}")
        except Exception as e:
            self._logger.error(f"❌ Exception image upload: {e}")
        finally:
            image_buf.close()

    def _send_image(self, image_buf: io.BytesIO,
                    caption: str, chat_id: Optional[int] = None) -> None:
        """
        Dispatches an image upload to a background thread to prevent blocking.

        Args:
            image_buf: BytesIO buffer of the image.
            caption: Caption text.
        """
        target = chat_id if chat_id else self._chat_id

        thread: threading.Thread = threading.Thread(
            target=self._execute_image_upload,
            args=(image_buf, caption, target),
            daemon=True)
        thread.start()
        self._logger.info("📤 Image upload started in background.")

    def poll_commands(
            self,
            on_command_received: Callable[[str, Dict[str, Any]], None]) -> None:
        """
        Starts the infinite polling loop to receive commands.

        Args:
            on_command_received: A callback function that receives (command_text, user_info_dict).
        """
        if not self._is_ready: 
            self._logger.error("❌ Bot not initialized. Aborting polling.")
            return
        
        self._clear_pending_updates()
        self._pool_commands_running = True

        while self._pool_commands_running:
            try:
                updates: List[Dict[str, Any]] = self._fetch_updates()
                for item in updates:
                    threading.Thread(
                        target=on_command_received, 
                        args=(item["command"], item["user"]),
                        daemon=True).start()
                
                time.sleep(0.1)
            except Exception as e:
                self._logger.error(f"⚠️ Erro no loop de polling: {e}")
                time.sleep(2)

    def _process_chat_member_update(
            self, update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parses chat_member updates to detect admin promotions or demotions.

        Args:
            update: Raw update dictionary from Telegram.

        Returns:
            Dictionary with 'command' and 'user' info if relevant, else None.
        """
        member: Dict[str, Any] = update["chat_member"]

        target_data: Dict[str, Any] = member.get(
            "new_chat_member", {}).get("user", {})
        actor_data: Dict[str, Any] = member.get("from", {})

        new_status: Optional[str] = member.get(
            "new_chat_member", {}).get("status")
        old_status: Optional[str] = member.get(
            "old_chat_member", {}).get("status")

        is_becoming_admin: bool = new_status in ["administrator", "creator"]
        was_admin: bool = old_status in ["administrator", "creator"]

        user_info: Dict[str, Any] = {
            'id': target_data.get("id"),
            'target_id': target_data.get("id"),
            'target_name': target_data.get("first_name", "User"),
            'first_name': actor_data.get("first_name", "Admin"),
            'username': actor_data.get("username")}

        if is_becoming_admin and not was_admin:
            user_info['promote_event'] = True
            return {"command": "EVENT_PROMOTE", "user": user_info}
        
        if was_admin and not is_becoming_admin:
            user_info['demote_event'] = True
            user_info['target_id'] = target_data.get("id")
            user_info['executor_id'] = actor_data.get("id")
            return {"command": "EVENT_DEMOTE", "user": user_info}
        
        return None

    def _process_message_update(
            self, update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parses message and callback_query updates into a standardized format.

        Args:
            update: Raw update dictionary.

        Returns:
            Dictionary with 'command' and 'user' if successful, else None.
        """
        is_callback: bool = "callback_query" in update
        source: Optional[Dict[str, Any]] = update.get(
            "callback_query") if is_callback else update.get("message")

        if not source:
            return None

        chat_id: Any = source.get("chat", {}).get("id")
        # In callback_queries, the message object is nested
        m_id: Any = source.get("message", {}).get("message_id") if \
            is_callback else source.get("message_id")

        user_info: Dict[str, Any] = {
            'id': source["from"].get("id"),
            'chat_id': chat_id,
            'first_name': source["from"].get("first_name", "User"),
            'username': source["from"].get("username"),
            'message_id': m_id}

        cmd_text: Optional[str] = source.get("data") if is_callback \
            else source.get("text")
        
        if is_callback:
            self._answer_callback(update["callback_query"]["id"])

        if cmd_text:
            return {"command": cmd_text, "user": user_info}
        
        return None

    def _fetch_updates(self) -> List[Dict[str, Any]]:
        """
        Polls the Telegram API for new updates using long polling.

        Returns:
            List of parsed update dictionaries.
        """
        try:
            params: Dict[str, Any] = {
                "offset": self._last_update_id + 1,
                "timeout": 20,
                "allowed_updates": json.dumps(
                    ["message", "callback_query", "chat_member"])}

            response: requests.Response = self._session.get(
                f"{self._base_url}/getUpdates", params=params, timeout=25)
            if response.status_code != 200:
                return []

            result: List[Dict[str, Any]] = response.json().get("result", [])
            updates_data: List[Dict[str, Any]] = []

            for update in result:
                self._last_update_id = update["update_id"]
                
                parsed_item: Optional[Dict[str, Any]] = (
                    self._process_chat_member_update(update) if "chat_member" \
                        in update else self._process_message_update(update))
                if parsed_item:
                    updates_data.append(parsed_item)

            return updates_data
        except (requests.exceptions.ReadTimeout, Exception):
            return []
    
    def _clear_pending_updates(self) -> bool:
        """
        Flushes the Telegram update queue by setting offset to -1.

        Returns:
            True if queue was cleared.
        """
        try:
            self._logger.info(
                "🧹 Cleaning pending Telegram updates (Flush)...")
            params: Dict[str, Any] = {
                "offset": -1,
                "timeout": 1,
                "allowed_updates": json.dumps(
                    ["message", "callback_query", "chat_member"])}
            
            response: requests.Response = self._session.get(
                f"{self._base_url}/getUpdates", params=params, timeout=5)
            if response.status_code == 200:
                self._logger.info("✅ Telegram queue cleared successfully.")
                return True
            return False
        except Exception as e:
            self._logger.error(f"❌ Exception during queue clear: {e}")
            return False

    def _answer_callback(self, callback_id: str) -> None:
        """
        Acknowledges a callback query to stop the 'loading' state on the user's UI.

        Args:
            callback_id: The unique ID of the callback query.
        """
        try:
            self._session.post(
                f"{self._base_url}/answerCallbackQuery",
                json={"callback_query_id": callback_id}, timeout=5)
        except Exception:
            pass
    
    def _send_private_message(
            self, user_id: int, message: str,
            keyboard: Optional[Dict[str, Any]] = None) -> bool:
        """
        Sends a private message to a specific user ID.

        Args:
            user_id: Telegram ID of the recipient.
            message: Text to send.
            keyboard: Optional inline keyboard.

        Returns:
            True if sent successfully.
        """
        payload: Dict[str, Any] = {
            "chat_id": user_id,
            "text": message,
            "parse_mode": "Markdown",}
        
        if keyboard:
            payload["reply_markup"] = json.dumps(keyboard)
        
        try:
            response: requests.Response = self._session.post(
                f"{self._base_url}/sendMessage", json=payload, timeout=10)
            
            if response.status_code == 200:
                self._logger.info(f"✅ Private message sent to ID: {user_id}")
                return True
            
            if response.status_code == 403:
                self._logger.warning(
                    f"🚫 Admin user id ({user_id}) not initiated bot.")
            
            self._logger.error(
                f"❌ Failed to send private message to {user_id}: {response.text}")
            return False
        except Exception as e:
            self._logger.error(f"❌ Exception in _send_private_message: {e}")
            return False
    
    def _send_shutdown_confirmation(self, admin_id: int) -> bool:
        """
        Sends a 2-step confirmation message for system shutdown.

        Args:
            admin_id: The admin ID who requested the shutdown.

        Returns:
            True if confirmation message was sent.
        """
        keyboard: Dict[str, Any] = {
            "inline_keyboard": [[
                {"text": "✅ Sim, desligar", "callback_data": "CONFIRM_HALT"},
                {"text": "❌ Cancelar", "callback_data": "CANCEL_HALT"}
            ]]
        }
        msg: str = (
            "⚠️ *ATENÇÃO:* Você solicitou o encerramento do sistema. "
            "Com isso todos os registros dos logs serão apagados. "
            "Para salvá-los, antes de desligar, escolha o comando "
            "*'/logs'*\nConfirmar desligamento?")
        return self._send_private_message(admin_id, msg, keyboard)

    def _delete_message(
            self, chat_id: Union[int, str], message_id: int) -> None:
        """
        Deletes a specific message from a chat. Silent on failure.

        Args:
            chat_id: ID of the chat.
            message_id: ID of the message to delete.
        """
        try:
            self._session.post(
                f"{self._base_url}/deleteMessage", 
                json={"chat_id": chat_id, "message_id": message_id}, timeout=5)
        except:
            pass
    
    def _send_promotion_confirmation(self, admin_who_promoted_id: int,
                                     new_admin_info: Dict[str, Any]) -> bool:
        """
        Sends a notification to an existing admin to confirm dynamic admin addition.

        Args:
            admin_who_promoted_id: ID of the admin who will confirm.
            new_admin_info: Dictionary containing 'id' and 'name' of the new user.

        Returns:
            True if sent.
        """
        keyboard: Dict[str, Any] = {
            "inline_keyboard": [[
                {"text": "✅ Sim, dar acesso",
                 "callback_data": f"CONFIRM_ADD_{new_admin_info['id']}"},
                {"text": "❌ Não",
                 "callback_data": f"CANCEL_ADD_{new_admin_info['id']}"}
            ]]
        }
        
        msg: str = (f"🔔 *NOVO ADMIN DETECTADO*\n\nO usuário `{new_admin_info['first_name']}`"
            f" ({new_admin_info['id']}) foi promovido no grupo.\n"
            "Deseja liberar o acesso aos comandos restritos do bot para ele?")
        
        return self._send_private_message(admin_who_promoted_id, msg, keyboard)

    def _add_admin_id(self, new_id: int) -> None:
        """
        Dynamically adds a new ID to the administrative tuple and updates menus.

        Args:
            new_id: The new admin user ID.
        """
        if new_id not in self._admin_ids:
            current_list: List[int] = list(self._admin_ids)
            current_list.append(new_id)
            self._admin_ids = tuple(current_list)
            self._logger.info(f"➕ ID {new_id} added in admins list dynamic.")
            self._post_commands_menu()
    
    def _remove_admin_id(self, user_id: int) -> bool:
        """
        Removes an ID from the administrative list and deletes their private commands.

        Args:
            user_id: The ID to remove.

        Returns:
            True if removed, False if not found.
        """
        if user_id in self._admin_ids:
            current_list: List[int] = list(self._admin_ids)
            current_list.remove(user_id)
            self._admin_ids = tuple(current_list)
            
            url: str = f"{self._base_url}/deleteMyCommands"
            payload: Dict[str, Any] = {
                "scope": {"type": "chat", "chat_id": user_id}}
            self._session.post(url, json=payload, timeout=5)
            
            self._logger.info(
                f"➖ ID {user_id} removed from admin list in memory.")
            return True
        return False