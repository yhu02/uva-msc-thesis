"""Abstract storage interface for ChaosProbe results."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class ResultStore(ABC):
    """Abstract interface for persisting experiment results."""

    @abstractmethod
    def save_run(self, run_data: Dict[str, Any]) -> str:
        """Save a complete run result.

        Args:
            run_data: Full output JSON from OutputGenerator.

        Returns:
            The run ID.
        """

    @abstractmethod
    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a run by its ID."""

    @abstractmethod
    def list_runs(
        self,
        scenario: Optional[str] = None,
        strategy: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List runs with optional filters."""

    @abstractmethod
    def get_metrics(
        self,
        run_id: str,
        metric_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get metrics for a run."""

    @abstractmethod
    def compare_strategies(
        self,
        scenario: Optional[str] = None,
        limit_per_strategy: int = 10,
    ) -> Dict[str, Any]:
        """Compare strategies across runs."""

    @abstractmethod
    def export_csv(self, output_path: str) -> str:
        """Export all runs to CSV."""
