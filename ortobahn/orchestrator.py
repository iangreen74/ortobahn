"""Orchestrator for running agents in parallel with dependency resolution."""

import asyncio
import logging
from typing import Dict, List, Set, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class Task:
    """Represents an agent task."""
    id: str
    agent_fn: Any
    dependencies: List[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Any] = None
    error: Optional[Exception] = None


class Orchestrator:
    """Orchestrates parallel agent execution with dependency management."""

    def __init__(self, max_concurrent_llm: int = 10, default_timeout: float = 300.0):
        """Initialize orchestrator.
        
        Args:
            max_concurrent_llm: Maximum concurrent LLM requests
            default_timeout: Default task timeout in seconds
        """
        self.max_concurrent_llm = max_concurrent_llm
        self.default_timeout = default_timeout
        self.llm_semaphore = asyncio.Semaphore(max_concurrent_llm)
        self.tasks: Dict[str, Task] = {}
        self.task_events: Dict[str, asyncio.Event] = {}

    def add_task(self, task_id: str, agent_fn: Any, dependencies: Optional[List[str]] = None) -> None:
        """Add a task to the orchestrator.
        
        Args:
            task_id: Unique task identifier
            agent_fn: Async function to execute
            dependencies: List of task IDs this task depends on
        """
        if task_id in self.tasks:
            raise ValueError(f"Task {task_id} already exists")
        
        self.tasks[task_id] = Task(
            id=task_id,
            agent_fn=agent_fn,
            dependencies=dependencies or []
        )
        self.task_events[task_id] = asyncio.Event()

    def _validate_dependencies(self) -> None:
        """Validate task dependency graph for cycles."""
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def has_cycle(task_id: str) -> bool:
            visited.add(task_id)
            rec_stack.add(task_id)

            for dep in self.tasks[task_id].dependencies:
                if dep not in self.tasks:
                    raise ValueError(f"Task {task_id} depends on non-existent task {dep}")
                if dep not in visited:
                    if has_cycle(dep):
                        return True
                elif dep in rec_stack:
                    return True

            rec_stack.remove(task_id)
            return False

        for task_id in self.tasks:
            if task_id not in visited:
                if has_cycle(task_id):
                    raise ValueError(f"Circular dependency detected involving {task_id}")

    async def _execute_task(self, task: Task, timeout: Optional[float] = None) -> Any:
        """Execute a single task with LLM semaphore and timeout.
        
        Args:
            task: Task to execute
            timeout: Task timeout in seconds
            
        Returns:
            Task result
        """
        task.status = TaskStatus.RUNNING
        timeout = timeout or self.default_timeout

        try:
            # Wait for dependencies
            for dep_id in task.dependencies:
                await self.task_events[dep_id].wait()
                if self.tasks[dep_id].status == TaskStatus.FAILED:
                    raise RuntimeError(f"Dependency {dep_id} failed")

            # Execute with semaphore for backpressure
            async with self.llm_semaphore:
                logger.info(f"Executing task {task.id}")
                result = await asyncio.wait_for(task.agent_fn(), timeout=timeout)
                task.result = result
                task.status = TaskStatus.COMPLETED
                self.task_events[task.id].set()
                return result

        except asyncio.TimeoutError:
            logger.error(f"Task {task.id} timed out after {timeout}s")
            task.status = TaskStatus.TIMEOUT
            task.error = TimeoutError(f"Task timed out after {timeout}s")
            self.task_events[task.id].set()
            raise
        except Exception as e:
            logger.error(f"Task {task.id} failed: {e}")
            task.status = TaskStatus.FAILED
            task.error = e
            self.task_events[task.id].set()
            raise

    async def run(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Run all tasks in parallel respecting dependencies.
        
        Args:
            timeout: Individual task timeout
            
        Returns:
            Dictionary mapping task IDs to results
        """
        if not self.tasks:
            return {}

        self._validate_dependencies()

        # Create all task coroutines
        task_coros = [
            self._execute_task(task, timeout)
            for task in self.tasks.values()
        ]

        # Execute all tasks in parallel
        results = await asyncio.gather(*task_coros, return_exceptions=True)

        # Collect results
        output = {}
        for task_id, result in zip(self.tasks.keys(), results):
            if isinstance(result, Exception):
                output[task_id] = {"status": "error", "error": str(result)}
            else:
                output[task_id] = {"status": "success", "result": result}

        return output

    def get_task_status(self, task_id: str) -> TaskStatus:
        """Get current status of a task."""
        if task_id not in self.tasks:
            raise ValueError(f"Task {task_id} not found")
        return self.tasks[task_id].status

    def clear(self) -> None:
        """Clear all tasks and reset orchestrator."""
        self.tasks.clear()
        self.task_events.clear()
