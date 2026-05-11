from opcua import Client, ua
from opcua.common.node import Node
from typing import Dict, Any, Union, Optional, Tuple, List, Callable, TypeVar, cast
from datetime import datetime
from ipaddress import ip_address
from logging import getLogger, Logger
from functools import wraps
import time

# Definindo um TypeVar para preservar a assinatura da função no decorador
F = TypeVar('F', bound=Callable[..., Any])

def _ensure_connected(func: F) -> F:
    @wraps(func)
    def wrapper(self: 'OPCUAClient', *args: Any, **kwargs: Any) -> Any:
        if self._client is None:
            self._logger.error(
                f"❌ Fail to execute '{func.__name__}': Client not connected.")
            raise ConnectionError(
                "CODESYS Client is not connected. Call connect() first.")
        return func(self, *args, **kwargs)
    return cast(F, wrapper)


class OPCUAClient:
    """
    Interface for communication with PLCs via the OPC UA protocol.
    
    This class provides methods to connect, disconnect, and perform read/write 
    operations on CODESYS-based PLCs.
    """

    DEFAULT_PORT: int = 4840

    DATA_TYPES_PLC: Dict[type, ua.VariantType] = {
        bool: ua.VariantType.Boolean,
        str: ua.VariantType.String,
        int: ua.VariantType.Int16,
        float: ua.VariantType.Double,
        datetime: ua.VariantType.DateTime
    }

    def __init__(
            self, plc_ip: str, plc_name: str, port: int = DEFAULT_PORT) -> None:
        """
        Initializes the OPCUA client instance.

        Args:
            plc_ip (str): IP address of the PLC.
            plc_name (str): Friendly name for the PLC (used in logging).
            port (int): Communication port (defaults to 4840).
        """
        self._plc_ip: str = plc_ip
        self._plc_name: str = plc_name
        self._port: int = port
        self._client: Optional[Client] = None
        self._logger: Logger = getLogger("PLC_LOG")
        
        self._pou_node_cache: Dict[str, str] = {}
        self._variables_value_cache: Dict[str, Any] = {}

    def _validate_plc_ip_format(self) -> bool:
        """
        Validates if the PLC IP address follows a correct IPv4/IPv6 format.

        Returns:
            bool: True if valid, False otherwise.
        """
        try:
            ip_address(self._plc_ip)
            return True
        except ValueError:
            return False

    def _is_connected(self) -> bool:
        """
        Checks if the client object is instantiated.

        Returns:
            bool: True if the client exists.
        """
        return self._client is not None

    def connect(self) -> bool:
        """
        Establishes a connection to the OPC UA server.

        Returns:
            bool: True if connection successful or already connected, False otherwise.
        """
        if not self._validate_plc_ip_format():
            self._logger.error(f"❌ Invalid PLC IP format: {self._plc_ip}")
            return False

        if self._is_connected():
            return True

        url: str = f"opc.tcp://{self._plc_ip}:{self._port}"
        self._client = Client(url)
        try:
            self._client.connect()
            self._logger.info(
                f"✅ PLC CODESYS connected successfully. URL: {url}")
            return True
        except Exception as e:
            self._client = None
            self._logger.error(f"❌ Error connecting to PLC CODESYS: {e}")
            return False

    def disconnect(self) -> bool:
        """
        Closes the connection to the OPC UA server and clears caches.

        Returns:
            bool: Always returns True after attempting disconnection.
        """
        if not self._is_connected():
            return True
        
        try:
            if self._client:
                self._client.disconnect()
            self._logger.info(f"🔌 PLC {self._plc_name} disconnected.")
        except Exception as e:
            self._logger.warning(f"⚠️ Warning while disconnecting: {e}")
        finally:
            self._client = None
            self._pou_node_cache.clear()
            self._variables_value_cache.clear()
        return True
    
    @_ensure_connected
    def _get_info(self) -> Dict[str, str]:
        """
        Retrieves the identification and connection details of the PLC.
        
        This method is protected by the '_ensure_connected' decorator, 
        ensuring that info is only returned if the communication is active.

        Returns:
            Dict[str, str]: A dictionary containing the PLC's friendly name,
            IP and port.
        """
        
        return {
            "name": self._plc_name,
            "ip": self._plc_ip,
            "port": str(self._port)
        }
    
    @_ensure_connected
    def _get_server_state(self) -> str:
        """
        Retrieves the current operational state of the OPC UA server.

        Returns:
            str: The state name (e.g., 'Running', 'Failed', 'Shutdown').
        """
        try:
            state_node: Node = self._client.get_node("ns=0;i=2259") # type: ignore
            state_value: int = state_node.get_value()
            
            states: Dict[int, str] = {
                0: "Running", 1: "Failed", 2: "NoConfiguration",
                3: "Suspended", 4: "Shutdown"
            }
            current_state: str = states.get(
                state_value, f"Unknown ({state_value})")
            
            self._logger.info(f"📡 Server State: {current_state}")
            return current_state
        except Exception as e:
            self._logger.error(f"❌ Could not retrieve server state: {e}")
            return "Disconnected/Error"
    
    @_ensure_connected
    def _check_process_health(
        self, pou_name: str, heartbeat_var: str, timeout: int = 1) -> bool:
        """
        Checks if the PLC logic is running by monitoring a changing variable.

        Args:
            pou_name (str): Name of the POU/GVL.
            heartbeat_var (str): Name of the variable that should increment/change.
            timeout (int): Seconds to wait between reads.

        Returns:
            bool: True if the variable value changed, False if it stayed the same.
        """
        val_1: Any = self.get_value(pou_name, heartbeat_var)
        if val_1 is None:
            return False
            
        time.sleep(timeout)
        val_2: Any = self.get_value(pou_name, heartbeat_var)
        
        is_alive: bool = val_1 != val_2
        if is_alive:
            self._logger.info(f"💓 Heartbeat OK: {val_1} -> {val_2}")
        else:
            self._logger.warning(
                "💔 Heartbeat STUCK: Process logic might be STOPPED.")            
        return is_alive

    def _is_plc_healthy(self, heartbeat_pou: Optional[str] = None,
                        heartbeat_var: Optional[str] = None) -> bool:
        """
        Performs a general health check on the PLC.

        Args:
            heartbeat_pou (Optional[str]): POU for heartbeat check.
            heartbeat_var (Optional[str]): Variable for heartbeat check.

        Returns:
            bool: True if server is running and optional heartbeat is valid.
        """
        if not self._is_connected():
            return False
            
        if self._get_server_state() != "Running":
            return False
            
        if heartbeat_pou and heartbeat_var:
            return self._check_process_health(heartbeat_pou, heartbeat_var)
            
        return True

    @_ensure_connected
    def find_pou_node_id(
        self, name_pou_file: str, node: Optional[Node] = None) -> Optional[str]:
        """
        Recursively searches for the NodeID of a specific POU or GVL.

        Args:
            name_pou_file (str): The name of the POU to find.
            node (Optional[Node]): The starting node for the search (defaults to Root).

        Returns:
            Optional[str]: The string representation of the NodeID if found.
        """
        if name_pou_file in self._pou_node_cache:
            return self._pou_node_cache[name_pou_file]

        if node is None:
            node = self._client.get_root_node() # type: ignore

        try:
            browse_name: str = node.get_browse_name().Name
            if browse_name == name_pou_file:
                node_id: str = node.nodeid.to_string()
                self._pou_node_cache[name_pou_file] = node_id
                return node_id
            
            for child in node.get_children():
                res: Optional[str] = self.find_pou_node_id(
                    name_pou_file, child)
                if res:
                    return res
        except Exception:
            pass
        return None

    @_ensure_connected
    def get_variable_path(self, pou_name: str, var_name: str) -> Optional[str]:
        """
        Constructs the full address path for a variable.

        Args:
            pou_name (str): The container name.
            var_name (str): The variable name.

        Returns:
            Optional[str]: The full path string or None if POU not found.
        """
        pou_id: Optional[str] = self.find_pou_node_id(pou_name)
        if not pou_id:
            self._logger.error(f"❌ POU '{pou_name}' not found.")
            return None
        return f"{pou_id}.{var_name}"

    @_ensure_connected
    def get_node(self, node_id: str) -> Node:
        """
        Retrieves an OPC UA Node object from a string ID.

        Args:
            node_id (str): The NodeID string.

        Returns:
            Node: The OPC UA Node object.
        """
        return self._client.get_node(node_id) # type: ignore

    @_ensure_connected
    def get_value(self, pou_name: str, var_name: str) -> Any:
        """
        Reads the value of a variable from the PLC.

        Args:
            pou_name (str): The POU/GVL name.
            var_name (str): The variable name.

        Returns:
            Any: The value of the variable or None if an error occurs.
        """
        full_path: Optional[str] = self.get_variable_path(pou_name, var_name)
        if not full_path:
            return None
        
        try:
            node: Node = self.get_node(full_path)
            value: Any = node.get_value()
            self._variables_value_cache[var_name] = value
            return value
        except Exception as e:
            self._logger.error(f"❌ Error to read {var_name}: {e}")
            return None
    
    @_ensure_connected
    def _validate_and_get_node(
            self, pou_name: str, var_name: str,
            new_value: Any) -> Optional[Tuple[Node, ua.VariantType]]:
        """
        Validates data types and returns the target node for writing.

        Args:
            pou_name (str): The container name.
            var_name (str): The variable name.
            new_value (Any): The value intended for writing.

        Returns:
            Optional[Tuple[Node, ua.VariantType]]: A tuple with the Node and 
                its expected VariantType.

        Raises:
            TypeError: If the Python type does not match the PLC variable type.
        """
        new_value_type: type = type(new_value)
        if new_value_type not in self.DATA_TYPES_PLC:
            self._logger.error(
                f"❌ '{new_value_type.__name__}' type not supported.")
            return None

        full_path: Optional[str] = self.get_variable_path(pou_name, var_name)
        if not full_path:
            return None

        node: Node = self.get_node(full_path)
        actual_plc_type: ua.VariantType = node.get_data_type_as_variant_type()
        expected_type: ua.VariantType = self.DATA_TYPES_PLC[new_value_type]

        if actual_plc_type != expected_type:
            raise TypeError(
                f"Type mismatch: '{new_value_type.__name__}' vs "
                f"PLC type {actual_plc_type}.")

        return node, expected_type

    @_ensure_connected
    def _set_value(self, pou_name: str, var_name: str, new_value: Any) -> bool:
        """
        Writes a value to a PLC variable.

        Args:
            pou_name (str): The POU/GVL name.
            var_name (str): The variable name.
            new_value (Any): The value to write.

        Returns:
            bool: True if writing succeeded, False otherwise.
        """
        try:
            val_res: Optional[Tuple[Node, ua.VariantType]] = \
                self._validate_and_get_node(
                pou_name, var_name, new_value)
            
            if not val_res:
                return False
                
            node, expected_type = val_res
            node.set_value(ua.DataValue(ua.Variant(new_value, expected_type)))
            self._logger.info(f"✅ '{var_name}' updated to '{new_value}'.")
            return True

        except TypeError as te:
            self._logger.error(f"❌ Type error: {te}")
            return False
        except Exception as e:
            self._logger.error(f"❌ Error writing to {var_name}: {e}")
            return False

    @_ensure_connected
    def read_all_variables(
        self, pou_names: Union[str, List[str]]) -> Dict[str, Any]:
        """
        Reads all children variables from one or more POUs/GVLs.

        Args:
            pou_names (Union[str, List[str]]): A single name or a list of 
                POU names.

        Returns:
            Dict[str, Any]: A dictionary containing {variable_name: value}.
        """
        if isinstance(pou_names, str):
            pou_names = [pou_names]

        combined_results: Dict[str, Any] = {}

        for pou in pou_names:
            pou_id: Optional[str] = self.find_pou_node_id(pou)
            if not pou_id:
                self._logger.warning(
                    f"⚠️ POU/GVL '{pou}' not founded on server.")
                continue

            self._logger.info(f"🔍 Inspecting file: {pou}")
            pou_node: Node = self.get_node(pou_id)
            
            for child in pou_node.get_children():
                name: str = "Unknown"
                try:
                    node_class = child.get_node_class()
                    if node_class != ua.NodeClass.Variable:
                        continue

                    name = child.get_browse_name().Name
                    val: Any = child.get_value()
                    
                    combined_results[name] = val
                    self._logger.info(f"📄 {pou}.{name}: {val}")
                except Exception as e:
                    if "BadAttributeIdInvalid" not in str(e):
                        self._logger.warning(
                            f"⚠️ Error to read {pou}.{name}: {e}")
                    continue        
        return combined_results
    

if __name__ == '__main__':
    import logging
    import os
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("opcua").setLevel(logging.WARNING)
    logging.getLogger("opcua.client.ua_client").setLevel(logging.WARNING)
    logging.getLogger("opcua.uaprotocol").setLevel(logging.WARNING)
    
    plc = OPCUAClient(plc_ip=os.getenv("PLC_IP"), plc_name='localhost')

    try:
        if plc.connect():
            print("\n--- 🟢 Iniciando Testes Multiarquivos ---")

            arquivos_clp = ["GVL_telegram", "PLC_PRG"]

            if not arquivos_clp:
                print("⚠️ Atenção: Nenhuma POU encontrada automaticamente.")
                raise Exception(
                    "Nenhuma POU detectada. Verifique a conexão e o servidor.")

            print(f"Estado operacional do servidor do PLC: "
                  f"{plc._get_server_state()}")
            print()

            print(f"Caminho do arquivo {arquivos_clp[0]}: "
                  f"{plc.get_variable_path(arquivos_clp[0], 'i_start_button')}")
            
            print(f"\n--- 📄 Lendo variáveis de: {arquivos_clp} ---")
            todas_vars = plc.read_all_variables(arquivos_clp)
            print(f"✅ Total de variáveis lidas no sistema: {len(todas_vars)}")

            GVL_NAME = "GVL_telegram"
            VAR_REMOTE = "ri_start_button"

            print(f"\n--- ⚙️ Testando Comando Remoto na GVL: {VAR_REMOTE} ---")
            
            valor_atual = plc.get_value(GVL_NAME, VAR_REMOTE)
            if valor_atual is not None:
                novo_valor = not valor_atual
                if plc._set_value(GVL_NAME, VAR_REMOTE, novo_valor):
                    print(f"✅ Sucesso: {VAR_REMOTE} alterado para {novo_valor}")
                else:
                    print(f"❌ Falha ao escrever na GVL")

            print(f"\n--- 🔍 Verificando Status no PLC_PRG ---")
            status_motor = plc.get_value("PLC_PRG", "o_box_conveyor")
            print(f"📊 Status da Esteira (o_box_conveyor): {status_motor}")

        else:
            print("❌ Falha crítica: Não foi possível estabelecer comunicação.")

    except Exception as e:
        print(f"💥 Erro durante a execução dos testes: {e}")

    finally:
        print("\n--- 🔌 Finalizando ---")
        plc.disconnect()