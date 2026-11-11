#!/usr/bin/env python3
"""Run a long-duration MCP soak test with periodic reconnects and health checks."""
from __future__ import annotations
import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def extract_result(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "exit_code" in structured:
        return structured
    for item in getattr(result, "content", []):
        try:
            candidate = json.loads(getattr(item, "text", ""))
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(candidate, dict) and "exit_code" in candidate:
            return candidate
    raise RuntimeError("MCP tool response did not contain structured command output")


def percentile(samples: list[float], fraction: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return round(ordered[index], 3)


async def check_health(client: httpx.AsyncClient, base_url: str) -> None:
    for path in ("/healthz", "/readyz"):
        response = await client.get(base_url.rstrip("/") + path)
        response.raise_for_status()
    response = await client.get(base_url.rstrip("/") + "/health/control-plane")
    response.raise_for_status()
    payload = response.json()
    details = payload.get("details") or {}
    if payload.get("status") != "ok" or not details.get("last_success"):
        raise RuntimeError("Tunnel control-plane health is not healthy")


async def worker(worker_id: int, args: argparse.Namespace, deadline: float,
                 counters: Counter[str], latencies: list[float]) -> None:
    sequence = 0
    backoff = 1.0
    while time.monotonic() < deadline:
        try:
            async with streamablehttp_client(args.url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    if "run_command" not in {item.name for item in tools.tools}:
                        raise RuntimeError("run_command is not advertised")
                    counters["sessions_opened"] += 1
                    calls_in_session = 0
                    while time.monotonic() < deadline and calls_in_session < args.reconnect_every:
                        sequence += 1
                        calls_in_session += 1
                        expected_timeout = sequence % 100 == 0
                        expected_output = sequence % 50 == 0 and not expected_timeout
                        if expected_timeout:
                            command, timeout = "sleep 2", 1
                        elif expected_output:
                            command, timeout = 'python3 -c "print(\'x\' * 50000)"', 120
                        else:
                            command = f"printf 'soak-worker-{worker_id}-{sequence}'"
                            timeout = 120
                        started = time.monotonic()
                        result = await session.call_tool(
                            "run_command", {"command": command, "timeout_seconds": timeout}
                        )
                        elapsed = time.monotonic() - started
                        data = extract_result(result)
                        if data.get("exit_code") == 75:
                            counters["busy_responses"] += 1
                            await asyncio.sleep(0.25)
                            continue
                        if expected_timeout:
                            if data.get("timed_out") is True:
                                counters["expected_timeouts"] += 1
                            else:
                                counters["failures"] += 1
                        elif expected_output:
                            size = len(data.get("stdout", "")) + len(data.get("stderr", ""))
                            if data.get("output_truncated") and size <= 20_000:
                                counters["output_limit_checks"] += 1
                            else:
                                counters["failures"] += 1
                        elif data.get("exit_code") == 0:
                            counters["successful_calls"] += 1
                        else:
                            counters["failures"] += 1
                        if len(latencies) < 10_000:
                            latencies.append(elapsed)
                    backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception:
            counters["transport_errors"] += 1
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    counters: Counter[str] = Counter()
    latencies: list[float] = []
    started = time.monotonic()
    deadline = started + args.duration_seconds
    next_health = started
    next_report = started
    async with httpx.AsyncClient(timeout=5) as health_client:
        workers = [asyncio.create_task(
            worker(i, args, deadline, counters, latencies)
        ) for i in range(args.concurrency)]
        try:
            while time.monotonic() < deadline:
                now = time.monotonic()
                if args.health_url and now >= next_health:
                    try:
                        await check_health(health_client, args.health_url)
                        counters["health_checks_passed"] += 1
                    except Exception:
                        counters["health_check_failures"] += 1
                    next_health = now + args.health_check_interval_seconds
                if now >= next_report:
                    print(json.dumps({
                        "elapsed_seconds": round(now - started, 1), **dict(counters)
                    }), flush=True)
                    next_report = now + args.report_interval_seconds
                await asyncio.sleep(1)
            await asyncio.gather(*workers)
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
    return {
        "duration_seconds": args.duration_seconds,
        "concurrency": args.concurrency,
        "url": args.url,
        "counters": dict(counters),
        "latency_seconds": {
            "samples": len(latencies),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": round(max(latencies), 3) if latencies else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--duration-seconds", type=int, default=86400)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--reconnect-every", type=int, default=100)
    parser.add_argument("--health-url", default="")
    parser.add_argument("--health-check-interval-seconds", type=int, default=30)
    parser.add_argument("--report-interval-seconds", type=int, default=60)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.duration_seconds < 1 or args.reconnect_every < 1:
        parser.error("duration and reconnect interval must be positive")
    if not 1 <= args.concurrency <= 128:
        parser.error("--concurrency must be between 1 and 128")
    if args.health_check_interval_seconds < 1 or args.report_interval_seconds < 1:
        parser.error("health and report intervals must be positive")
    report = asyncio.run(run(args))
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    counts = report["counters"]
    if counts.get("successful_calls", 0) == 0 or counts.get("failures", 0):
        return 1
    if counts.get("transport_errors", 0) or counts.get("health_check_failures", 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
