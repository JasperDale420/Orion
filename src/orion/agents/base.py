from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseAgent(ABC):
    """
    Abstract Base Class for Orion AI Agents.
    """

    def __init__(self, name: str, model: str = "gpt-4o"):
        self.name = name
        self.model = model

    @abstractmethod
    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main execution loop for the agent.
        Takes context (e.g. a candidate trade) and returns a decision.
        """
        pass
