"""Prometheus metrics collector for mesh router.

Exposes mesh-specific gauges, counters (as gauges from DB counts), and
a Summary for task duration quantiles. All DB state is read in a single
GROUP BY query per scrape to minimize overhead.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Gauge, Summary, generate_latest


class MeshMetrics:
    """Collects and exposes mesh router metrics via prometheus-client."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()

        # --- Gauges: point-in-time DB state ---
        self.router_up = Gauge(
            "mesh_router_up",
            "1 if router is healthy",
            registry=self.registry,
        )
        self.tasks_queued = Gauge(
            "mesh_tasks_queued",
            "Tasks in queued state",
            registry=self.registry,
        )
        self.tasks_running = Gauge(
            "mesh_tasks_running",
            "Tasks in running state",
            registry=self.registry,
        )
        self.tasks_review = Gauge(
            "mesh_tasks_review",
            "Tasks in review state",
            registry=self.registry,
        )
        self.queue_depth = Gauge(
            "mesh_queue_depth",
            "Total pending tasks (queued + assigned + blocked)",
            registry=self.registry,
        )
        self.workers_total = Gauge(
            "mesh_workers_total",
            "Total registered workers",
            registry=self.registry,
        )
        self.workers_idle = Gauge(
            "mesh_workers_idle",
            "Idle workers",
            registry=self.registry,
        )
        self.workers_busy = Gauge(
            "mesh_workers_busy",
            "Busy workers",
            registry=self.registry,
        )
        self.workers_stale = Gauge(
            "mesh_workers_stale",
            "Stale workers (stale_since IS NOT NULL)",
            registry=self.registry,
        )
        self.uptime_seconds = Gauge(
            "mesh_uptime_seconds",
            "Router uptime in seconds",
            registry=self.registry,
        )

        # --- Totals: DB counts exposed as gauges (monotonic in practice) ---
        self.tasks_completed_total = Gauge(
            "mesh_tasks_completed_total",
            "Total completed tasks",
            registry=self.registry,
        )
        self.tasks_failed_total = Gauge(
            "mesh_tasks_failed_total",
            "Total failed tasks",
            registry=self.registry,
        )
        self.tasks_timeout_total = Gauge(
            "mesh_tasks_timeout_total",
            "Total timed out tasks",
            registry=self.registry,
        )
        self.dead_letters_total = Gauge(
            "mesh_dead_letters_total",
            "Dead letter events",
            registry=self.registry,
        )

        # --- Summary: task duration quantiles ---
        self.task_duration_seconds = Summary(
            "mesh_task_duration_seconds",
            "Task duration from creation to completion",
            registry=self.registry,
        )

    def collect_from_db(self, db: object, uptime_s: float) -> None:
        """Update gauge values from database state.

        Uses db.count_all_task_statuses() for a single GROUP BY query,
        and db.list_workers() for worker state. Two DB queries total
        (plus one for dead letters).
        """
        self.router_up.set(1)
        self.uptime_seconds.set(round(uptime_s, 1))

        # Single query for all task status counts
        status_counts = db.count_all_task_statuses()  # type: ignore[attr-defined]
        self.tasks_queued.set(status_counts.get("queued", 0))
        self.tasks_running.set(status_counts.get("running", 0))
        self.tasks_review.set(status_counts.get("review", 0))
        self.queue_depth.set(
            status_counts.get("queued", 0)
            + status_counts.get("assigned", 0)
            + status_counts.get("blocked", 0)
        )

        # Terminal status totals
        self.tasks_completed_total.set(status_counts.get("completed", 0))
        self.tasks_failed_total.set(status_counts.get("failed", 0))
        self.tasks_timeout_total.set(status_counts.get("timeout", 0))

        # Dead letters (single query)
        self.dead_letters_total.set(db.count_dead_letters())  # type: ignore[attr-defined]

        # Workers (single query, iterated in Python)
        workers = db.list_workers()  # type: ignore[attr-defined]
        self.workers_total.set(len(workers))
        self.workers_idle.set(sum(1 for w in workers if w.status == "idle"))
        self.workers_busy.set(sum(1 for w in workers if w.status == "busy"))
        self.workers_stale.set(
            sum(1 for w in workers if w.stale_since is not None)
        )

    def observe_task_duration(self, duration_seconds: float) -> None:
        """Record a task completion duration for Summary quantiles."""
        self.task_duration_seconds.observe(duration_seconds)

    def generate(self) -> bytes:
        """Generate Prometheus text exposition format."""
        return generate_latest(self.registry)
