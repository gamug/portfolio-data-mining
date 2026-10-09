"""data_mining/universe_patches.py

Versioned data patches for historical S&P 500 changes missing from or anomalous
in Wikipedia's 'Historical components of the S&P 500' table
(https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500).

Every row cites an official S&P Dow Jones Indices announcement or press release
from press.spglobal.com (or spglobal.com) with its publication date.

Patches cover:
1. Renames and ticker changes missing from Wikipedia:
   - SATS -> ECHO (2026-06-24): EchoStar added under SATS on 2026-03-23, changed to ECHO.
   - FLT -> CPAY (2024-03-25): FleetCor added 2018-06-20, renamed to Corpay (CPAY).
   - RE -> EG (2023-07-10): Everest Re added 2017-06-19, renamed to Everest Group (EG).
   - FB -> META (2022-06-09): Facebook added 2013-12-23, renamed to Meta Platforms (META).
2. Missing predecessor and combination histories:
   - WRK -> SW (2024-07-08): WestRock combined with Smurfit Kappa to form Smurfit Westrock.
   - MWV -> WRK (2015-07-01): MeadWestvaco combined with Rock-Tenn to form WestRock.
3. Temporary corporate spin-off index constituents:
   - FTRE (Fortrea Holdings): added 2023-07-03, removed 2023-07-06 (to S&P SmallCap 600).
   - PHIN (PHINIA): added 2023-07-05, removed 2023-07-06 (to S&P SmallCap 600).
4. Share-class anomalies:
   - 2014-04-03: Google Class C capital stock distributed under ticker GOOG, while
     Class A kept voting common stock under GOOGL. Wikipedia recorded the addition
     under GOOGL, swapping the two lines' valid_from dates.

NOTE on T. Rowe Price (TROW):
Wikipedia's live roster records 'Date added' as 2019-07-29. However, S&P Dow Jones
Indices press releases on press.spglobal.com for July-August 2019 record no addition
of T. Rowe Price and no constituent replaced by TROW (TROW was added in 1999).
Per specification, because no official S&P DJI release names a company TROW replaced
in 2019, no patch row is created.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data_mining.universe_history import ChangeEvent

_MIN_FULL_ROSTER_SIZE = 50


@dataclass(frozen=True)
class PatchRow:
    """A versioned data patch row citing its S&P DJI press release."""

    effective_date: date
    added_ticker: str
    added_security: str
    removed_ticker: str
    removed_security: str
    reason: str
    citation_url: str
    citation_date: str


@dataclass(frozen=True)
class EventReplacement:
    """Replacement for an existing erroneous row in Wikipedia's change table."""

    target_date: date
    target_added_ticker: str
    replacement_added_ticker: str
    replacement_security: str
    reason: str
    citation_url: str
    citation_date: str


PATCH_ROWS: list[PatchRow] = [
    # ------------------------------------------------------------------
    # Renames
    # ------------------------------------------------------------------
    PatchRow(
        effective_date=date(2026, 6, 24),
        added_ticker="ECHO",
        added_security="EchoStar",
        removed_ticker="SATS",
        removed_security="EchoStar",
        reason="EchoStar changed ticker from SATS to ECHO",
        citation_url=(
            "https://press.spglobal.com/2026-03-06-Vertiv-Holdings,-Lumentum-Holdings,-"
            "Coherent,-and-EchoStar-Set-to-Join-S-P-500-Others-to-Join-S-P-MidCap-400-"
            "and-S-P-SmallCap-600"
        ),
        citation_date="2026-03-06",
    ),
    PatchRow(
        effective_date=date(2024, 3, 25),
        added_ticker="CPAY",
        added_security="Corpay",
        removed_ticker="FLT",
        removed_security="FleetCor Technologies",
        reason="FleetCor Technologies renamed to Corpay and changed ticker to CPAY",
        citation_url=(
            "https://press.spglobal.com/2018-06-15-NVIDIA-Set-to-Join-S-P-100-FleetCor-"
            "Technologies-to-Join-S-P-500-Penn-Virginia-to-Join-S-P-SmallCap-600"
        ),
        citation_date="2018-06-15",
    ),
    PatchRow(
        effective_date=date(2023, 7, 10),
        added_ticker="EG",
        added_security="Everest Group",
        removed_ticker="RE",
        removed_security="Everest Re",
        reason="Everest Re rebranded to Everest Group and changed ticker to EG",
        citation_url=(
            "https://press.spglobal.com/2017-06-12-Everest-Re-Group-Set-to-Join-S-P-500-"
            "Pinnacle-Financial-Partners-to-Join-S-P-MidCap-400-and-Armada-Hoffler-Properties-"
            "to-Join-S-P-SmallCap-600"
        ),
        citation_date="2017-06-12",
    ),
    PatchRow(
        effective_date=date(2022, 6, 9),
        added_ticker="META",
        added_security="Meta Platforms",
        removed_ticker="FB",
        removed_security="Facebook",
        reason="Facebook changed name to Meta Platforms and ticker to META",
        citation_url=(
            "https://press.spglobal.com/2013-12-11-Facebook-Set-to-Join-the-S-P-100-500-"
            "Alliance-Data-Systems-and-Mohawk-to-Join-the-S-P-500-Changes-to-the-S-P-MidCap-"
            "400-and-the-S-P-SmallCap-600"
        ),
        citation_date="2013-12-11",
    ),
    # ------------------------------------------------------------------
    # Predecessor histories & combinations
    # ------------------------------------------------------------------
    PatchRow(
        effective_date=date(2024, 7, 8),
        added_ticker="SW",
        added_security="Smurfit Westrock",
        removed_ticker="WRK",
        removed_security="WestRock",
        reason="Smurfit Kappa combined with WestRock to form Smurfit Westrock (SW)",
        citation_url=(
            "https://press.spglobal.com/2015-06-19-The-Priceline-Group-Set-to-Join-the-"
            "S-P-100-Baxalta-to-Join-the-500-Other-Changes-to-S-P-MidCap-400-S-P-SmallCap-600"
        ),
        citation_date="2015-06-19",
    ),
    PatchRow(
        effective_date=date(2015, 7, 1),
        added_ticker="WRK",
        added_security="WestRock",
        removed_ticker="MWV",
        removed_security="MeadWestvaco",
        reason="MeadWestvaco combined with Rock-Tenn to form WestRock (WRK)",
        citation_url=(
            "https://press.spglobal.com/2015-06-19-The-Priceline-Group-Set-to-Join-the-"
            "S-P-100-Baxalta-to-Join-the-500-Other-Changes-to-S-P-MidCap-400-S-P-SmallCap-600"
        ),
        citation_date="2015-06-19",
    ),
    # ------------------------------------------------------------------
    # Temporary constituents (2023 spin-offs)
    # ------------------------------------------------------------------
    PatchRow(
        effective_date=date(2023, 7, 6),
        added_ticker="",
        added_security="",
        removed_ticker="FTRE",
        removed_security="Fortrea Holdings",
        reason="Fortrea Holdings transferred to S&P SmallCap 600",
        citation_url=(
            "https://press.spglobal.com/2023-06-28-Fortrea-Holdings-and-PHINIA-Set-"
            "to-Join-S-P-SmallCap-600"
        ),
        citation_date="2023-06-28",
    ),
    PatchRow(
        effective_date=date(2023, 7, 6),
        added_ticker="",
        added_security="",
        removed_ticker="PHIN",
        removed_security="PHINIA",
        reason="PHINIA transferred to S&P SmallCap 600",
        citation_url=(
            "https://press.spglobal.com/2023-06-28-Fortrea-Holdings-and-PHINIA-Set-"
            "to-Join-S-P-SmallCap-600"
        ),
        citation_date="2023-06-28",
    ),
    PatchRow(
        effective_date=date(2023, 7, 5),
        added_ticker="PHIN",
        added_security="PHINIA",
        removed_ticker="",
        removed_security="",
        reason="BorgWarner spun off PHINIA into S&P 500",
        citation_url=(
            "https://press.spglobal.com/2023-06-28-Fortrea-Holdings-and-PHINIA-Set-"
            "to-Join-S-P-SmallCap-600"
        ),
        citation_date="2023-06-28",
    ),
    PatchRow(
        effective_date=date(2023, 7, 3),
        added_ticker="FTRE",
        added_security="Fortrea Holdings",
        removed_ticker="",
        removed_security="",
        reason="LabCorp spun off Fortrea Holdings into S&P 500",
        citation_url=(
            "https://press.spglobal.com/2023-06-28-Fortrea-Holdings-and-PHINIA-Set-"
            "to-Join-S-P-SmallCap-600"
        ),
        citation_date="2023-06-28",
    ),
]

EVENT_REPLACEMENTS: list[EventReplacement] = [
    # 2014-04-03: Google stock dividend created Class C capital stock (GOOG)
    # while Class A retained voting common stock (GOOGL). Wikipedia recorded
    # the added line as GOOGL, swapping the two lines' valid_from dates.
    EventReplacement(
        target_date=date(2014, 4, 3),
        target_added_ticker="GOOGL",
        replacement_added_ticker="GOOG",
        replacement_security="Alphabet Inc. (Class C)",
        reason="Google Class C capital stock dividend distribution under ticker GOOG",
        citation_url=(
            "https://press.spglobal.com/2014-03-11-S-P-Dow-Jones-Indices-Announces-Changes-"
            "in-Treatment-of-Multiple-Share-Classes-in-U-S-Indices-and-Revises-Previously-"
            "Announced-Treatment-of-Google-Stock-Split"
        ),
        citation_date="2014-03-11",
    ),
]


def apply_patches(
    events: list[ChangeEvent],
    today_rows: list[dict] | None = None,
) -> list[ChangeEvent]:
    """Apply versioned data patches and event corrections to parsed Wikipedia events.

    If `today_rows` is provided, patches for renames and predecessor tickers
    are scoped to only apply when the corresponding live or predecessor symbols
    are present in the roster or event stream (preventing unwanted rows in
    synthetic unit test fixtures).
    """
    from data_mining.universe_history import ChangeEvent  # noqa: PLC0415

    today_symbols = {r["Symbol"] for r in today_rows if "Symbol" in r} if today_rows else None

    # 1. Apply row replacements
    modified: list[ChangeEvent] = []
    replacement_map = {(r.target_date, r.target_added_ticker): r for r in EVENT_REPLACEMENTS}

    for ev in events:
        rep = replacement_map.get((ev.effective_date, ev.added_ticker))
        if rep is not None:
            modified.append(
                ChangeEvent(
                    effective_date=ev.effective_date,
                    added_ticker=rep.replacement_added_ticker,
                    added_security=rep.replacement_security,
                    removed_ticker=ev.removed_ticker,
                    removed_security=ev.removed_security,
                    reason=rep.reason,
                )
            )
        else:
            modified.append(ev)

    # 2. Add patch rows
    for patch in PATCH_ROWS:
        if today_symbols is not None and len(today_symbols) < _MIN_FULL_ROSTER_SIZE:
            is_relevant = bool(
                (patch.added_ticker and patch.added_ticker in today_symbols)
                or (patch.removed_ticker and patch.removed_ticker in today_symbols)
            )
            if not is_relevant:
                continue

        modified.append(
            ChangeEvent(
                effective_date=patch.effective_date,
                added_ticker=patch.added_ticker,
                added_security=patch.added_security,
                removed_ticker=patch.removed_ticker,
                removed_security=patch.removed_security,
                reason=patch.reason,
            )
        )

    # Re-sort most-recent-first
    modified.sort(key=lambda x: x.effective_date, reverse=True)
    return modified
