import json
import logging
import os
from typing import Any, Dict, Optional

from aiokafka import AIOKafkaProducer
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class RedpandaProducer:
    """
    Singleton-ish wrapper for AIOKafkaProducer to send events to Redpanda.
    Reads config from env:
      - REDPANDA_BROKERS (default: localhost:9092)
    Configures robust production:
      - enable_idempotence=True
      - acks='all'
      - Automatic retries via tenacity
    """

    _instance = None

    def __init__(self):
        self.bootstrap_servers = os.getenv("REDPANDA_BROKERS", "localhost:9092")
        self.producer: Optional[AIOKafkaProducer] = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = RedpandaProducer()
        return cls._instance

    async def start(self):
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

    async def stop(self):
        if self.producer:
            await self.producer.stop()
            logger.info("Redpanda Producer stopped.")
            self.producer = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, min=0.1, max=1.0),
        retry=retry_if_exception_type(Exception),  # Catch broader exceptions during produce
        reraise=True,
    )
    async def produce_event(self, topic: str, key: str, payload: Dict[str, Any]):
        """
        Produces a message to the specified topic with retries.
        :param topic: Kafka topic name (e.g. 'orion.events.bronze')
        :param key: Partition key (e.g. ticker or event_id)
        :param payload: Dict payload
        """
        if not self.producer:
            # Try to start if not started? Or just fail?
            # Ideally start() is called at app startup.
            logger.warning("Producer not started. Attempting lazy start...")
            await self.start()
            if not self.producer:
                raise RuntimeError("Producer unavailable and failed to start.")

        # Fire and wait for Ack (Required for Idempotence guarantee to mean anything to us)
        await self.producer.send_and_wait(topic, value=payload, key=key.encode("utf-8"))
