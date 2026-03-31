"""
ML Performance Tracker.

Logs ML predictions and outcomes for performance evaluation.
"""

import uuid
from typing import Any

from sqlalchemy import text

from orion.shared.db_utils import db_write
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ml.performance_tracker")


async def log_entry_prediction(
    symbol: str,
    option_chain: str,
    bucket: str,
    prediction_score: float,
    confidence: float | None = None,
    position_id: str | None = None,
) -> str | None:
    """Log an entry prediction for performance tracking.

    Args:
        symbol: Ticker symbol
        option_chain: OCC option symbol
        bucket: Trade bucket (0DTE, SHORT_SWING, SWING, POSITION)
        prediction_score: ML model score (0-1)
        confidence: Optional confidence score
        position_id: Optional Alpaca position ID

    Returns:
        Prediction ID if logged successfully, None otherwise
    """
    prediction_id = str(uuid.uuid4())
    prediction_class = 1 if prediction_score >= 0.5 else 0

    def write(session: Any) -> None:
        session.execute(
            text("""
                INSERT INTO ml_predictions
                (id, symbol, option_chain, bucket, model_type, prediction_score,
                 prediction_class, confidence, position_id)
                VALUES (:id, :symbol, :option_chain, :bucket, 'entry_score',
                        :score, :pred_class, :confidence, :position_id)
            """),
            {
                "id": prediction_id,
                "symbol": symbol,
                "option_chain": option_chain,
                "bucket": bucket,
                "score": prediction_score,
                "pred_class": prediction_class,
                "confidence": confidence,
                "position_id": position_id,
            },
        )

    try:
        await db_write(write)
        logger.debug(f"Logged entry prediction for {symbol}: score={prediction_score:.3f}")
        return prediction_id
    except Exception as e:
        logger.error(f"Failed to log entry prediction: {e}")
        return None


async def log_exit_prediction(
    symbol: str,
    option_chain: str,
    bucket: str,
    prediction_score: float,
    position_id: str | None = None,
) -> str | None:
    """Log an exit prediction for performance tracking."""
    prediction_id = str(uuid.uuid4())
    prediction_class = 1 if prediction_score >= 0.5 else 0

    def write(session: Any) -> None:
        session.execute(
            text("""
                INSERT INTO ml_predictions
                (id, symbol, option_chain, bucket, model_type, prediction_score,
                 prediction_class, position_id)
                VALUES (:id, :symbol, :option_chain, :bucket, 'exit_score',
                        :score, :pred_class, :position_id)
            """),
            {
                "id": prediction_id,
                "symbol": symbol,
                "option_chain": option_chain,
                "bucket": bucket,
                "score": prediction_score,
                "pred_class": prediction_class,
                "position_id": position_id,
            },
        )

    try:
        await db_write(write)
        logger.debug(f"Logged exit prediction for {symbol}: score={prediction_score:.3f}")
        return prediction_id
    except Exception as e:
        logger.error(f"Failed to log exit prediction: {e}")
        return None


async def log_outcome(
    position_id: str,
    actual_return_pct: float,
    hit_target: bool,
    hit_stop: bool,
) -> bool:
    """Update prediction with actual outcome when position closes.

    Args:
        position_id: Alpaca position ID
        actual_return_pct: Actual return percentage
        hit_target: Whether trade hit profit target
        hit_stop: Whether trade hit stop loss

    Returns:
        True if updated successfully
    """

    def write(session: Any) -> None:
        # Determine if prediction was correct
        # For entry: correct if hit_target and predicted 1, or didn't and predicted 0
        session.execute(
            text("""
                UPDATE ml_predictions
                SET outcome_ts = NOW(),
                    actual_return_pct = :return_pct,
                    hit_target = :hit_target,
                    hit_stop = :hit_stop,
                    prediction_correct = CASE
                        WHEN model_type = 'entry_score' THEN
                            (prediction_class = 1 AND :hit_target) OR
                            (prediction_class = 0 AND NOT :hit_target)
                        WHEN model_type = 'exit_score' THEN
                            (prediction_class = 1 AND :return_pct >= 0)
                        ELSE NULL
                    END
                WHERE position_id = :position_id
                AND outcome_ts IS NULL
            """),
            {
                "position_id": position_id,
                "return_pct": actual_return_pct,
                "hit_target": hit_target,
                "hit_stop": hit_stop,
            },
        )

    try:
        await db_write(write)
        logger.info(f"Logged outcome for position {position_id}: return={actual_return_pct:.2f}%")
        return True
    except Exception as e:
        logger.error(f"Failed to log outcome: {e}")
        return False


async def get_daily_accuracy(bucket: str | None = None) -> dict[str, Any]:
    """Get prediction accuracy for today.

    Returns:
        Dict with accuracy metrics
    """
    from orion.shared.db_utils import db_query

    async def query(session: Any) -> Any:
        sql = """
                SELECT
                    COUNT(*) as total_predictions,
                    COUNT(CASE WHEN prediction_correct THEN 1 END) as correct,
                    COUNT(CASE WHEN prediction_correct = false THEN 1 END) as incorrect,
                    ROUND(
                        COUNT(CASE WHEN prediction_correct THEN 1 END)::numeric /
                        NULLIF(COUNT(CASE WHEN prediction_correct IS NOT NULL THEN 1 END), 0) * 100,
                    2) as accuracy_pct,
                    AVG(actual_return_pct) FILTER (WHERE prediction_class = 1) as avg_return_high_score,
                    AVG(actual_return_pct) FILTER (WHERE prediction_class = 0) as avg_return_low_score
                FROM ml_predictions
                WHERE prediction_ts >= CURRENT_DATE
            """
        params: dict[str, Any] = {}
        if bucket:
            sql += "\n                AND bucket = :bucket"
            params["bucket"] = bucket
        result = await session.execute(
            text(sql),
            params,
        )
        return result.fetchone()

    try:
        result = await db_query(query)
        if result:
            return {
                "total": result[0] or 0,
                "correct": result[1] or 0,
                "incorrect": result[2] or 0,
                "accuracy_pct": float(result[3]) if result[3] else None,
                "avg_return_high_score": float(result[4]) if result[4] else None,
                "avg_return_low_score": float(result[5]) if result[5] else None,
            }
    except Exception as e:
        logger.error(f"Failed to get daily accuracy: {e}")

    return {"total": 0, "correct": 0, "incorrect": 0, "accuracy_pct": None}


async def get_weekly_performance() -> dict[str, Any]:
    """Get weekly prediction performance by bucket.

    Returns:
        Dict with per-bucket performance metrics
    """
    from orion.shared.db_utils import db_query

    async def query(session: Any) -> Any:
        result = await session.execute(
            text("""
                SELECT
                    bucket,
                    model_type,
                    COUNT(*) as predictions,
                    COUNT(CASE WHEN prediction_correct THEN 1 END) as correct,
                    ROUND(
                        COUNT(CASE WHEN prediction_correct THEN 1 END)::numeric /
                        NULLIF(COUNT(CASE WHEN prediction_correct IS NOT NULL THEN 1 END), 0) * 100,
                    2) as accuracy_pct,
                    AVG(actual_return_pct) as avg_return
                FROM ml_predictions
                WHERE prediction_ts >= CURRENT_DATE - INTERVAL '7 days'
                AND outcome_ts IS NOT NULL
                GROUP BY bucket, model_type
                ORDER BY bucket, model_type
            """)
        )
        return result.fetchall()

    try:
        results = await db_query(query)
        performance = {}
        for row in results or []:
            key = f"{row[0]}_{row[1]}"
            performance[key] = {
                "predictions": row[2],
                "correct": row[3],
                "accuracy_pct": float(row[4]) if row[4] else None,
                "avg_return": float(row[5]) if row[5] else None,
            }
        return performance
    except Exception as e:
        logger.error(f"Failed to get weekly performance: {e}")
        return {}
