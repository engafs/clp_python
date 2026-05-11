import operator
from typing import Callable, Any, Optional, Dict

class RuleEngine:
    """
    Logic engine for evaluating industrial rules and sensor thresholds.
    
    Provides a mapping between string-based operators (from YAML/JSON) 
    and Python's operator functions to determine if a rule condition is met.
    """
    OPERATIONS: Dict[str, Callable[[Any, Any], bool]] = {
        ">": operator.gt, ">=": operator.ge,
        "<": operator.lt, "<=": operator.le,
        "==": operator.eq, "!=": operator.ne
    }

    def get_operation(
            self, op_string: str) -> Optional[Callable[[Any, Any], bool]]:
        """
        Retrieves the corresponding operator function for a given string.

        Args:
            op_string: The comparison operator as a string (e.g., ">=").

        Returns:
            The operator function or None if the operator is not supported.
        """
        return self.OPERATIONS.get(op_string)

    def evaluate(
            self, current_value: Any, threshold: Any, op_string: str) -> bool:
        """
        Evaluates a logic expression: current_value [op_string] threshold.

        Example: evaluate(10, 5, ">") -> True

        Args:
            current_value: The real-time value from the PLC/Sensor.
            threshold: The limit value defined in the configuration.
            op_string: The operator to be applied.

        Returns:
            True if the condition is satisfied, False otherwise (including 
            invalid operators).
        """
        operation: Optional[Callable[[Any, Any], bool]] = self.get_operation(
            op_string)
        if not operation:
            return False
        
        try:
            return bool(operation(current_value, threshold))
        except (TypeError, ValueError):
            # Gracefully handle cases where types are incompatible (e.g., None > 10)
            return False