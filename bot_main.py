from src.monitoring_main import IndustrialMonitoringSystem
import threading
import asyncio

system = IndustrialMonitoringSystem(config_filename="config_scene_10.yaml")

telegram_thread = threading.Thread(
        target=system.telegram_service.poll_commands, 
        args=(system._telegram_command_handler,),
        daemon=True)
telegram_thread.start() 
try:
    asyncio.run(system.run())
except KeyboardInterrupt:
    system._logger_mqtt.info("🛑 User interrupted the system.")
finally:
    system.telegram_service._pool_commands_running = False
    system.mqtt.disconnect_broker()
    system._logger_mqtt.info("✅ System closed and Broker disconnected.")