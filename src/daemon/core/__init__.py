"""
Core module for Voice Assistant Daemon
"""
from .state import StateMachine, AssistantState
from .settings import SettingsObserver
from .model_manager import ModelManager
from .pipeline import SentenceAggregator, FastPathDispatcher, PipelineController

try:
    from skills.skill_registry import SkillRegistry
    from skills.vector_intent_matcher import VectorIntentMatcher
except Exception:  # pragma: no cover - optional import path in some runtime contexts
    SkillRegistry = None
    VectorIntentMatcher = None

__all__ = ['StateMachine', 'AssistantState', 'SettingsObserver', 'ModelManager', 'SentenceAggregator', 'FastPathDispatcher', 'PipelineController', 'SkillRegistry', 'VectorIntentMatcher']
