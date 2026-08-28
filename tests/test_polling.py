"""Tests for polling-based tools: export_and_download, materialize_and_wait."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch


def _mock_client() -> MagicMock:
    """Create a mock SigmaClient with preset methods."""
    c = MagicMock()
    c.export_workbook = AsyncMock()
    c.download_query_raw = AsyncMock()
    c.materialize_workbook = AsyncMock()
    c.get_materialization_job = AsyncMock()
    return c


# ─── Export polling ───────────────────────────────────────────────────────────


class TestExportPolling:
    async def test_export_polls_until_200(self) -> None:
        from sigma_mcp.server import sigma_export_and_download

        mc = _mock_client()
        mc.export_workbook.return_value = {"queryId": "q-1"}

        resp_204 = MagicMock()
        resp_204.status_code = 204

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.content = b"csv,data,here"

        mc.download_query_raw.side_effect = [resp_204, resp_204, resp_200]

        with patch("sigma_mcp.server.get_client", AsyncMock(return_value=mc)):
            with patch("sigma_mcp.server.asyncio.sleep", new_callable=AsyncMock):
                result_str = await sigma_export_and_download("wb-1", format="csv", timeout_seconds=60)

        result = json.loads(result_str)
        assert result["status"] == "completed"
        assert mc.download_query_raw.call_count == 3

    async def test_export_timeout_does_not_hang(self) -> None:
        from sigma_mcp.server import sigma_export_and_download

        mc = _mock_client()
        mc.export_workbook.return_value = {"queryId": "q-timeout"}

        resp_204 = MagicMock()
        resp_204.status_code = 204
        mc.download_query_raw.return_value = resp_204

        # Use a very small timeout and patch time.time to simulate elapsed time
        call_count = [0]
        original_time = time.time

        def fake_time() -> float:
            # After 2 download attempts, time "jumps" past deadline
            call_count[0] += 1
            if call_count[0] > 3:
                return original_time() + 999
            return original_time()

        with patch("sigma_mcp.server.get_client", AsyncMock(return_value=mc)):
            with patch("sigma_mcp.server.asyncio.sleep", new_callable=AsyncMock):
                with patch("sigma_mcp.server.time.time", side_effect=fake_time):
                    result_str = await sigma_export_and_download("wb-1", format="csv", timeout_seconds=1)

        result = json.loads(result_str)
        assert result.get("error") == "timeout"
        assert "query_id" in result


# ─── Materialize and wait ─────────────────────────────────────────────────────


class TestMaterializeAndWait:
    async def test_pending_to_completed(self) -> None:
        from sigma_mcp.server import sigma_materialize_and_wait

        mc = _mock_client()
        mc.materialize_workbook.return_value = {"materializationId": "job-1"}
        mc.get_materialization_job.side_effect = [
            {"status": "pending"},
            {"status": "running"},
            {"status": "completed", "rows": 100},
        ]

        with patch("sigma_mcp.server.get_client", AsyncMock(return_value=mc)):
            with patch("sigma_mcp.server.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result_str = await sigma_materialize_and_wait("wb-1", "elem-1", timeout_seconds=60)

        result = json.loads(result_str)
        assert result["status"] == "completed"
        assert result["job"]["rows"] == 100
        # Proves asyncio.sleep was awaited (no blocking time.sleep)
        assert mock_sleep.await_count >= 2

    async def test_failed_job_returns_structured_error(self) -> None:
        from sigma_mcp.server import sigma_materialize_and_wait

        mc = _mock_client()
        mc.materialize_workbook.return_value = {"materializationId": "job-fail"}
        mc.get_materialization_job.return_value = {"status": "failed", "error": "query timeout"}

        with patch("sigma_mcp.server.get_client", AsyncMock(return_value=mc)):
            with patch("sigma_mcp.server.asyncio.sleep", new_callable=AsyncMock):
                result_str = await sigma_materialize_and_wait("wb-1", "elem-1", timeout_seconds=60)

        result = json.loads(result_str)
        assert result["status"] == "failed"
        assert result["job"]["error"] == "query timeout"

    async def test_no_blocking_sleep(self) -> None:
        """Prove that asyncio.sleep is used, not time.sleep."""
        from sigma_mcp.server import sigma_materialize_and_wait

        mc = _mock_client()
        mc.materialize_workbook.return_value = {"materializationId": "j1"}
        mc.get_materialization_job.side_effect = [
            {"status": "pending"},
            {"status": "completed"},
        ]

        with patch("sigma_mcp.server.get_client", AsyncMock(return_value=mc)):
            with patch("sigma_mcp.server.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await sigma_materialize_and_wait("wb-1", "e1", timeout_seconds=60)

        assert mock_sleep.await_count >= 1
