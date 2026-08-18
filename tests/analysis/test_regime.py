"""Tests for MultiAxisRegimeDetector's vix provenance fields.

RegimeGate hard-blocks trading on a SHOCK vol regime, but the only vix
source wired in today (VIXY-close proxy) is not a real spot-VIX reading.
detect() must carry a source/observed_at tag through so downstream code
can tell a proxy estimate apart from a trusted one instead of treating
every fresh vix_level as ground truth.
"""

from datetime import UTC, datetime, timedelta

from orion.analysis.regime import MultiAxisRegimeDetector, is_trusted_vix_source


class TestIsTrustedVixSource:
    def test_spot_vix_is_trusted(self):
        assert is_trusted_vix_source("spot_vix") is True

    def test_proxy_source_is_not_trusted(self):
        assert is_trusted_vix_source("proxy:VIXY") is False

    def test_none_source_is_not_trusted(self):
        assert is_trusted_vix_source(None) is False

    def test_unknown_source_is_not_trusted(self):
        assert is_trusted_vix_source("something_made_up") is False


class TestDetectCarriesVixProvenance:
    def test_detect_threads_vix_source_and_observed_at_onto_snapshot(self):
        detector = MultiAxisRegimeDetector()
        now = datetime.now(UTC)
        observed_at = now - timedelta(minutes=1)

        snapshot = detector.detect(
            ts=now,
            vix=40.0,
            vix_source="proxy:VIXY",
            vix_observed_at=observed_at,
        )

        assert snapshot.vix_level == 40.0
        assert snapshot.vix_source == "proxy:VIXY"
        assert snapshot.vix_observed_at == observed_at

    def test_detect_defaults_vix_provenance_to_none(self):
        detector = MultiAxisRegimeDetector()

        snapshot = detector.detect(ts=datetime.now(UTC))

        assert snapshot.vix_source is None
        assert snapshot.vix_observed_at is None
