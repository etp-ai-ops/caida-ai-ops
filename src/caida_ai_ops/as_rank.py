"""as_rank — CAIDA AS Rank (customer cone) queries for Matthew++.

Implements the queries from the ``nids-asn-introduction-jaber-the-great``
assignment (Task 2: customer-cone tier classification; Task 3: tier
breakdown by country), plus single-ASN lookups for "similar" ad hoc
questions. Datasets are auto-downloaded from CAIDA's **public** mirror
(not the assignment's internal NRP-cluster-only rook-ceph URL, which is
unreachable from outside CAIDA's network) into a local cache directory the
first time they're needed, and reused after that.

Datasets (both from CAIDA's public data server, verified reachable):

- **AS Customer Cone** (CAIDA AS Relationships Serial-1):
  ``https://publicdata.caida.org/datasets/as-relationships/serial-1/{date}.ppdc-ases.txt.bz2``
- **AS-to-Organization mapping**:
  ``https://publicdata.caida.org/datasets/as-organizations/{date}.as-org2info.jsonl.gz``
  This raw file interleaves two record types (``"type": "ASN"`` and
  ``"type": "Organization"``) that this module joins on ``organizationId``
  to reconstruct an asn -> {org_name, country} mapping — the assignment
  notebook instead reads an already-joined ``as2org.jsonl`` from an
  internal endpoint that isn't public, so this is a locally-built
  equivalent, not a byte-identical copy (no ``score``/``date``/``ts``
  fields, since those aren't in the raw public data).

Tier definitions (exact copy of the assignment notebook's `TIERS`/`classify()`):
edge (cone size 1), transit small (2-10), transit middle (11-1000),
transit large (1001-10000), transit huge (10001+).

Two backends, selected automatically like the rest of this toolkit:

- **Live** (default): downloads/caches the real public datasets above.
- **Demo** (``MATTHEWPP_DEMO=1``): a handful of fixed synthetic ASNs, no
  network access.

Output contract: every public function returns
``{"status", "function", "parameters", "data", "warnings", "provenance"}``
(see ``matthewpp_core.envelope``/``Result``).
"""

from __future__ import annotations

import argparse
import bz2
import gzip
import json
import os
import re
import shutil
import ssl
import urllib.request
from pathlib import Path

try:
    import certifi

    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # pragma: no cover - certifi is a near-universal transitive dep
    _SSL_CONTEXT = ssl.create_default_context()

from .core import MatthewPPError, Result, demo_warning, envelope, is_demo_mode, write_output

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AsRankDatasetError(MatthewPPError):
    """A dataset could not be downloaded, or failed a basic sanity check
    after download (too small / too few rows -- likely truncated)."""


class InvalidAsnError(MatthewPPError):
    """An ASN argument was not a positive integer, or wasn't found."""


class UnknownTierError(MatthewPPError):
    """A requested tier label doesn't match one of the five defined tiers."""


class AsRankLimitExceededError(MatthewPPError):
    """A requested result size exceeds this module's hard cap. Not
    overridable -- narrow the request (e.g. a specific tier/country)
    instead."""


# ---------------------------------------------------------------------------
# Tiers -- exact copy of nids-asn-introduction-jaber-the-great's TIERS/classify()
# ---------------------------------------------------------------------------

TIERS: list[tuple[str, int, int | None]] = [
    ("edge", 1, 1),
    ("transit small", 2, 10),
    ("transit middle", 11, 1000),
    ("transit large", 1001, 10000),
    ("transit huge", 10001, None),  # None = no upper bound
]
TIER_LABELS = [t[0] for t in TIERS]


def classify_tier(cone_size: int) -> str:
    """Classify a customer-cone size into one of the five assignment tiers.

    Args:
        cone_size: Number of ASNs in the AS's customer cone (including
            itself), i.e. token count on its `ppdc-ases.txt` line minus 1.

    Returns:
        One of "edge", "transit small", "transit middle", "transit large",
        "transit huge".
    """
    for label, lo, hi in TIERS:
        if hi is None:
            if cone_size >= lo:
                return label
        elif lo <= cone_size <= hi:
            return label
    return "unknown"  # pragma: no cover - unreachable given the TIERS above


# ---------------------------------------------------------------------------
# Hard limits
# ---------------------------------------------------------------------------

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 2_000
DOWNLOAD_TIMEOUT_S = 30
MIN_EXPECTED_CONE_ROWS = 1_000  # sanity floor -- a real snapshot has ~80k
MIN_EXPECTED_ORG_ROWS = 1_000  # sanity floor -- a real snapshot has ~200k total records

DEFAULT_DATASET_DATE = "20260501"  # the date nids-asn-introduction-jaber-the-great pins to
CONE_URL_TEMPLATE = "https://publicdata.caida.org/datasets/as-relationships/serial-1/{date}.ppdc-ases.txt.bz2"
ORG_URL_TEMPLATE = "https://publicdata.caida.org/datasets/as-organizations/{date}.as-org2info.jsonl.gz"

_DATE_RE = re.compile(r"^\d{8}$")


def _dataset_dir() -> Path:
    return Path(os.environ.get("AS_RANK_DATASET_DIR", "datasets/as-rank")).expanduser()


def _validate_dataset_date(dataset_date: str) -> None:
    if not _DATE_RE.match(dataset_date):
        raise AsRankDatasetError(f"dataset_date must be 8 digits (YYYYMMDD), got {dataset_date!r}")


def _cone_path(dataset_date: str) -> Path:
    return _dataset_dir() / f"{dataset_date}.ppdc-ases.txt.bz2"


def _org_path(dataset_date: str) -> Path:
    return _dataset_dir() / f"{dataset_date}.as-org2info.jsonl.gz"


def _download(url: str, path: Path, min_bytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        # Explicit certifi-backed SSL context + timeout: some Python installs (notably
        # python.org's macOS build without "Install Certificates.command" run) ship an
        # empty default CA bundle, which fails real, validly-signed HTTPS downloads.
        with (
            urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S, context=_SSL_CONTEXT) as resp,
            tmp.open("wb") as out,
        ):  # noqa: S310 - fixed https CAIDA public host, not user input
            shutil.copyfileobj(resp, out)
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise AsRankDatasetError(f"failed to download {url}: {exc}") from exc
    if not tmp.exists() or tmp.stat().st_size < min_bytes:
        size = tmp.stat().st_size if tmp.exists() else 0
        tmp.unlink(missing_ok=True)
        raise AsRankDatasetError(
            f"downloaded file from {url} is suspiciously small ({size} bytes) -- aborting"
        )
    tmp.rename(path)


# ---------------------------------------------------------------------------
# Demo fixtures (used only when MATTHEWPP_DEMO is truthy)
# ---------------------------------------------------------------------------

_DEMO_CONE_SIZES: dict[str, int] = {
    "1": 1,
    "13335": 1,  # edge
    "2914": 5,  # transit small
    "2906": 200,  # transit middle
    "15169": 5000,  # transit large
    "3356": 60000,
    "4837": 15000,  # transit huge
}
_DEMO_ORG: dict[str, dict] = {
    "1": {"org_id": "LVLT-1-ARIN", "org_name": "Level 3 Parent, LLC", "country": "US"},
    "13335": {"org_id": "CLOUD14-ARIN", "org_name": "Cloudflare, Inc.", "country": "US"},
    "2914": {"org_id": "NTTAM-ARIN", "org_name": "NTT America, Inc.", "country": "JP"},
    "2906": {"org_id": "NFLX-ARIN", "org_name": "Netflix, Inc.", "country": "US"},
    "15169": {"org_id": "GOGL-ARIN", "org_name": "Google LLC", "country": "US"},
    "3356": {"org_id": "LPL-141-ARIN", "org_name": "Level 3 Parent, LLC", "country": "US"},
    "4837": {"org_id": "CNNIC-CU", "org_name": "China Unicom", "country": "CN"},
}


def _demo_warning() -> list[str]:
    return demo_warning("AS Rank")


# ---------------------------------------------------------------------------
# Dataset loading (download-if-missing + in-process cache)
# ---------------------------------------------------------------------------

_CONE_CACHE: dict[str, dict[str, int]] = {}
_ORG_CACHE: dict[str, dict[str, dict]] = {}  # dataset_date -> {asn: {org_id, org_name, country}}


def _load_cone(dataset_date: str) -> dict[str, int]:
    if is_demo_mode():
        return _DEMO_CONE_SIZES
    if dataset_date in _CONE_CACHE:
        return _CONE_CACHE[dataset_date]
    _validate_dataset_date(dataset_date)
    path = _cone_path(dataset_date)
    if not path.exists():
        _download(CONE_URL_TEMPLATE.format(date=dataset_date), path, min_bytes=10_000)

    sizes: dict[str, int] = {}
    with bz2.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            sizes[tokens[0]] = len(tokens) - 1
    if len(sizes) < MIN_EXPECTED_CONE_ROWS:
        raise AsRankDatasetError(
            f"parsed only {len(sizes)} ASNs from {path} -- expected at least "
            f"{MIN_EXPECTED_CONE_ROWS}; the download may be truncated/corrupt"
        )
    _CONE_CACHE[dataset_date] = sizes
    return sizes


def _load_org(dataset_date: str) -> dict[str, dict]:
    if is_demo_mode():
        return _DEMO_ORG
    if dataset_date in _ORG_CACHE:
        return _ORG_CACHE[dataset_date]
    _validate_dataset_date(dataset_date)
    path = _org_path(dataset_date)
    if not path.exists():
        _download(ORG_URL_TEMPLATE.format(date=dataset_date), path, min_bytes=10_000)

    orgs: dict[str, dict] = {}  # org_id -> {org_name, country}
    asn_org_id: dict[str, str] = {}  # asn -> org_id
    total_records = 0
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total_records += 1
            record = json.loads(line)
            if record.get("type") == "Organization":
                orgs[record["organizationId"]] = {
                    "org_name": record.get("name"),
                    "country": record.get("country"),
                }
            elif record.get("type") == "ASN":
                asn_org_id[record["asn"]] = record["organizationId"]
    if total_records < MIN_EXPECTED_ORG_ROWS:
        raise AsRankDatasetError(
            f"parsed only {total_records} records from {path} -- expected at least "
            f"{MIN_EXPECTED_ORG_ROWS}; the download may be truncated/corrupt"
        )

    result: dict[str, dict] = {}
    for asn, org_id in asn_org_id.items():
        org = orgs.get(org_id, {})
        result[asn] = {"org_id": org_id, "org_name": org.get("org_name"), "country": org.get("country")}
    _ORG_CACHE[dataset_date] = result
    return result


def _validate_asn(asn: int) -> str:
    if not isinstance(asn, int) or asn <= 0:
        raise InvalidAsnError(f"asn must be a positive integer, got {asn!r}")
    return str(asn)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


@envelope("matthewpp.asrank.download_as_rank_datasets")
def download_as_rank_datasets(dataset_date: str = DEFAULT_DATASET_DATE, force: bool = False) -> Result:
    """Download (or re-use a cached copy of) both AS Rank datasets.

    Called automatically by every other function in this module on first
    use, so calling this directly is optional — useful mainly to pre-warm
    the cache or force a refresh.

    Args:
        dataset_date: 8-digit CAIDA snapshot date (YYYYMMDD). Default
            "20260501", matching the pinned date in
            nids-asn-introduction-jaber-the-great's own notebook. CAIDA
            publishes a new snapshot roughly monthly (the 1st of each
            month) at both source URLs.
        force: If True, re-download even if a cached copy already exists.

    Returns:
        ``data`` is ``{"cone_path", "cone_asn_count", "org_path",
        "org_asn_count"}``.

    Raises:
        AsRankDatasetError: `dataset_date` is malformed, the download
            failed, or a downloaded file failed its row-count sanity check.
    """
    if is_demo_mode():
        return Result(
            data={
                "cone_path": None,
                "cone_asn_count": len(_DEMO_CONE_SIZES),
                "org_path": None,
                "org_asn_count": len(_DEMO_ORG),
            },
            warnings=_demo_warning(),
        )
    if force:
        _CONE_CACHE.pop(dataset_date, None)
        _ORG_CACHE.pop(dataset_date, None)
        _cone_path(dataset_date).unlink(missing_ok=True)
        _org_path(dataset_date).unlink(missing_ok=True)
    cone = _load_cone(dataset_date)
    org = _load_org(dataset_date)
    return Result(
        data={
            "cone_path": str(_cone_path(dataset_date)),
            "cone_asn_count": len(cone),
            "org_path": str(_org_path(dataset_date)),
            "org_asn_count": len(org),
        }
    )


@envelope("matthewpp.asrank.get_customer_cone")
def get_customer_cone(asn: int, dataset_date: str = DEFAULT_DATASET_DATE) -> Result:
    """Look up one AS's customer-cone size and tier.

    Args:
        asn: The AS number to look up.
        dataset_date: See `download_as_rank_datasets`.

    Returns:
        ``data`` is ``{"asn", "cone_size", "tier"}``.

    Raises:
        InvalidAsnError: `asn` isn't a positive integer, or has no entry
            in the customer-cone dataset (e.g. it announces no routes).
        AsRankDatasetError: dataset download/parse failed.
    """
    asn_s = _validate_asn(asn)
    sizes = _load_cone(dataset_date)
    if asn_s not in sizes:
        raise InvalidAsnError(f"AS{asn} has no entry in the {dataset_date} customer-cone dataset")
    size = sizes[asn_s]
    warnings = _demo_warning() if is_demo_mode() else []
    return Result(data={"asn": asn, "cone_size": size, "tier": classify_tier(size)}, warnings=warnings)


@envelope("matthewpp.asrank.resolve_asn_org")
def resolve_asn_org(asn: int, dataset_date: str = DEFAULT_DATASET_DATE) -> Result:
    """Look up the organization and country CAIDA's AS2Org mapping
    associates with an AS.

    Args:
        asn: The AS number to look up.
        dataset_date: See `download_as_rank_datasets`.

    Returns:
        ``data`` is ``{"asn", "org_id", "org_name", "country"}`` (fields
        are `None` if the ASN has no organization mapping on file).

    Raises:
        InvalidAsnError: `asn` isn't a positive integer.
        AsRankDatasetError: dataset download/parse failed.
    """
    asn_s = _validate_asn(asn)
    org_map = _load_org(dataset_date)
    org = org_map.get(asn_s, {"org_id": None, "org_name": None, "country": None})
    warnings = _demo_warning() if is_demo_mode() else []
    return Result(data={"asn": asn, **org}, warnings=warnings)


@envelope("matthewpp.asrank.cone_tier_distribution")
def cone_tier_distribution(dataset_date: str = DEFAULT_DATASET_DATE) -> Result:
    """Classify every ASN into a tier and report the distribution — the
    assignment's Task 2 "Table 1", answering Q1 (% edge), Q2 (max cone
    size), and Q3 (tier proportions) directly.

    Args:
        dataset_date: See `download_as_rank_datasets`.

    Returns:
        ``data`` is ``{"total_asns", "max_cone_size", "tiers": [{"tier",
        "range", "count", "pct"}, ...]}`` (tiers in the fixed order from
        `TIERS`).

    Raises:
        AsRankDatasetError: dataset download/parse failed.
    """
    sizes = _load_cone(dataset_date)
    total = len(sizes)
    max_size = max(sizes.values()) if sizes else 0
    counts = {label: 0 for label in TIER_LABELS}
    for size in sizes.values():
        counts[classify_tier(size)] += 1

    rows = []
    for label, lo, hi in TIERS:
        if hi is None:
            range_str = f"{lo}..{max_size}"
        elif lo == hi:
            range_str = f"{lo}"
        else:
            range_str = f"{lo}..{hi}"
        count = counts[label]
        rows.append(
            {
                "tier": label,
                "range": range_str,
                "count": count,
                "pct": round(100.0 * count / total, 1) if total else 0.0,
            }
        )

    warnings = _demo_warning() if is_demo_mode() else []
    return Result(data={"total_asns": total, "max_cone_size": max_size, "tiers": rows}, warnings=warnings)


@envelope("matthewpp.asrank.tier_country_breakdown")
def tier_country_breakdown(dataset_date: str = DEFAULT_DATASET_DATE, top_n_countries: int = 4) -> Result:
    """Break down each tier's ASN count by country — the assignment's
    Task 3 "Table 2", answering Q4 (which countries dominate transit-huge),
    Q5 (share in "other"), and Q6 (do the same countries dominate every
    tier) directly.

    Args:
        dataset_date: See `download_as_rank_datasets`.
        top_n_countries: How many top countries (by total ASN count across
            all tiers) get their own column; every other country's count
            is folded into "other". Default 4, matching the assignment.

    Returns:
        ``data`` is ``{"top_countries": [cc, ...], "rows": [{"tier",
        "<cc>": {"count", "pct"}, ..., "other": {"count", "pct"}}, ...]}``.

    Raises:
        AsRankLimitExceededError: `top_n_countries` > 20.
        AsRankDatasetError: dataset download/parse failed.
    """
    if top_n_countries > 20:
        raise AsRankLimitExceededError(f"top_n_countries must be <= 20 (got {top_n_countries})")

    sizes = _load_cone(dataset_date)
    org_map = _load_org(dataset_date)

    tier_country_counts: dict[str, dict[str, int]] = {label: {} for label in TIER_LABELS}
    country_totals: dict[str, int] = {}
    for asn, size in sizes.items():
        tier = classify_tier(size)
        country = org_map.get(asn, {}).get("country") or "unknown"
        tier_country_counts[tier][country] = tier_country_counts[tier].get(country, 0) + 1
        country_totals[country] = country_totals.get(country, 0) + 1

    top_countries = [c for c, _ in sorted(country_totals.items(), key=lambda kv: -kv[1])[:top_n_countries]]

    rows = []
    for label in TIER_LABELS:
        tier_total = sum(tier_country_counts[label].values())
        row = {"tier": label}
        other = 0
        for cc in top_countries:
            n = tier_country_counts[label].get(cc, 0)
            row[cc] = {"count": n, "pct": round(100.0 * n / tier_total, 1) if tier_total else 0.0}
        for cc, n in tier_country_counts[label].items():
            if cc not in top_countries:
                other += n
        row["other"] = {"count": other, "pct": round(100.0 * other / tier_total, 1) if tier_total else 0.0}
        rows.append(row)

    warnings = _demo_warning() if is_demo_mode() else []
    return Result(data={"top_countries": top_countries, "rows": rows}, warnings=warnings)


@envelope("matthewpp.asrank.list_asns_by_tier")
def list_asns_by_tier(
    tier: str, dataset_date: str = DEFAULT_DATASET_DATE, limit: int = DEFAULT_LIST_LIMIT
) -> Result:
    """List ASNs in a given tier, largest cone first.

    Args:
        tier: One of "edge", "transit small", "transit middle",
            "transit large", "transit huge".
        dataset_date: See `download_as_rank_datasets`.
        limit: Max ASNs to return (largest-cone-first). Default 50, hard
            max 2000 — the "edge" tier alone typically has tens of
            thousands of members, so this is never unbounded.

    Returns:
        ``data`` is ``{"tier", "total_matching", "asns": [{"asn",
        "cone_size"}, ...]}`` — ``total_matching`` is the true count even
        when truncated by `limit`.

    Raises:
        UnknownTierError: `tier` isn't one of the five defined tiers.
        AsRankLimitExceededError: `limit` > 2000.
        AsRankDatasetError: dataset download/parse failed.
    """
    if tier not in TIER_LABELS:
        raise UnknownTierError(f"unknown tier {tier!r}; must be one of {TIER_LABELS}")
    if limit > MAX_LIST_LIMIT:
        raise AsRankLimitExceededError(f"limit must be <= {MAX_LIST_LIMIT} (got {limit})")

    sizes = _load_cone(dataset_date)
    matches = [(asn, size) for asn, size in sizes.items() if classify_tier(size) == tier]
    matches.sort(key=lambda kv: -kv[1])
    warnings = _demo_warning() if is_demo_mode() else []
    data = {
        "tier": tier,
        "total_matching": len(matches),
        "asns": [{"asn": int(a), "cone_size": s} for a, s in matches[:limit]],
    }
    return Result(data=data, warnings=warnings)


@envelope("matthewpp.asrank.largest_customer_cones")
def largest_customer_cones(n: int = 10, dataset_date: str = DEFAULT_DATASET_DATE) -> Result:
    """The N ASes with the largest customer cones, with org/country
    attached for convenience (answers "which ASes are most influential?"
    without a separate `resolve_asn_org` call per result).

    Args:
        n: Number of top ASes to return. Default 10, hard max 2000.
        dataset_date: See `download_as_rank_datasets`.

    Returns:
        ``data`` is a list of ``{"asn", "cone_size", "tier", "org_name",
        "country"}``, largest cone first.

    Raises:
        AsRankLimitExceededError: `n` > 2000.
        AsRankDatasetError: dataset download/parse failed.
    """
    if n > MAX_LIST_LIMIT:
        raise AsRankLimitExceededError(f"n must be <= {MAX_LIST_LIMIT} (got {n})")

    sizes = _load_cone(dataset_date)
    org_map = _load_org(dataset_date)
    ordered = sorted(sizes.items(), key=lambda kv: -kv[1])[:n]
    warnings = _demo_warning() if is_demo_mode() else []
    data = [
        {
            "asn": int(asn),
            "cone_size": size,
            "tier": classify_tier(size),
            "org_name": org_map.get(asn, {}).get("org_name"),
            "country": org_map.get(asn, {}).get("country"),
        }
        for asn, size in ordered
    ]
    return Result(data=data, warnings=warnings)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="as_rank", description=__doc__)
    parser.add_argument("--format", choices=["json", "csv", "md"], default="json")
    parser.add_argument("--output-file", default=None)
    parser.add_argument("--dataset-date", default=DEFAULT_DATASET_DATE)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("download", help="download/cache the AS Rank datasets")

    p = sub.add_parser("cone", help="look up one AS's customer-cone size and tier")
    p.add_argument("--asn", type=int, required=True)

    p = sub.add_parser("resolve-org", help="look up one AS's organization/country")
    p.add_argument("--asn", type=int, required=True)

    sub.add_parser("tier-distribution", help="Task 2: tier distribution table (Q1-Q3)")

    p = sub.add_parser("country-breakdown", help="Task 3: tier x country breakdown (Q4-Q6)")
    p.add_argument("--top-n-countries", type=int, default=4)

    p = sub.add_parser("list-by-tier", help="list ASNs in a tier")
    p.add_argument("--tier", required=True, choices=TIER_LABELS)
    p.add_argument("--limit", type=int, default=DEFAULT_LIST_LIMIT)

    p = sub.add_parser("top-cones", help="largest N customer cones")
    p.add_argument("-n", type=int, default=10)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "download":
        resp = download_as_rank_datasets(args.dataset_date)
    elif args.command == "cone":
        resp = get_customer_cone(args.asn, dataset_date=args.dataset_date)
    elif args.command == "resolve-org":
        resp = resolve_asn_org(args.asn, dataset_date=args.dataset_date)
    elif args.command == "tier-distribution":
        resp = cone_tier_distribution(dataset_date=args.dataset_date)
    elif args.command == "country-breakdown":
        resp = tier_country_breakdown(dataset_date=args.dataset_date, top_n_countries=args.top_n_countries)
    elif args.command == "list-by-tier":
        resp = list_asns_by_tier(args.tier, dataset_date=args.dataset_date, limit=args.limit)
    elif args.command == "top-cones":
        resp = largest_customer_cones(args.n, dataset_date=args.dataset_date)
    else:  # pragma: no cover
        parser.error(f"unknown command: {args.command}")
        return 2

    rendered = write_output(resp, path=args.output_file, output_format=args.format)
    if args.output_file is None:
        print(rendered)
    return 0 if resp["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
