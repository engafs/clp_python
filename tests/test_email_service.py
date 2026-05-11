import unittest
from unittest.mock import patch, MagicMock
import socket
import smtplib
from src.services.email_service import EmailService
from email.message import EmailMessage
import logging
logging.disable(logging.CRITICAL)


class TestEmailService(unittest.TestCase):
    
    def setUp(self):
        self.patcher = patch('smtplib.SMTP_SSL')
        self.mock_smtp_ssl = self.patcher.start()
        
        self.valid_email = "teste@hotmail.com"
        self.email_service = EmailService(sender_email=self.valid_email)

    def tearDown(self):
        self.patcher.stop()

    def test_initialization_with_valid_email(self):
        """Test that the EmailService initializes correctly with a valid email."""
        self.assertEqual(self.email_service._sender_email, self.valid_email)
        self.assertEqual(self.email_service._domain, "hotmail.com")
        self.assertEqual(self.email_service._smtp_server, "smtp.office365.com")

    def test_raise_error_on_invalid_format(self):
        """Test that the EmailService raises an error for an invalid email 
            format."""
        with self.assertRaises(ValueError) as cm:
            EmailService(sender_email="invalid_format@dominio")
        self.assertIn("Formato de email inválido", str(cm.exception))

    def test_raise_error_on_unsupported_domain(self):
        """Test that the EmailService raises an error for an unsupported email domain."""
        with self.assertRaises(ValueError) as cm:
            EmailService(sender_email="user@unknown_provider.com")
        self.assertIn("is not supported", str(cm.exception))

    def test_is_valid_email_regex(self):
        """Test that the email validation regex works correctly."""
        self.assertTrue(self.email_service._is_valid_email("valido@yahoo.com"))
        self.assertFalse(
            self.email_service._is_valid_email("invalido@@ponto.com"))

    @patch('smtplib.SMTP_SSL')
    def test_probe_connection_returns_ssl_on_success(self, mock_smtp_ssl):
        """Test that the probe_connection method returns SSL settings when the 
            connection is successful."""
        mock_smtp_ssl.return_value.__enter__.return_value = MagicMock()
        
        port, is_ssl = self.email_service._probe_connection()
        
        self.assertEqual(port, 587)
        self.assertTrue(is_ssl)

    @patch('smtplib.SMTP_SSL')
    def test_probe_connection_returns_tls_on_failure(self, mock_smtp_ssl):
        """Test that the probe_connection method returns TLS settings when the 
            connection fails."""
        mock_smtp_ssl.side_effect = socket.timeout("Timeout!")
        
        port, is_ssl = self.email_service._probe_connection()
        
        self.assertEqual(port, 587)
        self.assertFalse(
            is_ssl, "The probe should return False on connection failure.")

    def test_set_credentials_updates_private_attribute(self):
        """Test that the set_credentials method correctly updates the private 
            _password attribute."""
        self.email_service.set_credentials("minha_senha_123")
        self.assertEqual(self.email_service._password, "minha_senha_123")

    def test_set_credentials_raises_error_if_empty(self):
        """Test that the set_credentials method raises an error if the 
            password is empty."""
        with self.assertRaises(ValueError):
            self.email_service.set_credentials("")
    
    def test_authenticate_success_ssl(self):
        """Test that the _authenticate method works correctly with SSL."""
        self.email_service._use_ssl = True
        self.email_service.set_credentials("correct_password")
        
        mock_server = MagicMock()
        
        self.email_service._authenticate(mock_server)
        
        mock_server.starttls.assert_not_called()
        mock_server.login.assert_called_once_with(
            self.email_service._sender_email, "correct_password")

    def test_authenticate_success_tls(self):
        """Test that the _authenticate method works correctly with TLS."""
        self.email_service._use_ssl = False
        self.email_service.set_credentials("password_123")
        
        self.email_service._ssl_port = 0
        mock_server = MagicMock()
        
        self.email_service._authenticate(mock_server)
        
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with(
            self.email_service._sender_email, "password_123")

    def test_authenticate_failed_password(self):
        """Test that the _authenticate method raises an error when 
            authentication fails."""
        self.email_service.set_credentials("wrong_password")
        
        mock_server = MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(
            535, "Authentication failed")
        
        with self.assertRaises(ValueError) as cm:
            self.email_service._authenticate(mock_server)
        
        self.assertIn("Autenticação falhou", str(cm.exception))
    
    def test_format_message_structure(self):
        """Test that the _format_message method creates an EmailMessage with 
            the correct structure."""
        receiver: str = "client@provider.com"
        subject: str = "Sensor Warning"
        body: str = "The counter marks 100 pieces."

        msg = self.email_service._format_message(receiver, subject, body)

        self.assertIsInstance(msg, EmailMessage)
        self.assertEqual(msg['To'], receiver)
        self.assertEqual(msg['Subject'], subject)
        self.assertEqual(msg['From'], self.email_service._sender_email)
        
        self.assertEqual(msg.get_content().strip(), body)
    
    @patch('smtplib.SMTP_SSL')
    @patch('smtplib.SMTP')
    def test_send_email_success_flow(self, mock_smtp, mock_smtp_ssl):
        """Test that the send_email method successfully sends an email using 
            SSL."""
        self.email_service.set_credentials("test_password")
        self.email_service._use_ssl = True 
        
        mock_server = MagicMock()
        mock_smtp_ssl.return_value.__enter__.return_value = mock_server
        
        result = self.email_service.send_email(
            recipient="destino@teste.com",
            subject="Subject",
            body="Email content")
        
        self.assertTrue(result)

        mock_smtp_ssl.assert_called_with(
            self.email_service._smtp_server,
            self.email_service._ssl_port,
            timeout=30)

        mock_server.login.assert_called()
        mock_server.send_message.assert_called_once()

    def test_send_email_raises_runtime_error_without_password(self):
        """Test that the send_email method raises a RuntimeError if credentials
            are not set."""
        self.email_service._password = None 
        
        with self.assertRaises(RuntimeError) as cm:
            self.email_service.send_email("a@a.com", "S", "B")
        
        self.assertIn(
            "As credenciais do email não foram configuradas.", str(cm.exception))

    @patch('smtplib.SMTP_SSL')
    def test_send_email_failure_returns_false(self, mock_smtp_ssl):
        """Test that the send_email method returns False when an exception occurs
            during the email sending process."""
        self.email_service.set_credentials("password")
        self.email_service._use_ssl = True
        
        mock_smtp_ssl.side_effect = Exception("Refused connection")
        
        result = self.email_service.send_email("a@a.com", "S", "B")        
        self.assertFalse(result)
    
    @patch('os.path.isfile')
    @patch('builtins.open', new_callable=unittest.mock.mock_open, read_data=b"fake_zip_content")
    @patch('mimetypes.guess_type')
    def test_add_attachment_success(self, mock_guess, mock_file, mock_isfile):
        """Test that the _add_attachment method correctly adds an attachment 
            to the email message."""
        mock_isfile.return_value = True
        mock_guess.return_value = ('application/zip', None)
        msg = EmailMessage()
        file_path = "logs.zip"

        self.email_service._add_attachment(msg, file_path)

        mock_isfile.assert_called_once_with(file_path)
        mock_file.assert_called_once_with(file_path, 'rb')
        
        attachments = list(msg.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "logs.zip")
        self.assertEqual(attachments[0].get_content_type(), "application/zip")

    @patch('os.path.isfile')
    def test_add_attachment_raises_filenotfound(self, mock_isfile):
        """Test that the _add_attachment method raises a FileNotFoundError 
            when the file does not exist."""
        mock_isfile.return_value = False
        msg = EmailMessage()

        with self.assertRaises(FileNotFoundError):
            self.email_service._add_attachment(msg, "arquivo_fantasma.zip")

    @patch('os.path.isfile')
    @patch('builtins.open', new_callable=unittest.mock.mock_open, read_data=b"data")
    @patch('mimetypes.guess_type')
    def test_add_attachment_fallback_mimetype(self, mock_guess, mock_file, mock_isfile):
        """Test that the _add_attachment method falls back to 
            application/octet-stream when the MIME type cannot be guessed."""
        mock_isfile.return_value = True
        mock_guess.return_value = (None, None)
        msg = EmailMessage()

        self.email_service._add_attachment(msg, "log.unknown")

        attachments = list(msg.iter_attachments())
        self.assertEqual(attachments[0].get_content_type(), "application/octet-stream")

if __name__ == '__main__':
    unittest.main(verbosity=2)