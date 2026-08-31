import unittest
import sys
from pathlib import Path

daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

try:
    from daemon.core.state import StateMachine, AssistantState
except ImportError:
    from core.state import StateMachine, AssistantState


class TestCoreState(unittest.TestCase):
    def test_state_transitions(self):
        sm = StateMachine(initial_state=AssistantState.IDLE.value)
        self.assertEqual(sm.state, AssistantState.IDLE.value)

        changed_states = []
        sm.add_callback(lambda s: changed_states.append(s))

        # Transition to listening
        res = sm.set_state(AssistantState.LISTENING.value)
        self.assertTrue(res)
        self.assertEqual(sm.state, AssistantState.LISTENING.value)
        self.assertEqual(changed_states, [AssistantState.LISTENING.value])

        # Duplicate transition returns False
        res = sm.set_state(AssistantState.LISTENING.value)
        self.assertFalse(res)
        self.assertEqual(changed_states, [AssistantState.LISTENING.value])

        # Transition to processing
        sm.set_state(AssistantState.PROCESSING.value)
        self.assertEqual(sm.state, AssistantState.PROCESSING.value)
        self.assertEqual(changed_states, [AssistantState.LISTENING.value, AssistantState.PROCESSING.value])


if __name__ == '__main__':
    unittest.main()
