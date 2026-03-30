"""Tests for orchestrator module."""

import asyncio
import pytest
from ortobahn.orchestrator import Orchestrator, TaskStatus


@pytest.mark.asyncio
async def test_orchestrator_basic_execution():
    """Test basic task execution."""
    orchestrator = Orchestrator(max_concurrent_llm=5)
    
    async def task1():
        await asyncio.sleep(0.1)
        return "result1"
    
    async def task2():
        await asyncio.sleep(0.1)
        return "result2"
    
    orchestrator.add_task("task1", task1)
    orchestrator.add_task("task2", task2)
    
    results = await orchestrator.run()
    
    assert results["task1"]["status"] == "success"
    assert results["task1"]["result"] == "result1"
    assert results["task2"]["status"] == "success"
    assert results["task2"]["result"] == "result2"


@pytest.mark.asyncio
async def test_orchestrator_dependencies():
    """Test dependency resolution."""
    orchestrator = Orchestrator()
    execution_order = []
    
    async def task1():
        execution_order.append("task1")
        await asyncio.sleep(0.1)
        return "result1"
    
    async def task2():
        execution_order.append("task2")
        return "result2"
    
    orchestrator.add_task("task1", task1)
    orchestrator.add_task("task2", task2, dependencies=["task1"])
    
    results = await orchestrator.run()
    
    assert execution_order == ["task1", "task2"]
    assert results["task2"]["status"] == "success"


@pytest.mark.asyncio
async def test_orchestrator_timeout():
    """Test timeout handling."""
    orchestrator = Orchestrator()
    
    async def slow_task():
        await asyncio.sleep(5)
        return "result"
    
    orchestrator.add_task("slow", slow_task)
    
    results = await orchestrator.run(timeout=0.1)
    
    assert results["slow"]["status"] == "error"
    assert "timed out" in results["slow"]["error"].lower()


@pytest.mark.asyncio
async def test_orchestrator_error_handling():
    """Test error handling."""
    orchestrator = Orchestrator()
    
    async def failing_task():
        raise ValueError("Task failed")
    
    orchestrator.add_task("fail", failing_task)
    
    results = await orchestrator.run()
    
    assert results["fail"]["status"] == "error"
    assert "Task failed" in results["fail"]["error"]


@pytest.mark.asyncio
async def test_orchestrator_circular_dependency():
    """Test circular dependency detection."""
    orchestrator = Orchestrator()
    
    async def task1():
        return "result1"
    
    async def task2():
        return "result2"
    
    orchestrator.add_task("task1", task1, dependencies=["task2"])
    orchestrator.add_task("task2", task2, dependencies=["task1"])
    
    with pytest.raises(ValueError, match="Circular dependency"):
        await orchestrator.run()


@pytest.mark.asyncio
async def test_orchestrator_semaphore():
    """Test LLM request semaphore."""
    max_concurrent = 3
    orchestrator = Orchestrator(max_concurrent_llm=max_concurrent)
    active_tasks = []
    max_active = [0]
    
    async def task():
        active_tasks.append(1)
        max_active[0] = max(max_active[0], len(active_tasks))
        await asyncio.sleep(0.1)
        active_tasks.pop()
        return "done"
    
    for i in range(10):
        orchestrator.add_task(f"task{i}", task)
    
    await orchestrator.run()
    
    assert max_active[0] <= max_concurrent
