from abc import ABC, abstractmethod
from typing import Any


class BaseAgent(ABC):
    """
    Abstract Base Class for Orion AI Agents.
    """

    def __init__(self, name: str, model: str = "glm-5.1"):
        self.name = name
        self.model = model

    @abstractmethod
    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        Main execution loop for the agent.
        Takes context (e.g. a candidate trade) and returns a decision.
        """
        pass
