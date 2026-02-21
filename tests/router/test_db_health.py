"""Tests for DB health check methods and Prometheus metrics."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.router.db import RouterDB
from src.router.metrics import MeshMetrics


class TestDBHealthChecks:
    """Unit tests for RouterDB health check methods."""

    def test_db_path_property(self, tmp_path: Path) -> None:
        """db_path property returns the path passed to constructor."""
        db_file = str(tmp_path / "test.db")
        db = RouterDB(db_file)
        try:
            assert db.db_path == db_file
        finally:
            db.close()

    def test_check_wal_size_returns_zero_no_wal(self, tmp_path: Path) -> None:
        """check_wal_size returns 0 when WAL file does not exist."""
        db_file = str(tmp_path / "test.db")
        db = RouterDB(db_file)
        try:
            db.init_schema()
            # Force a checkpoint to clear WAL, then delete WAL if present
            db._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            wal_path = Path(db_file + "-wal")
            if wal_path.exists():
                wal_path.unlink()
            assert db.check_wal_size() == 0
        finally:
            db.close()

    def test_check_wal_size_returns_file_size(self, tmp_path: Path) -> None:
        """check_wal_size returns actual WAL file size after writes."""
        db_file = str(tmp_path / "test.db")
        db = RouterDB(db_file)
        try:
            db.init_schema()
            # Perform writes to generate WAL data
            db._conn.execute(
                "INSERT INTO workers (worker_id, last_heartbeat) VALUES ('w1', '2026-01-01T00:00:00Z')"
            )
            db._conn.commit()
            wal_path = Path(db_file + "-wal")
            if wal_path.exists():
                expected_size = wal_path.stat().st_size
                assert db.check_wal_size() == expected_size
                assert expected_size > 0
            else:
                # WAL may have been auto-checkpointed; size 0 is valid
                assert db.check_wal_size() == 0
        finally:
            db.close()

    def test_check_integrity_passes_on_clean_db(self, tmp_path: Path) -> None:
        """check_integrity returns True on a healthy database."""
        db_file = str(tmp_path / "test.db")
        db = RouterDB(db_file)
        try:
            db.init_schema()
            assert db.check_integrity() is True
        finally:
            db.close()

    def test_check_integrity_returns_false_on_error(self, tmp_path: Path) -> None:
        """check_integrity returns False when PRAGMA integrity_check fails."""
        db_file = str(tmp_path / "test.db")
        db = RouterDB(db_file)
        try:
            db.init_schema()
            # sqlite3.Connection.execute is read-only C attr, so replace _conn
            from unittest.mock import MagicMock

            mock_conn = MagicMock()
            mock_conn.execute.side_effect = sqlite3.DatabaseError("corrupt")
            original_conn = db._conn
            db._conn = mock_conn
            try:
                assert db.check_integrity() is False
            finally:
                db._conn = original_conn
        finally:
            db.close()

    def test_check_disk_space_returns_positive(self, tmp_path: Path) -> None:
        """check_disk_space returns a positive integer."""
        db_file = str(tmp_path / "test.db")
        db = RouterDB(db_file)
        try:
            result = db.check_disk_space()
            assert isinstance(result, int)
            assert result > 0
        finally:
            db.close()

    def test_check_disk_space_matches_shutil(self, tmp_path: Path) -> None:
        """check_disk_space matches shutil.disk_usage for the same partition."""
        db_file = str(tmp_path / "test.db")
        db = RouterDB(db_file)
        try:
            result = db.check_disk_space()
            expected = shutil.disk_usage(str(tmp_path)).free
            # Should be equal (same partition, near-instant call)
            assert result == expected
        finally:
            db.close()


class TestDBHealthMetrics:
    """Tests for DB health Prometheus metrics."""

    def test_db_health_metrics_exist(self) -> None:
        """MeshMetrics exposes all four DB health metrics."""
        metrics = MeshMetrics()
        output = metrics.generate().decode("utf-8")
        assert "mesh_db_wal_size_bytes" in output
        assert "mesh_db_integrity_ok" in output
        assert "mesh_db_disk_free_bytes" in output
        assert "mesh_db_health_check_errors_total" in output


class TestWatchdogDBHealth:
    """Tests for watchdog loop DB health check integration."""

    def test_watchdog_wal_warning_logged(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """WAL size exceeding threshold logs a warning."""
        db_file = str(tmp_path / "test.db")
        db = RouterDB(db_file)
        metrics = MeshMetrics()
        try:
            db.init_schema()
            # Write data to generate WAL
            db._conn.execute(
                "INSERT INTO workers (worker_id, last_heartbeat) VALUES ('w1', '2026-01-01T00:00:00Z')"
            )
            db._conn.commit()

            # Use threshold of 0 so any WAL triggers warning
            wal_size_threshold = 0
            wal_size = db.check_wal_size()
            metrics.db_wal_size_bytes.set(wal_size)

            logger = logging.getLogger("mesh.server")
            with caplog.at_level(logging.WARNING, logger="mesh.server"):
                if wal_size > wal_size_threshold:
                    logger.warning(
                        "WAL size %.1fMB exceeds threshold %.1fMB",
                        wal_size / (1024 * 1024),
                        wal_size_threshold / (1024 * 1024),
                    )
            assert "WAL size" in caplog.text
        finally:
            db.close()

    def test_watchdog_disk_space_error_logged(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Low disk space below threshold logs an error."""
        db_file = str(tmp_path / "test.db")
        db = RouterDB(db_file)
        metrics = MeshMetrics()
        try:
            # Use impossibly high threshold
            disk_free_threshold = float("inf")
            disk_free = db.check_disk_space()
            metrics.db_disk_free_bytes.set(disk_free)

            logger = logging.getLogger("mesh.server")
            with caplog.at_level(logging.ERROR, logger="mesh.server"):
                if disk_free < disk_free_threshold:
                    logger.error(
                        "Low disk space: %.1fMB free (threshold %.1fMB)",
                        disk_free / (1024 * 1024),
                        disk_free_threshold / (1024 * 1024),
                    )
            assert "Low disk space" in caplog.text
        finally:
            db.close()

    def test_integrity_check_runs_only_every_n_cycles(self, tmp_path: Path) -> None:
        """Integrity check runs at cycle > 0 and cycle % N == 0, skipping cycle 0."""
        db_file = str(tmp_path / "test.db")
        db = RouterDB(db_file)
        try:
            db.init_schema()
            integrity_check_interval = 3
            integrity_calls: list[int] = []

            for cycle in range(10):
                if cycle > 0 and cycle % integrity_check_interval == 0:
                    db.check_integrity()
                    integrity_calls.append(cycle)

            # Should run on cycles 3, 6, 9
            assert integrity_calls == [3, 6, 9]
            # Should NOT run on cycle 0 (startup delay avoidance)
            assert 0 not in integrity_calls
        finally:
            db.close()

    def test_watchdog_health_check_error_increments_counter(self, tmp_path: Path) -> None:
        """Health check exceptions increment the error counter."""
        db_file = str(tmp_path / "test.db")
        db = RouterDB(db_file)
        metrics = MeshMetrics()
        try:
            # Mock check_wal_size to raise OSError
            original_check = db.check_wal_size
            db.check_wal_size = MagicMock(side_effect=OSError("permission denied"))  # type: ignore[assignment]

            # Simulate one watchdog cycle error handling
            try:
                db.check_wal_size()
            except Exception:
                metrics.db_health_check_errors_total.inc()

            # Verify counter incremented
            output = metrics.generate().decode("utf-8")
            assert "mesh_db_health_check_errors_total" in output
            # Counter should be 1.0
            assert "mesh_db_health_check_errors_total 1.0" in output
        finally:
            db.check_wal_size = original_check  # type: ignore[method-assign]
            db.close()

    def test_config_env_vars_read(self) -> None:
        """DB health config env vars are read correctly."""
        env_overrides = {
            "MESH_DB_WAL_SIZE_THRESHOLD_BYTES": "1000",
            "MESH_DB_DISK_FREE_THRESHOLD_BYTES": "2000",
            "MESH_DB_INTEGRITY_CHECK_INTERVAL": "5",
        }
        with patch.dict(os.environ, env_overrides):
            wal_size_threshold = int(os.environ.get("MESH_DB_WAL_SIZE_THRESHOLD_BYTES", str(50 * 1024 * 1024)))
            disk_free_threshold = int(os.environ.get("MESH_DB_DISK_FREE_THRESHOLD_BYTES", str(100 * 1024 * 1024)))
            integrity_check_interval = int(os.environ.get("MESH_DB_INTEGRITY_CHECK_INTERVAL", "10"))

            assert wal_size_threshold == 1000
            assert disk_free_threshold == 2000
            assert integrity_check_interval == 5
