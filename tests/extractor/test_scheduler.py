import asyncio

from extractor.scheduler import DomainScheduler


def test_get_concurrency_returns_override_for_configured_domain() -> None:
    scheduler = DomainScheduler(default_concurrency=2, domain_concurrency={"cnbc.com": 5})

    assert scheduler.get_concurrency("cnbc.com") == 5


def test_get_concurrency_returns_default_for_unconfigured_domain() -> None:
    scheduler = DomainScheduler(default_concurrency=2, domain_concurrency={"cnbc.com": 5})

    assert scheduler.get_concurrency("nasdaq.com") == 2


async def test_slot_never_exceeds_configured_concurrency_for_a_domain() -> None:
    scheduler = DomainScheduler(default_concurrency=2, domain_concurrency={"cnbc.com": 2})
    current = 0
    max_seen = 0

    async def worker() -> None:
        nonlocal current, max_seen
        async with scheduler.slot("cnbc.com"):
            current += 1
            max_seen = max(max_seen, current)
            await asyncio.sleep(0.01)
            current -= 1

    await asyncio.gather(*(worker() for _ in range(10)))

    assert max_seen == 2


async def test_slot_gives_each_domain_an_independent_budget() -> None:
    scheduler = DomainScheduler(default_concurrency=1, domain_concurrency={})
    order: list[str] = []

    async def worker(domain: str) -> None:
        async with scheduler.slot(domain):
            order.append(f"start:{domain}")
            await asyncio.sleep(0.02)
            order.append(f"end:{domain}")

    await asyncio.gather(worker("a.com"), worker("b.com"))

    # both start before either ends -> domains ran concurrently, not serialized
    assert order[0].startswith("start")
    assert order[1].startswith("start")
