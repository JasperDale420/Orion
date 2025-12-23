import types

from . import (
    get_headlines,
)


class NewsEndpoints:
    @classmethod
    def get_headlines(cls) -> types.ModuleType:
        """
        News Headlines
        """
        return get_headlines
