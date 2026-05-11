import unittest
from src.core.rules import RuleEngine

class TestRuleEngine(unittest.TestCase):
    
    def setUp(self):
        """Initialize the engine before each test."""
        self.engine = RuleEngine()

    def test_get_operation_valid(self):
        """Test if valid operator strings return the correct callable."""
        op = self.engine.get_operation(">=")
        self.assertIsNotNone(op)
        self.assertTrue(op(10, 5))
        self.assertFalse(op(5, 10))

    def test_get_operation_invalid(self):
        """Test if invalid operator strings return None."""
        self.assertIsNone(self.engine.get_operation("??"))
        self.assertIsNone(self.engine.get_operation("AND"))

    def test_evaluate_basic_comparisons(self):
        """Test standard numerical logic evaluations."""
        self.assertTrue(self.engine.evaluate(10, 5, ">"))
        self.assertTrue(self.engine.evaluate(5, 10, "<"))
        self.assertTrue(self.engine.evaluate(10, 10, "=="))
        self.assertFalse(self.engine.evaluate(10, 5, "=="))
        self.assertFalse(self.engine.evaluate(10, 20, ">="))

    def test_evaluate_edge_cases(self):
        """Test equality and inequality thresholds."""
        self.assertTrue(self.engine.evaluate(10, 10, ">="))
        self.assertTrue(self.engine.evaluate(10, 10, "<="))
        self.assertTrue(self.engine.evaluate(10, 5, "!="))
        self.assertFalse(self.engine.evaluate(10, 10, "!="))

    def test_evaluate_invalid_operator(self):
        """Test if an invalid operator string returns False instead of raising an error."""
        result = self.engine.evaluate(10, 5, "invalid_op")
        self.assertFalse(result)

    def test_evaluate_incompatible_types(self):
        """Test if the engine handles None or mismatched types safely."""
        # Case: PLC returns None due to communication loss
        self.assertFalse(self.engine.evaluate(None, 10, ">"))
        # Case: Threshold is a string by mistake in YAML
        self.assertFalse(self.engine.evaluate(10, "error", ">"))

if __name__ == '__main__':
    unittest.main(verbosity=2)