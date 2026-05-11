import re
import smtplib
import socket
import os
import mimetypes
from typing import Tuple, Dict, Optional, Union
from email.message import EmailMessage
from src.utils.logging_config import setup_logging
from logging import Logger

class EmailService:
    """
    Service for sending emails via SMTP with support for multiple providers.
    
    This class handles connection probing (SSL/TLS), authentication, 
    message formatting, and file attachments.
    """

    # Provider mapping: domain -> (smtp_server, ssl_port, tls_port)
    PROVIDERS: Dict[str, Tuple[str, int, int]] = {
        "gmail.com": ("smtp.gmail.com", 465, 587),
        "outlook.com": ("smtp.office365.com", 587, 587),
        "hotmail.com": ("smtp.office365.com", 587, 587),
        "yahoo.com": ("smtp.mail.yahoo.com", 465, 587)
    }
    
    def __init__(self, sender_email: str) -> None:
        """
        Initializes the EmailService with sender details and probes connection.

        Args:
            sender_email (str): The email address that will send the messages.

        Raises:
            ValueError: If the email format is invalid or the domain is not supported.
        """
        self._sender_email: str = sender_email
        self._password: Optional[str] = None
        self._logger: Logger = setup_logging(
            file_name="email_log.txt", logger_name="EMAIL_LOG")
        
        if not self._is_valid_email(self._sender_email):
            self._logger.error(f"❌ Invalid email format: {self._sender_email}")
            raise ValueError(f"Formato de email inválido: {self._sender_email}")
        
        self._domain: str = self._sender_email.split('@')[-1].lower()
        
        if self._domain not in self.PROVIDERS:
            raise ValueError(
                f"Email domain '{self._domain}' is not supported. "
                f"Supported domains: {', '.join(self.PROVIDERS.keys())}")

        self._smtp_server, self._ssl_port, \
            self._tls_port = self.PROVIDERS[self._domain]
        
        self._active_port, self._use_ssl = self._probe_connection()
    
    def _is_valid_email(self, email: str) -> bool:
        """
        Validates the email format using regex.

        Args:
            email (str): Email string to validate.

        Returns:
            bool: True if valid, False otherwise.
        """
        regex = r'^[a-z0-9]+[\._]?[a-z0-9]+[@]\w+[.]\w{2,3}$'
        return bool(re.search(regex, email.lower()))
    
    def _probe_connection(self) -> Tuple[int, bool]:
        """
        Probes the SMTP server to check if it prefers SSL or TLS.

        Returns:
            Tuple[int, bool]: A tuple containing (active_port, use_ssl_flag).
        """
        try:
            with smtplib.SMTP_SSL(
                self._smtp_server, self._ssl_port, timeout=5):
                self._logger.info("SMTP server connected with SSL port.")
                return self._ssl_port, True
        except (smtplib.SMTPException, socket.timeout, OSError):
            self._logger.info("SMTP server connected with TLS port.")
            return self._tls_port, False
        
    def set_credentials(self, password: str) -> None:
        """
        Sets the password for the sender's email account.

        Args:
            password (str): The account password or app-specific password.

        Raises:
            ValueError: If the password is empty.
        """
        if not password:
            raise ValueError("Password cannot be empty.")
        self._password = str(password)
        self._logger.info(
            f"✅ {self._sender_email} password accepted. Ready to send emails.")
    
    def _authenticate(
            self, server: Union[smtplib.SMTP, smtplib.SMTP_SSL]) -> None:
        """
        Handles the authentication process for the SMTP server.

        Args:
            server: The active SMTP or SMTP_SSL instance.

        Raises:
            ValueError: If authentication fails due to invalid credentials.
            RuntimeError: For other authentication-related errors.
        """
        try:
            if not self._use_ssl:
                server.ehlo()
                server.starttls()
                server.ehlo()
                
            server.login(self._sender_email, self._password) # type: ignore
            self._logger.info(
            f"✅ Authentication successful for {self._sender_email}.")
        except smtplib.SMTPAuthenticationError:
            self._logger.error(
                f"❌ Auth Failed: Password invalid for {self._sender_email}")
            raise ValueError(
                f"Autenticação falhou: Senha inválida para {self._sender_email}")
        except Exception as e:
            self._logger.error(f"❌ Authentication error: {e}")
            raise RuntimeError(f"Erro de autenticação: {e}")
    
    def _add_attachment(self, msg: EmailMessage, file_path: str) -> None:
        """
        Reads a file and attaches it to the EmailMessage object.

        Args:
            msg (EmailMessage): The message object to attach the file to.
            file_path (str): The full path to the file.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        if not os.path.isfile(file_path):
            self._logger.error(f"❌ Attachment not found: {file_path}")
            raise FileNotFoundError(f"Anexo não encontrado: {file_path}")

        ctype, encoding = mimetypes.guess_type(file_path)
        if ctype is None or encoding is not None:
            ctype = 'application/octet-stream'
        
        maintype, subtype = ctype.split('/', 1)

        with open(file_path, 'rb') as f:
            msg.add_attachment(
                f.read(), 
                maintype=maintype,
                subtype=subtype, 
                filename=os.path.basename(file_path)
            )
    
    def _format_message(
            self, recipient: str, subject: str, body: str, 
            attachment_path: Optional[str] = None) -> EmailMessage:
        """
        Constructs the EmailMessage object with content and headers.

        Args:
            recipient (str): Destination email address.
            subject (str): Email subject line.
            body (str): Email body content.
            attachment_path (Optional[str]): Path to an optional attachment.

        Returns:
            EmailMessage: The formatted message object.
        """
        msg: EmailMessage = EmailMessage()
        msg.set_content(body)
        msg['Subject'] = subject
        msg['From'] = self._sender_email
        msg['To'] = recipient

        if attachment_path:
            self._add_attachment(msg, attachment_path)

        return msg
    
    def send_email(
            self, recipient: str, subject: str, body: str, 
            attachment_path: Optional[str] = None) -> bool:
        """
        Orchestrates the connection, authentication, and sending of an email.

        Args:
            recipient (str): Destination email address.
            subject (str): Email subject line.
            body (str): Email body content.
            attachment_path (Optional[str]): Optional path to a file attachment.

        Returns:
            bool: True if the email was sent successfully, False otherwise.

        Raises:
            RuntimeError: If credentials have not been set.
        """
        if not self._password:
            self._logger.error(
                f"❌ Credentials not set for {self._sender_email}.")
            raise RuntimeError(
                "As credenciais do email não foram configuradas. "
                "Chame set_credentials() primeiro.")
        
        try:
            msg = self._format_message(recipient, subject, body, attachment_path)
            smtp_class = smtplib.SMTP_SSL if self._use_ssl else smtplib.SMTP

            with smtp_class(
                self._smtp_server, self._active_port, timeout=30) as server:
                self._authenticate(server)
                server.send_message(msg)
                
                status = "com anexo" if attachment_path else "simples"
                self._logger.info(f"✅ Email {status} sent to {recipient}")
                return True
        except Exception as e:
            self._logger.error(f"❌ Error sending email: {e}")
            return False

# --- Bloco de Teste ---
if __name__ == "__main__":
    # Exemplo de uso do EmailService
    SENDER = "seu_email@gmail.com"
    RECEIVER = "destino@gmail.com"
    PASSWORD = "sua_senha_de_app" # Lembre-se de usar senhas de app para Gmail/Outlook
    
    email_svc = EmailService(SENDER)
    email_svc.set_credentials(PASSWORD)
    
    sucesso = email_svc.send_email(
        recipient=RECEIVER,
        subject="Teste de Sistema IoT",
        body="Este é um e-mail automático enviado pelo EmailService."
    )
    
    if sucesso:
        print("Teste concluído com sucesso!")
    else:
        print("Falha no envio do teste.")