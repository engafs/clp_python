import unittest
from unittest.mock import MagicMock, patch
import paho.mqtt.client as mqtt
from src.services.mqtt import MQTTManager


class TestMQTTManager(unittest.TestCase):
    def setUp(self):
        self.broker = "127.0.0.1"
        self.user = "admin"
        self.pw = "password123"
        self.mqtt_manager = MQTTManager(self.broker, 1883, self.user, self.pw)
        
        self.mock_paho = MagicMock(spec=mqtt.Client)
        self.mqtt_manager._client = self.mock_paho

    def test_initialize_client_with_auth(self):
        """Test that the MQTT client is initialized with the correct username 
            and password."""
        self.mqtt_manager._client = None
        
        with patch('paho.mqtt.client.Client', return_value=self.mock_paho):
            self.mqtt_manager._initialize_client()
            
            self.mock_paho.username_pw_set.assert_called_with(
                self.user, self.pw)
            self.assertIsNotNone(self.mqtt_manager._client)

    def test_validate_broker_ip(self):
        """Test that the broker IP format validation works correctly."""
        self.assertTrue(self.mqtt_manager._validate_broker_ip_format())
        
        invalid_manager = MQTTManager("broker.invalido.com")
        self.assertFalse(invalid_manager._validate_broker_ip_format())

    def test_add_alert_rule_and_trigger_callback(self):
        """Test that adding an alert rule and triggering the callback works 
            correctly."""
        topic = "factory/sensor1"
        callback = MagicMock()
        
        self.mqtt_manager.add_alert_rule(topic, None, callback)
        
        mock_msg = MagicMock()
        mock_msg.topic = topic
        mock_msg.payload = b"Valor: 50.5"
        
        self.mqtt_manager._on_message(self.mock_paho, None, mock_msg)
        
        callback.assert_called_once_with(topic, "Valor: 50.5")

    def test_publish_message_deduplication(self):
        """Test that publishing the same message to the same topic doesn't 
            result in multiple publications."""
        self.mock_paho.is_connected.return_value = True
        self.mqtt_manager._loop_running = True
        topic = "test/topic"
        
        self.mqtt_manager.publish_message(topic, "100")
        self.mqtt_manager.publish_message(topic, "100")
        
        self.assertEqual(self.mock_paho.publish.call_count, 1)

    def test_connect_broker_calls_paho_connect(self):
        """Test that connecting to the broker calls the paho MQTT client's 
            connect method."""
        with patch.object(self.mqtt_manager, '_wait_for_broker_connection',
                          return_value=True):
            result = self.mqtt_manager.connect_broker()
            
            self.assertTrue(result)
            self.mock_paho.connect.assert_called_with(self.broker, 1883)
            self.mock_paho.loop_start.assert_called_once()

    def test_disconnect_broker_stops_loop(self):
        """Test that disconnecting from the broker stops the MQTT loop and 
            calls the disconnect method."""
        self.mqtt_manager._loop_running = True
        
        self.mqtt_manager.disconnect_broker()
        
        self.mock_paho.loop_stop.assert_called_once()
        self.mock_paho.disconnect.assert_called_once()
        self.assertFalse(self.mqtt_manager._loop_running)

    def test_subscribe_topic_success(self):
        """Test that subscribing to a topic successfully calls the paho MQTT 
            client's subscribe method."""
        self.mock_paho.is_connected.return_value = True
        self.mock_paho.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
        
        result = self.mqtt_manager.subscribe_topic("test/topic")
        
        self.assertTrue(result)
        self.mock_paho.subscribe.assert_called_with("test/topic")

if __name__ == '__main__':
    unittest.main(verbosity=2)