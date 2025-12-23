from enum import Enum


class FlowAlertRule(str, Enum):
    FLOORTRADELARGECAP = "FloorTradeLargeCap"
    FLOORTRADEMIDCAP = "FloorTradeMidCap"
    FLOORTRADESMALLCAP = "FloorTradeSmallCap"
    LOWHISTORICVOLUMEFLOOR = "LowHistoricVolumeFloor"
    OTMEARNINGSFLOOR = "OtmEarningsFloor"
    REPEATEDHITS = "RepeatedHits"
    REPEATEDHITSASCENDINGFILL = "RepeatedHitsAscendingFill"
    REPEATEDHITSDESCENDINGFILL = "RepeatedHitsDescendingFill"
    SWEEPSFOLLOWEDBYFLOOR = "SweepsFollowedByFloor"
    VOLUMEOVEROI = "VolumeOverOi"

    def __str__(self) -> str:
        return str(self.value)
