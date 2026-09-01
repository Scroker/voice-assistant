from abc import ABC, abstractmethod
from typing import Dict, Any

class NativeTool(ABC):
    """Abstract base class for native GNOME tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool unique identifier."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable tool description."""
        pass

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """JSON Schema for tool arguments."""
        pass

    @abstractmethod
    async def execute(self, args: Dict[str, Any]) -> str:
        """Execute the tool action and return a result string."""
        pass

    def to_schema(self) -> Dict[str, Any]:
        """Return tool schema formatted for LLM function calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
