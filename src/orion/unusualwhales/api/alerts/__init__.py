import types

from . import (
    get_alerts,
    get_configurations,
)


class AlertsEndpoints:
    @classmethod
    def get_alerts(cls) -> types.ModuleType:
        """
        Alerts
        """
        return get_alerts

    @classmethod
    def get_configurations(cls) -> types.ModuleType:
        """
        Alert Configurations
        """
        return get_configurations
