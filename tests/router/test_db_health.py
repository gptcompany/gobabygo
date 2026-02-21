"""Tests for DB health check methods and Prometheus metrics."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

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
