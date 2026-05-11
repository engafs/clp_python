import paho.mqtt.client as mqtt
from ipaddress import ip_address
from typing import Optional, Any, Dict, Callable, List, Union
from src.utils.logging_config import setup_logging
from logging import Logger
import time

class MQTTManager:
    """
    Manager for MQTT communication, handling connections, publishing, and alerts.
    
    This class wraps the paho-mqtt client to provide a more robust interface 
    for industrial IoT applications, including automatic state tracking 
    and threshold-based callbacks.
    """
    
    DEFAULT_PORT: int = 1883
    CONNECTION_TIMEOUT_SECONDS: int = 5

    def __init__(
            self, broker_address: str, port: int = DEFAULT_PORT,
            username: Optional[str] = None, password: Optional[str] = None) -> None:
        """
        Initializes the MQTTManager instance.

        Args:
            broker_address (str): IP address or hostname of the MQTT broker.
            port (int): Network port for the broker (default is 1883).
            username (Optional[str]): Username for authentication.
            password (Optional[str]): Password for authentication.
        """
        self._broker_address: str = broker_address
        self._port: int = port
        self._username: Optional[str] = username
        self._password: Optional[str] = password
        self._client: Optional[mqtt.Client] = None
        self._loop_running: bool = False
        self._last_sent_values: Dict[str, str] = {}
        self._alert_rules: Dict[str, List[Dict[str, Any]]] = {}
        self._logger: Logger = setup_logging(
            file_name="mqtt_log.txt", 
            logger_name="MQTT_LOG")

    def _validate_broker_ip_format(self) -> bool:
        """
        Validates the format of the broker IP address.

        Returns:
            bool: True if the format is a valid IP, False otherwise.
        """
        try:
            ip_address(self._broker_address)
            return True
        except ValueError:
            return False

    def _initialize_client(self) -> None:
        """
        Initializes the Paho MQTT client with Callback API V2.
        """
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        
        if self._username and self._password:
            self._client.username_pw_set(self._username, self._password)
            self._logger.info("🔐 MQTT Credentials set.")
            
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def _on_connect(
            self, client: mqtt.Client, userdata: Any, flags: Dict[str, Any], 
            rc: int, properties: Optional[Any] = None) -> None:
        """
        Callback triggered when the client connects to the broker.

        Args:
            client (mqtt.Client): The client instance.
            userdata (Any): Private user data.
            flags (Dict): Response flags from the broker.
            rc (int): Connection result code (0 is success).
            properties (Optional[Any]): MQTT v5 properties.
        """
        if rc == 0:
            self._logger.info(
                "✅ ESTABLISHED CONNECTION: Broker client accepted.")
            for topic in self._alert_rules.keys():
                client.subscribe(topic)
        else:
            self._logger.error(f"❌ CONNECTION FAIL: Error code {rc}")

    def _on_disconnect(
            self, client: mqtt.Client, userdata: Any, disconnect_flags: Any,
            rc: int, properties: Optional[Any] = None) -> None:
        """
        Callback triggered when the client disconnects.

        Args:
            client (mqtt.Client): The client instance.
            userdata (Any): Private user data.
            disconnect_flags (Any): Disconnection flags.
            rc (int): Disconnection result code.
            properties (Optional[Any]): MQTT v5 properties.
        """
        self._loop_running = False
        self._logger.warning(
            f"⚠️ EVENT: Disconnected from broker (Code: {rc})")

    def add_alert_rule(self, topic: str, threshold_value: Optional[str],
                        callback: Callable[[str, str], None]) -> None:
        """
        Adds an alert rule to trigger a callback when a specific value is 
        received on a topic.

        Args:
            topic (str): The MQTT topic to monitor.
            threshold_value (Optional[str]): The value that triggers the alert. 
                If None, any message triggers the callback.
            callback (Callable): Function to execute when the rule matches 
                (topic, payload).
        """
        if topic not in self._alert_rules:
            self._alert_rules[topic] = []

        self._alert_rules[topic].append(
            {"threshold": threshold_value, "callback": callback})
        self._logger.info(f"🔔 Rule added to topic '{topic}'")

    def _on_message(self, client: mqtt.Client, userdata: Any,
                    msg: mqtt.MQTTMessage) -> None:
        """
        Callback triggered when a message is received from the broker.

        Args:
            client (mqtt.Client): The client instance.
            userdata (Any): Private user data.
            msg (mqtt.MQTTMessage): The received message object.
        """
        payload: str = msg.payload.decode('utf-8')
        topic: str = msg.topic

        self._logger.info(f"📥 MESSAGE RECEIVED: Topic '{topic}' -> {payload}")
        
        if topic in self._alert_rules:
            for rule in self._alert_rules[topic]:
                try:
                    threshold: Optional[str] = rule.get("threshold")
                    # If threshold is None, it triggers for any message, else it must match exactly
                    if threshold is None or payload == threshold:
                        rule["callback"](topic, payload)
                except Exception as e:
                    self._logger.error(
                        f"❌ Error executing callback for {topic}: {e}")

    @property
    def _is_connected(self) -> bool:
        """
        Checks if the MQTT client is currently connected.

        Returns:
            bool: True if connected, False otherwise.
        """
        return self._client.is_connected() if self._client else False

    def _wait_for_broker_connection(self) -> bool:
        """
        Blocks until the connection is established or timeout is reached.

        Returns:
            bool: True if connected, False if timeout.
        """
        start_time: float = time.time()
        while not self._is_connected:
            if time.time() - start_time > self.CONNECTION_TIMEOUT_SECONDS:
                self._logger.error("❌ TIMEOUT: The broker did not respond.")
                return False
            time.sleep(0.1)
        return True

    def connect_broker(self) -> bool:
        """
        Initiates connection to the MQTT broker and starts the background loop.

        Returns:
            bool: True if connection was successful.
        """
        if not self._client:
            self._initialize_client()

        try:
            # We cast because _client is initialized in _initialize_client
            if self._client:
                self._client.connect(self._broker_address, self._port)
                self._client.loop_start()
                self._loop_running = True          
                return self._wait_for_broker_connection()
            return False
        except (ConnectionRefusedError, TimeoutError) as e:
            self._logger.error(
                f"❌ NETWORK ERROR: Cannot access {self._broker_address}: {e}")
            return False

    def _can_publish(self) -> bool:
        """
        Validates if the client is in a valid state to publish messages.

        Returns:
            bool: True if connected and loop is running.
        """
        return self._is_connected and self._loop_running

    def publish_message(self, topic: str, message: Any) -> bool:
        """
        Publishes a message to a specific topic. Includes change tracking to 
        avoid redundant updates.

        Args:
            topic (str): Target MQTT topic.
            message (Any): Content to be sent (will be converted to string).

        Returns:
            bool: True if published successfully or value hasn't changed.
        """
        if not self._is_connected:
            self._logger.info(
                "🔄 Trying automatic reconnection before publish...")
            self.connect_broker()

        if not self._can_publish():
            self._logger.warning(
                f"⚠️ DENIED PUBLISH: Disconnected Client. Topic: {topic}")
            return False     

        new_payload: str = str(message)
        # Avoid flooding the broker if the value is the same as the last sent
        if self._last_sent_values.get(topic) == new_payload:
            return True

        try:
            if self._client:
                result = self._client.publish(topic, new_payload)
                result.wait_for_publish()    
                self._last_sent_values[topic] = new_payload

                self._logger.info(
                    f"✅ SUCCESS: Message sent to {topic}: {new_payload}")
                return True
            return False
        except Exception as e:
            self._logger.error(f"❌ ERROR: Failed to publish to {topic}: {e}")
            return False
   
    def subscribe_topic(self, topic: str) -> bool:
        """
        Subscribes the client to a specific topic.

        Args:
            topic (str): The topic string to subscribe to.

        Returns:
            bool: True if subscription was successful.
        """
        if not self._is_connected:
            self._logger.info(
                f"🔄 Trying automatic reconnection before subscribing to: {topic}")
            if not self.connect_broker():
                return False

        try:
            if self._client:
                result, _ = self._client.subscribe(topic)
                if result == mqtt.MQTT_ERR_SUCCESS:
                    self._logger.info(
                        f"✅ SUCCESS: Subscribed to topic '{topic}'")
                    return True
                else:
                    self._logger.error(
                        "❌ ERROR: Failed to subscribe to "
                        f"'{topic}' (Code: {result})")
            return False
        except Exception as e:
            self._logger.error(
                f"❌ ERROR: Exception while subscribing to '{topic}': {e}")
            return False
    
    def disconnect_broker(self) -> bool:
        """
        Stops the background loop and gracefully disconnects from the broker.

        Returns:
            bool: True if disconnection was clean.
        """
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
                self._loop_running = False
                self._logger.info(
                    "✅ DISCONNECTED: MQTT connection closed safely.")
                return True
            except Exception as e:
                self._logger.error(f"❌ ERROR: Failed to disconnect: {e}")
                return False
        return True


if __name__ == "__main__":
    BROKER_TEST = "192.168.15.10"  # Public broker for tests
    ALERT_TOPIC = "v1/industrial/sensores/temperatura"

    # Function to be called when the alert condition is met
    def display_alert_topic_function(topic: str, payload: str):
        print(f"\n[ALERTA DISPARADO] 🚨")
        print(f"Tópico: {topic} | Valor Crítico Recebido: {payload}\n")

    # Setting up the MQTTManager with the test broker. If you have a password-protected broker, you can provide credentials here (username and password).
    manager = MQTTManager(broker_address=BROKER_TEST)

    print(f"--- Iniciando Teste do MQTTManager ---")

    manager.add_alert_rule(
        topic=ALERT_TOPIC,
        threshold_value="90", 
        callback=display_alert_topic_function)

    # Try to connect to the broker and run the test sequence
    if manager.connect_broker():
        try:
            print("Enviando dados normais...")
            manager.publish_message(ALERT_TOPIC, "25") # Must NOT trigger callback
            
            time.sleep(2) 
            
            print("Enviando valor crítico para testar alerta...")
            manager.publish_message(ALERT_TOPIC, "90") # MUST trigger callback
            
            # Mantém o script rodando um pouco para receber o retorno do broker
            print("Aguardando mensagens do broker (Ctrl+C para encerrar)...")
            while True:
                time.sleep(1)
                
        except KeyboardInterrupt:
            print("\nEncerrando testes...")
        finally:
            manager.disconnect_broker()
    else:
        print("Não foi possível iniciar os testes devido a falha na conexão.")