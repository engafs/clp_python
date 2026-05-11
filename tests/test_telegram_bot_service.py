import unittest
from unittest.mock import patch, MagicMock, io
from src.services.telegram_bot_service import TelegramBotService
import os
import json
import logging
logging.disable(logging.CRITICAL)


class TestTelegramBotService(unittest.TestCase):
    
    def setUp(self):
        self.mock_env = {
            "TELEGRAM_TOKEN": "12345",
            "TELEGRAM_CHAT_ID": "67890",
            "TELEGRAM_ADMIN_IDS": "111,222"}

        with patch.dict(os.environ, self.mock_env), \
             patch.object(TelegramBotService, '_check_bot_connection',
                          return_value=True), \
             patch.object(TelegramBotService, '_post_commands_menu',
                          return_value=True):
            
            self.telegram_bot = TelegramBotService(
                token=self.mock_env["TELEGRAM_TOKEN"], 
                chat_id=self.mock_env["TELEGRAM_CHAT_ID"]
            )
            
            self.token = self.mock_env["TELEGRAM_TOKEN"]
            self.chat_id = self.mock_env["TELEGRAM_CHAT_ID"]
            
            self.telegram_bot._is_ready = True
    
    @patch('requests.Session.get')
    def test_is_active_admin_in_group_logic(self, mock_get):
        """Test the logic of _is_active_admin_in_group with various admin 
            statuses."""
        self.telegram_bot._admin_ids = (111,)
        
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "result": {"status": "administrator"}}
        self.assertTrue(self.telegram_bot._is_active_admin_in_group(111))

        mock_get.return_value.json.return_value = {
            "result": {"status": "member"}}
        self.assertFalse(self.telegram_bot._is_active_admin_in_group(111))

        mock_get.return_value.json.return_value = {
            "result": {"status": "left"}}
        self.assertFalse(self.telegram_bot._is_active_admin_in_group(111))

        self.assertFalse(self.telegram_bot._is_active_admin_in_group(999))

    @patch('requests.get')
    def test_check_bot_connection_success(self, mock_get):
        """Test that _check_bot_connection returns True on a successful API 
            call."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        
        self.telegram_bot._session.get = mock_get
        
        result = self.telegram_bot._check_bot_connection()
        
        self.assertTrue(result)
        mock_get.assert_called_once()
        
        args, _ = mock_get.call_args
        self.assertIn("/getMe", args[0])
    
    @patch('requests.Session.post')
    def test_set_commands_api_success(self, mock_post):
        """Test that _set_commands_api returns True when the API call is 
            successful."""
        mock_post.return_value.status_code = 200
        commands = [{"command": "test", "description": "desc"}]
        scope = {"type": "default"}
        
        result = self.telegram_bot._set_commands_api(commands, scope)
        
        self.assertTrue(result)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs['json']['commands'], commands)
        self.assertEqual(kwargs['json']['scope'], scope)
    
    @patch.object(TelegramBotService, '_set_commands_api', return_value=True)
    @patch('requests.Session.post')
    def test_post_commands_menu_orchestration(self, mock_post, mock_set_api):
        """Test that _post_commands_menu orchestrates calls to _set_commands_api
            and makes the correct number of API calls based on admin IDs."""
        self.telegram_bot._admin_ids = (111, 222)
        
        result = self.telegram_bot._post_commands_menu()
        
        self.assertTrue(result) 
        self.assertEqual(mock_set_api.call_count, 4)
        self.assertEqual(mock_post.call_count, 1)
    
    @patch('requests.Session.post')
    def test_post_commands_menu_success(self, mock_post):
        """Test that _post_commands_menu returns True when all API calls 
            succeed."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        
        self.telegram_bot._session.post = mock_post
        
        result = self.telegram_bot._post_commands_menu()
        
        self.assertTrue(result)
        self.assertGreaterEqual(mock_post.call_count, 3)

    @patch('requests.Session.post')
    def test_post_commands_menu_failure(self, mock_post):
        """Test that _post_commands_menu returns False when an API call 
            fails."""
        mock_post.side_effect = Exception("Conexão falhou")
    
        result = self.telegram_bot._post_commands_menu()
        self.assertFalse(result)
    
    @patch.object(TelegramBotService, '_set_commands_api')
    @patch('requests.Session.post')
    def test_post_commands_menu_returns_false_on_api_error_granular(
        self, mock_post, mock_set_api):
        """Test that _post_commands_menu returns False if any call to 
            _set_commands_api fails, even if others succeed."""
        mock_post.return_value.status_code = 200
        
        mock_set_api.side_effect = [True, False]

        self.telegram_bot._admin_ids = (111,)
        result = self.telegram_bot._post_commands_menu()

        self.assertFalse(result)
    
    @patch('requests.Session.post')
    def test_post_commands_menu_exception_handling(self, mock_post):
        """Test that _post_commands_menu handles exceptions gracefully and 
            returns False."""
        mock_post.side_effect = Exception("Timeout")
        
        result = self.telegram_bot._post_commands_menu()
        self.assertFalse(result)
    
    @patch('requests.Session.post')
    def test_post_commands_menu_per_admin_id(self, mock_post):
        """Test that _post_commands_menu makes API calls for each admin ID and
            that the payloads are correct."""
        self.telegram_bot._admin_ids = (111, 222)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        self.telegram_bot._session.post = mock_post
        
        result = self.telegram_bot._post_commands_menu()
        
        self.assertTrue(result)
        
        self.assertEqual(mock_post.call_count, 5)
        
        last_call_kwargs = mock_post.call_args[1]
        self.assertEqual(last_call_kwargs['json']['scope']['chat_id'], 222)
        self.assertEqual(last_call_kwargs['json']['scope']['type'], 'chat')
    
    @patch.object(TelegramBotService, '_send_notifications')
    def test_send_bot_startup_message_formatting(self, mock_send):
        """Test that _send_bot_startup_message formats the message correctly 
        and calls _send_notifications with the expected content."""
        plc_name = "Siemens S7-1200"
        plc_ip = "192.168.0.10"
        mock_send.return_value = True

        result = self.telegram_bot._send_bot_startup_message(plc_name, plc_ip)

        self.assertTrue(result)
        mock_send.assert_called_once()
        
        args, _ = mock_send.call_args
        sent_msg = args[0]

        self.assertIn("🚀 *SISTEMA INICIALIZADO*", sent_msg)
        self.assertIn(f"`{plc_name}`", sent_msg)
        self.assertIn(f"`{plc_ip}`", sent_msg)
        self.assertIn("✅ O bot está ativo", sent_msg)

    @patch('requests.Session.post')
    def test_send_notifications_success(self, mock_post):
        """Test that _send_notifications returns True when the API call is 
            successful."""
        mock_post.return_value = MagicMock(status_code=200)
        self.telegram_bot._session.post = mock_post
        
        result = self.telegram_bot._send_notifications("Teste")
        self.assertTrue(result)
        mock_post.assert_called()
    
    def test_process_chat_member_update_promotion(self):
        """Test that _process_chat_member_update correctly identifies a 
            promotion"""
        update = {
            "chat_member": {
                "old_chat_member": {"status": "member"},
                "new_chat_member": {
                    "status": "administrator",
                    "user": {"id": 888, "first_name": "Leo"}
                }
            }
        }
        result = self.telegram_bot._process_chat_member_update(update)
        
        self.assertIsNotNone(result)
        self.assertEqual(result["command"], "EVENT_PROMOTE")
        self.assertEqual(result["user"]["id"], 888)
        self.assertTrue(result["user"]["promote_event"])

    def test_process_message_update_text(self):
        """Test that _process_message_update correctly processes a text 
            message update."""
        update = {
            "message": {
                "message_id": 123,
                "chat": {"id": 67890},
                "from": {"id": 1, "first_name": "Leo"},
                "text": "/status"
            }
        }
        result = self.telegram_bot._process_message_update(update)
        
        self.assertIsNotNone(result)
        self.assertEqual(result["command"], "/status")
        self.assertEqual(result["user"]["message_id"], 123)

    @patch.object(TelegramBotService, '_answer_callback')
    def test_process_message_update_callback(self, mock_answer):
        """Test that _process_message_update correctly processes a callback 
            query update and calls _answer_callback."""
        update = {
            "callback_query": {
                "id": "query_99",
                "from": {"id": 1, "first_name": "Leo"},
                "message": {"message_id": 456, "chat": {"id": 67890}},
                "data": "CALLBACK_DATA_TEST"
            }
        }
        result = self.telegram_bot._process_message_update(update)
        
        self.assertIsNotNone(result)
        self.assertEqual(result["command"], "CALLBACK_DATA_TEST")
        mock_answer.assert_called_once_with("query_99")

    @patch('requests.Session.get')
    def test_fetch_updates_success(self, mock_get):
        """Test that _fetch_updates returns a list of processed updates when 
            the API call is successful."""
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "ok": True, 
            "result": [{
                "update_id": 100, 
                "message": {"text": "/status",
                            "from": {"id": 1, "first_name": "Leo"}}
            }]
        }
        mock_get.return_value = mock_response
        self.telegram_bot._session.get = mock_get

        updates = self.telegram_bot._fetch_updates()
        
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]['command'], "/status")
    
    @patch('requests.Session.get')
    def test_fetch_updates_integration(self, mock_get):
        """Test that _fetch_updates correctly processes a mix of message and
            chat member updates in a single API response."""
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "result": [
                {"update_id": 1,
                 "message": {"text": "cmd1", "from": {"id": 1}, "chat": {"id": 1}, "message_id": 1}},
                {"update_id": 2,
                 "chat_member": {
                    "old_chat_member": {"status": "member"},
                    "new_chat_member": {"status": "administrator", "user": {"id": 2}}
                }}
            ]
        }
        mock_get.return_value = mock_response
        
        updates = self.telegram_bot._fetch_updates()
        
        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[0]["command"], "cmd1")
        self.assertEqual(updates[1]["command"], "EVENT_PROMOTE")
        self.assertEqual(self.telegram_bot._last_update_id, 2)
    
    @patch('requests.Session.get')
    def test_fetch_updates_timeout_handling(self, mock_get):
        """Test that _fetch_updates handles a timeout exception gracefully and 
            returns an empty list."""
        import requests
        mock_get.side_effect = requests.exceptions.ReadTimeout()
        
        updates = self.telegram_bot._fetch_updates()
        self.assertEqual(updates, [])

    @patch('threading.Thread')
    def test_send_image_dispatches_thread(self, mock_thread):
        """Test that _send_image dispatches a new thread to handle the image 
            upload."""
        test_buf = io.BytesIO(b"fake_image_data")
        
        self.telegram_bot._send_image(test_buf, "Legenda")
        
        mock_thread.assert_called_once()
        args, kwargs = mock_thread.call_args
        self.assertEqual(
            kwargs['target'], self.telegram_bot._execute_image_upload)

    @patch('requests.Session.post')
    def test_execute_image_upload_success(self, mock_post):
        """Test that _execute_image_upload makes the correct API call and 
            handles the file buffer correctly."""
        mock_post.return_value = MagicMock(status_code=200)
        self.telegram_bot._session.post = mock_post
        test_buf = io.BytesIO(b"fake_image_data")
        
        self.telegram_bot._execute_image_upload(test_buf, "Legenda", None)
        
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertIn('photo', kwargs['files'])
        self.assertEqual(kwargs['data']['caption'], "Legenda")
        self.assertTrue(test_buf.closed)

    @patch.object(TelegramBotService, '_answer_callback')
    @patch('requests.Session.get')
    def test_fetch_updates_with_callback(self, mock_get, mock_answer):
        """Test that _fetch_updates correctly processes a callback query 
            update and calls _answer_callback."""
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "result": [{
                "update_id": 101,
                "callback_query": {
                    "id": "cb_1",
                    "from": {"id": 1, "first_name": "Leo"},
                    "data": "view_graph_test"
                }
            }]
        }

        mock_get.return_value = mock_response
        self.telegram_bot._session.get = mock_get

        updates = self.telegram_bot._fetch_updates()
        
        self.assertEqual(updates[0]['command'], "view_graph_test")
        mock_answer.assert_called_once_with("cb_1")
    
    @patch('requests.Session.get')
    def test_clear_pending_updates_success(self, mock_get):
        """Test that _clear_pending_updates makes the correct API call and 
            returns True on success."""
        mock_response = MagicMock(status_code=200)
        mock_get.return_value = mock_response
        self.telegram_bot._session.get = mock_get

        result = self.telegram_bot._clear_pending_updates()

        self.assertTrue(result)
        mock_get.assert_called_once()
        
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs['params']['offset'], -1)
        self.assertEqual(kwargs['params']['timeout'], 1)
        self.assertIn("/getUpdates", mock_get.call_args[0][0])

    @patch('requests.Session.get')
    def test_clear_pending_updates_network_failure(self, mock_get):
        """Test that _clear_pending_updates handles a network failure 
            gracefully and returns False."""
        mock_get.side_effect = Exception("Network Down")
        self.telegram_bot._session.get = mock_get

        result = self.telegram_bot._clear_pending_updates()

        self.assertFalse(result)
    
    @patch('requests.Session.post')
    def test_send_private_message_forbidden(self, mock_post):
        """Test that _send_private_message returns False and logs a warning when
            the bot is forbidden from sending a message."""
        mock_post.return_value.status_code = 403
        self.telegram_bot._logger = MagicMock()
        
        result = self.telegram_bot._send_private_message(111, "Oi")
        
        self.assertFalse(result)
        self.telegram_bot._logger.warning.assert_called()
    
    @patch('requests.Session.post')
    def test_send_private_message_success(self, mock_post):
        """Test that _send_private_message returns True and makes the correct 
            API call when the message is sent successfully."""
        mock_post.return_value = MagicMock(status_code=200)
        user_id = 998877
        message = "Olá, Admin"
        
        result = self.telegram_bot._send_private_message(user_id, message)
        
        self.assertTrue(result)
        mock_post.assert_called_once()

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs['json']['chat_id'], user_id)
        self.assertEqual(kwargs['json']['text'], message)

    @patch('requests.Session.post')
    def test_send_private_message_failure(self, mock_post):
        """Test that _send_private_message returns False when the API call fails."""
        mock_post.return_value = MagicMock(
            status_code=403, text="Forbidden: bot was blocked")
        
        result = self.telegram_bot._send_private_message(123, "Teste")
        
        self.assertFalse(result)
    
    @patch('requests.Session.post')
    def test_send_shutdown_confirmation_payload(self, mock_post):
        """Test that _send_shutdown_confirmation makes the correct API call 
            and formats the payload correctly."""
        mock_post.return_value = MagicMock(status_code=200)
        admin_id = 111
        
        self.telegram_bot._send_shutdown_confirmation(admin_id)

        args, kwargs = mock_post.call_args
        
        url_chamada = args[0]
        payload = kwargs['json']

        self.assertIn("/sendMessage", url_chamada)
        self.assertEqual(payload['chat_id'], admin_id)
        
        reply_markup = json.loads(payload['reply_markup'])
        buttons = reply_markup['inline_keyboard'][0]
        
        self.assertEqual(buttons[0]['callback_data'], "CONFIRM_HALT")
        self.assertEqual(buttons[0]['text'], "✅ Sim, desligar")
        self.assertEqual(buttons[1]['callback_data'], "CANCEL_HALT")
        self.assertIn("ATENÇÃO", payload['text'])

    @patch('requests.Session.post')
    def test_send_shutdown_confirmation_exception_logging(self, mock_post):
        """Test that _send_shutdown_confirmation logs an error when an exception
            occurs during the API call."""
        self.telegram_bot._logger = MagicMock()
        
        mock_post.side_effect = Exception("Conexão perdida")
        
        self.telegram_bot._send_shutdown_confirmation(111)

        self.telegram_bot._logger.error.assert_called()

        args_log = self.telegram_bot._logger.error.call_args[0][0]
        self.assertIn("Conexão perdida", args_log)

    @patch('requests.Session.post')
    def test_delete_message_call(self, mock_post):
        """Test that _delete_message makes the correct API call with the 
            expected payload."""
        mock_post.return_value = MagicMock(status_code=200)
        chat_id = 67890
        message_id = 12345
        
        self.telegram_bot._delete_message(chat_id, message_id)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        
        self.assertIn("/deleteMessage", args[0])
        self.assertEqual(kwargs['json']['chat_id'], chat_id)
        self.assertEqual(kwargs['json']['message_id'], message_id)

    @patch('requests.Session.post')
    def test_delete_message_silent_failure(self, mock_post):
        """Test that _delete_message does not log an error when an exception 
            occurs, since deletion failures should be silent."""
        self.telegram_bot._logger = MagicMock() 
        mock_post.side_effect = Exception("Telegram Error")
        
        self.telegram_bot._delete_message(1, 1)

        self.telegram_bot._logger.error.assert_not_called()
    
    @patch('requests.Session.post')
    def test_delete_message_already_deleted(self, mock_post):
        """Test that _delete_message does not raise an exception when the message
            is already deleted or cannot be deleted."""
        mock_post.return_value = MagicMock(status_code=400)
        
        try:
            self.telegram_bot._delete_message(123, 456)
            success = True
        except:
            success = False
        
        self.assertTrue(success, "O método deveria ignorar erros de deleção.")
    
    @patch('requests.Session.post')
    def test_send_promotion_confirmation_payload(self, mock_post):
        """Test that _send_promotion_confirmation makes the correct API call and
            formats the payload correctly when confirming a new admin promotion."""
        mock_post.return_value = MagicMock(status_code=200)
        
        admin_atual_id = 111
        novo_admin_info = {'id': 999, 'first_name': 'Engenheiro Junior'}
        
        self.telegram_bot._send_promotion_confirmation(admin_atual_id, novo_admin_info)

        _, kwargs = mock_post.call_args
        payload = kwargs['json']
        reply_markup = json.loads(payload['reply_markup'])
        
        confirm_btn = reply_markup['inline_keyboard'][0][0]
        self.assertEqual(confirm_btn['callback_data'], "CONFIRM_ADD_999")
        self.assertEqual(payload['chat_id'], admin_atual_id)
        self.assertIn("Engenheiro Junior", payload['text'])

    def test_add_admin_id_dynamic_update(self):
        """Test that _add_admin_id correctly updates the _admin_ids tuple and 
            calls _post_commands_menu to refresh the commands menu for the new 
            admin."""
        self.telegram_bot._admin_ids = (111,)
        novo_id = 222
        
        with patch.object(TelegramBotService, '_post_commands_menu') as mock_menu:
            self.telegram_bot._add_admin_id(novo_id)

            self.assertIn(novo_id, self.telegram_bot._admin_ids)
            self.assertIsInstance(self.telegram_bot._admin_ids, tuple)
            self.assertEqual(len(self.telegram_bot._admin_ids), 2)
            
            mock_menu.assert_called_once()

    def test_add_admin_id_duplicate_prevention(self):
        """Test that _add_admin_id does not add a duplicate ID to the 
            _admin_ids tuple and still calls _post_commands_menu to ensure the 
            menu is updated."""
        self.telegram_bot._admin_ids = (111, 222)
        
        with patch.object(TelegramBotService, '_post_commands_menu'):
            self.telegram_bot._add_admin_id(222)
            
            self.assertEqual(len(self.telegram_bot._admin_ids), 2)
            self.assertEqual(self.telegram_bot._admin_ids.count(222), 1)

    @patch('requests.Session.get')
    def test_fetch_updates_detects_chat_member_promotion(self, mock_get):
        """Test that _fetch_updates correctly identifies a chat member 
            promotion and processes it into an update with the expected 
            structure."""
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "result": [{
                "update_id": 500,
                "chat_member": {
                    "old_chat_member": {"status": "member"},
                    "new_chat_member": {
                        "status": "administrator",
                        "user": {"id": 888, "first_name": "NovoAdmin"}
                    }
                }
            }]
        }
        mock_get.return_value = mock_response
        
        updates = self.telegram_bot._fetch_updates()
        
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]['command'], "EVENT_PROMOTE")
        self.assertEqual(updates[0]['user']['id'], 888)
        self.assertTrue(updates[0]['user']['promote_event'])
    
    @patch('requests.Session.post')
    def test_remove_admin_id_logic_and_api_call(self, mock_post):
        """Test that _remove_admin_id correctly updates the _admin_ids tuple by
            removing the specified ID, and that it makes the correct API call 
            to delete the commands menu for that admin."""
        self.telegram_bot._admin_ids = (111, 222)
        mock_post.return_value = MagicMock(status_code=200)
        id_para_remover = 111

        result = self.telegram_bot._remove_admin_id(id_para_remover)

        self.assertTrue(result)
        self.assertNotIn(id_para_remover, self.telegram_bot._admin_ids)
        self.assertEqual(len(self.telegram_bot._admin_ids), 1)
        self.assertIsInstance(self.telegram_bot._admin_ids, tuple)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        
        self.assertIn("/deleteMyCommands", args[0])
        self.assertEqual(kwargs['json']['scope']['chat_id'], id_para_remover)
        self.assertEqual(kwargs['json']['scope']['type'], 'chat')

    def test_remove_admin_id_non_existent(self):
        """Test that _remove_admin_id returns False and does not modify the 
            _admin_ids tuple when trying to remove an ID that is not in the 
            tuple, and that it does not make an API call."""
        self.telegram_bot._admin_ids = (111, 222)
        id_falso = 999

        with patch('requests.Session.post') as mock_post:
            result = self.telegram_bot._remove_admin_id(id_falso)
            
            self.assertFalse(result)
            self.assertEqual(len(self.telegram_bot._admin_ids), 2)
            mock_post.assert_not_called()

    @patch('requests.Session.get')
    def test_fetch_updates_detects_chat_member_demotion(self, mock_get):
        """Test that _fetch_updates correctly identifies a chat member 
            demotion and processes it into an update with the expected 
            structure."""
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "result": [{
                "update_id": 600,
                "chat_member": {
                    "old_chat_member": {"status": "administrator"},
                    "new_chat_member": {
                        "status": "member",
                        "user": {"id": 111, "first_name": "ExAdmin"}
                    }
                }
            }]
        }
        mock_get.return_value = mock_response
        
        updates = self.telegram_bot._fetch_updates()
        
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]['command'], "EVENT_DEMOTE")
        self.assertEqual(updates[0]['user']['id'], 111)
        self.assertTrue(updates[0]['user']['demote_event'])
    