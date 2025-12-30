import json
import logging
import os
from typing import Any, Dict, Optional

from aiokafka import AIOKafkaProducer
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from orion.shared.patterns import AsyncSingleton

logger = logging.getLogger(__name__)


class RedpandaProducer(AsyncSingleton):
    """
    Singleton wrapper for AIOKafkaProducer to send events to Redpanda.
    Reads config from env:
      - REDPANDA_BROKERS (default: localhost:9092)
    Configures robust production:
      - enable_idempotence=True
      - acks='all'
      - Automatic retries via tenacity
    """

    def __init__(self) -> None:
        self.bootstrap_servers = os.getenv("REDPANDA_BROKERS", "localhost:9092")
        self.producer: Optional[AIOKafkaProducer] = None

    async def start(self) -> None:
        """
        Initializes the AIOKafkaProducer. Must be called within an async loop.
        """
        if self.producer:
            return

        try:
            # Enable Idempotency and Strong Consistency
            self.producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                enable_idempotence=True,
                acks="all",
                # Retries are handled internally for idempotence, but we add application-level retry below
            )
            await self.producer.start()
            logger.info(f"Redpanda Producer started (brokers: {self.bootstrap_servers}, idempotence=True/acks=all)")
        except Exception as e:
            logger.error(f"Failed to start Redpanda Producer: {e}")
            self.producer = None

    async def stop(self) -> None:
        if self.producer:
            await self.producer.stop()
            logger.info("Redpanda Producer stopped.")
            self.producer = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.2, min=0.2, max=5.0),  # L1: increased backoff for infra failures
        retry=retry_if_exception_type(Exception),  # Catch broader exceptions during produce
        reraise=True,
    )
    async def produce_event(self, topic: str, key: str, payload: Dict[str, Any]) -> None:
        """
        Produces a message to the specified topic with retries.

        :param topic: Kafka topic name (e.g. 'orion.events.bronze')
        :param key: Partition key (e.g. ticker or event_id)
        :param payload: Dict payload
        :raises RuntimeError: If producer not initialized (start() must be called at app startup)
        """
        if not self.producer:
            logger.error(
                "Producer not initialized - start() must be called at app startup",
                extra={"topic": topic, "key": key, "error_code": "PRODUCER_NOT_INITIALIZED"},
            )
            raise RuntimeError("RedpandaProducer not initialized. Call start() during application startup.")

        await self.producer.send_and_wait(topic, value=payload, key=key.encode("utf-8"))
