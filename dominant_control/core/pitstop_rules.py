"""Which pitstop regulations the session is running, for the car being driven.

iRacing publishes the active ruleset in ``WeekendInfo:AltAssetTag``.  The tag
alone is not the whole answer: what the driver actually needs to know — whether
fuel and tires are serviced at the same time, when an ARB or wing change is
applied, and how long a full tank and a set of tires take — depends on the car
as much as on the ruleset, and several cars are not covered by a ruleset at all
and quietly fall back to the global rules.

This module answers that question and nothing else: no Tk, no SDK, no state.
``describe`` takes the tag and whatever the session says about the car and
returns the lines the HUD shows.
"""

from __future__ import annotations

from dataclasses import dataclass


# --------------------------------------------------------------------------
# Regulamentos
# --------------------------------------------------------------------------
DEFAULT = "DEFAULT"
IMSA = "IMSA"
NEC = "NEC"
DTM = "DTM"

# The tags iRacing publishes.  The ones with no ruleset of their own
# fall back to the global rules, like the empty tag.
TAG_RULESETS: dict[str, str] = {
    "": DEFAULT,
    "IMSA": IMSA,
    "NEC": NEC,
    "DTM": DTM,
    "INDY": DEFAULT,
    "NOABS": DEFAULT,
    "NOABSTC": DEFAULT,
    "NOTC": DEFAULT,
    "PMNA": DEFAULT,
    "ROOKIE": DEFAULT,
    "WSC2017": DEFAULT,
}

RULESET_LABELS: dict[str, str] = {
    DEFAULT: "Global",
    IMSA: "IMSA",
    NEC: "NEC",
    DTM: "DTM",
}

# --------------------------------------------------------------------------
# Service order and adjustments
# --------------------------------------------------------------------------
SEQUENTIAL = "sequential"
SIMULTANEOUS = "simultaneous"

ORDER_LABELS: dict[str, str] = {
    SEQUENTIAL: "Fuel first, then tires",
    SIMULTANEOUS: "Fuel and tires at the same time",
}

NO_ADJUSTMENT = "none"
ARB_WING_AFTER_FUEL = "arb_wing_after_fuel"
ARB_WING_DURING = "arb_wing_during"
ARB_AFTER_FUEL = "arb_after_fuel"
ARB_DURING = "arb_during"
BODYWORK_AFTER_TIRES = "bodywork_after_tires"

ADJUSTMENT_LABELS: dict[str, str] = {
    NO_ADJUSTMENT: "",
    ARB_WING_AFTER_FUEL: "ARB and wing after fuel, during the tires",
    ARB_WING_DURING: "ARB and wing during fuel and tires",
    ARB_AFTER_FUEL: "ARB after fuel, during the tires",
    ARB_DURING: "ARB during fuel and tires",
    BODYWORK_AFTER_TIRES: "Bodywork after the tire service ends",
}

# Under the global ruleset repairs only start once refuelling ends.
DEFAULT_REPAIR_NOTE = "Repairs only after refuelling"
# Cars that follow their own category's real-world regulations.
REAL_WORLD_NOTE = "Follows the category's real-world rules"
DIRT_NOTE = "No pitstop on dirt tracks"
FALLBACK_NOTE = "This car is not covered by the ruleset; the global rules apply"
UNLISTED_NOTE = "Car with no rules of its own in the table; the global rules apply"


@dataclass(frozen=True)
class Service:
    """How one car is serviced under one ruleset."""

    order: str
    adjustments: str
    fuel_s: int
    tires_s: int


@dataclass(frozen=True)
class CarRules:
    """One row of the regulation table, with the cars it applies to."""

    key: str
    label: str
    patterns: tuple[str, ...]
    default: Service
    imsa: Service
    nec: Service | None = None
    dtm: Service | None = None


def _service(order: str, adjustments: str, fuel_s: int, tires_s: int) -> Service:
    return Service(order=order, adjustments=adjustments, fuel_s=fuel_s, tires_s=tires_s)


# Times and rules come from the regulation table iRacing publishes for 26S4.
# The order matters: the first row whose pattern matches wins, so the specific
# cars come before the classes that would also match them.
CAR_RULES: tuple[CarRules, ...] = (
    CarRules(
        key="gtp",
        label="GTP / Hypercar",
        patterns=("gtp", "hypercar", "lmdh", "963", "arx06", "vseriesrgt", "499p"),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 40, 20),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 40, 20),
    ),
    CarRules(
        key="hpd_arx",
        label="HPD ARX-01C (LMP2)",
        patterns=("arx01", "hpdarx"),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 30, 20),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 30, 20),
    ),
    CarRules(
        key="lmp2",
        label="Dallara P217 (LMP2)",
        patterns=("p217", "lmp2"),
        default=_service(SEQUENTIAL, BODYWORK_AFTER_TIRES, 40, 20),
        imsa=_service(SIMULTANEOUS, BODYWORK_AFTER_TIRES, 40, 20),
    ),
    CarRules(
        key="lmp3",
        label="Ligier JS P320 (LMP3)",
        patterns=("jsp320", "lmp3"),
        default=_service(SEQUENTIAL, BODYWORK_AFTER_TIRES, 40, 20),
        imsa=_service(SIMULTANEOUS, BODYWORK_AFTER_TIRES, 40, 20),
    ),
    CarRules(
        key="porsche_cup",
        label="Porsche 911 Cup",
        patterns=("911cup", "992cup", "991cup", "porschecup", "gt3cup"),
        default=_service(SEQUENTIAL, ARB_WING_AFTER_FUEL, 44, 20),
        imsa=_service(SIMULTANEOUS, ARB_WING_DURING, 44, 20),
        nec=_service(SIMULTANEOUS, ARB_WING_DURING, 120, 20),
    ),
    CarRules(
        key="gt3",
        label="GT3",
        patterns=("gt3",),
        default=_service(SEQUENTIAL, ARB_WING_AFTER_FUEL, 40, 20),
        imsa=_service(SIMULTANEOUS, ARB_WING_DURING, 40, 20),
        nec=_service(SIMULTANEOUS, ARB_WING_DURING, 120, 20),
        dtm=_service(SIMULTANEOUS, ARB_WING_DURING, 40, 7),
    ),
    CarRules(
        key="ruf_cspec",
        label="Ruf RT 12 R C-Spec",
        patterns=("rufcspec", "rt12rcspec", "cspec"),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 44, 20),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 44, 20),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 20),
    ),
    CarRules(
        key="ferrari296challenge",
        label="Ferrari 296 Challenge",
        patterns=("296challenge",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 53, 20),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 53, 20),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 20),
    ),
    CarRules(
        key="cadillac_ctsv",
        label="Cadillac CTS-V",
        patterns=("ctsv",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 48, 35),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 48, 35),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 35),
    ),
    CarRules(
        key="kia_optima",
        label="Kia Optima",
        patterns=("kiaoptima", "optima"),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 48, 36),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 48, 36),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 36),
    ),
    CarRules(
        key="gt4",
        label="GT4",
        patterns=("gt4",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 48, 35),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 48, 35),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 35),
    ),
    CarRules(
        key="tcr",
        label="TCR",
        patterns=("tcr",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 52, 40),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 52, 40),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 40),
    ),
    CarRules(
        key="bmw_m2",
        label="BMW M2",
        patterns=("bmwm2", "m2f87", "m2g87"),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 60, 40),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 60, 40),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 40),
    ),
    CarRules(
        key="gr86",
        label="Toyota GR86",
        patterns=("gr86",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 60, 40),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 60, 40),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 40),
    ),
    CarRules(
        key="mx5",
        label="Mazda MX-5",
        patterns=("mx5",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 60, 40),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 60, 40),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 40),
    ),
    CarRules(
        key="clio",
        label="Renault Clio",
        patterns=("clio",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 60, 40),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 60, 40),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 40),
    ),
    CarRules(
        key="mustang_fr500s",
        label="Ford Mustang FR500S",
        patterns=("fr500s",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 60, 40),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 60, 40),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 40),
    ),
    CarRules(
        key="solstice",
        label="Pontiac Solstice",
        patterns=("solstice",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 60, 40),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 60, 40),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 40),
    ),
    CarRules(
        key="jetta",
        label="VW Jetta TDI Cup",
        patterns=("jetta",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 60, 40),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 60, 40),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 40),
    ),
    CarRules(
        key="ruf_rt12r",
        label="Ruf RT 12 R",
        patterns=("rufrt12r", "rt12r", "ruf"),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 60, 40),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 60, 40),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 40),
    ),
    CarRules(
        key="caterham",
        label="Caterham",
        patterns=("caterham",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 60, 40),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 60, 40),
        nec=_service(SIMULTANEOUS, NO_ADJUSTMENT, 120, 40),
    ),
    CarRules(
        key="gte",
        label="GTE",
        patterns=("gte", "rsr", "488gt", "c8r", "corvettec8"),
        default=_service(SEQUENTIAL, ARB_AFTER_FUEL, 34, 20),
        imsa=_service(SIMULTANEOUS, ARB_DURING, 34, 20),
    ),
    CarRules(
        key="lmp1",
        label="LMP1",
        patterns=("lmp1", "audir18", "porsche919"),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 30, 20),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 30, 20),
    ),
    CarRules(
        key="ford_gt2",
        label="Ford GT (GT2)",
        patterns=("fordgt",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 34, 20),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 34, 20),
    ),
    CarRules(
        key="gt1",
        label="GT1",
        patterns=("gt1", "c6r"),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 34, 20),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 34, 20),
    ),
    CarRules(
        key="daytona_prototype",
        label="Daytona Prototype",
        patterns=("daytonaprototype", "rileydp", "dallaradp"),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 30, 20),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 30, 20),
    ),
    CarRules(
        key="nissan_gtp",
        label="Nissan GTP / Audi 90 GTO",
        patterns=("nissangtp", "audi90", "gto"),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 45, 15),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 45, 15),
    ),
    CarRules(
        key="radical",
        label="Radical SR8 / SR10",
        patterns=("radical", "sr8", "sr10"),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 40, 20),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 40, 20),
    ),
    CarRules(
        key="spec_racer_ford",
        label="Spec Racer Ford",
        patterns=("specracer", "srf",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 36, 40),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 36, 40),
    ),
    CarRules(
        key="mission_r",
        label="Porsche Mission R",
        patterns=("missionr",),
        default=_service(SEQUENTIAL, NO_ADJUSTMENT, 820, 20),
        imsa=_service(SIMULTANEOUS, NO_ADJUSTMENT, 820, 20),
    ),
)

# Cars that race by their category's real-world rules, with no
# alternative ruleset, and dirt cars, which have no pitstop on dirt.
REAL_WORLD_PATTERNS = (
    "stockcar", "nascar", "supercar", "sprintcup", "xfinity", "trucks",
    "gander", "formula", "skipbarber", "indycar", "dallarair", "f4", "f3",
    "fr20", "super formula", "superformula", "usf",
)
DIRT_PATTERNS = ("dirt", "sprintcar", "midget", "streetstock", "limitedlatemodel")


@dataclass(frozen=True)
class PitstopRules:
    """What the HUD shows: the ruleset, the car it applies to, and how."""

    ruleset: str
    ruleset_label: str
    car_label: str
    service: Service | None
    notes: tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        return self.service is not None


def normalize_tag(raw: object) -> str:
    """Read ``WeekendInfo:AltAssetTag`` the way the SDK may write it."""

    text = str(raw or "").strip().upper()
    return text if text in TAG_RULESETS else ""


def ruleset_for_tag(raw: object) -> str:
    return TAG_RULESETS.get(normalize_tag(raw), DEFAULT)


def _haystack(*values: object) -> str:
    joined = " ".join(str(value or "") for value in values).lower()
    return "".join(char for char in joined if char.isalnum() or char == " ")


def find_car_rules(*car_identity: object) -> CarRules | None:
    """Match the car by its path, screen name and class, in that spirit."""

    haystack = _haystack(*car_identity)
    if not haystack.strip():
        return None
    squashed = haystack.replace(" ", "")
    for rules in CAR_RULES:
        for pattern in rules.patterns:
            if pattern in squashed:
                return rules
    return None


def _service_for(rules: CarRules, ruleset: str) -> tuple[Service, bool]:
    """Return the service for this ruleset, and whether it fell back."""

    if ruleset == IMSA:
        return rules.imsa, False
    if ruleset == NEC:
        return (rules.nec, False) if rules.nec else (rules.default, True)
    if ruleset == DTM:
        return (rules.dtm, False) if rules.dtm else (rules.default, True)
    return rules.default, False


def describe(tag: object, *car_identity: object) -> PitstopRules:
    """Say which rules are running, for this session and this car."""

    ruleset = ruleset_for_tag(tag)
    label = RULESET_LABELS[ruleset]
    haystack = _haystack(*car_identity).replace(" ", "")

    notes: list[str] = []
    rules = find_car_rules(*car_identity)
    if rules is None:
        if any(pattern in haystack for pattern in DIRT_PATTERNS):
            notes.append(DIRT_NOTE)
        elif any(pattern.replace(" ", "") in haystack for pattern in REAL_WORLD_PATTERNS):
            notes.append(REAL_WORLD_NOTE)
        elif haystack:
            notes.append(UNLISTED_NOTE)
        if ruleset == DEFAULT and haystack:
            notes.append(DEFAULT_REPAIR_NOTE)
        return PitstopRules(
            ruleset=ruleset,
            ruleset_label=label,
            car_label="",
            service=None,
            notes=tuple(notes),
        )

    service, fell_back = _service_for(rules, ruleset)
    if fell_back:
        notes.append(FALLBACK_NOTE)
    if ruleset == DEFAULT or fell_back:
        notes.append(DEFAULT_REPAIR_NOTE)
    return PitstopRules(
        ruleset=ruleset,
        ruleset_label=label,
        car_label=rules.label,
        service=service,
        notes=tuple(notes),
    )


def headline(rules: PitstopRules) -> str:
    """First HUD line: the ruleset and the car it is being read for."""

    if rules.car_label:
        return f"{rules.ruleset_label} • {rules.car_label}"
    return rules.ruleset_label


def detail(rules: PitstopRules) -> str:
    """Second HUD line: how this car is serviced under these rules."""

    parts: list[str] = []
    service = rules.service
    if service is not None:
        parts.append(ORDER_LABELS[service.order])
        adjustment = ADJUSTMENT_LABELS[service.adjustments]
        if adjustment:
            parts.append(adjustment)
        parts.append(f'tank {service.fuel_s} s')
        parts.append(f'tires {service.tires_s} s')
    parts.extend(rules.notes)
    return "  •  ".join(parts)


__all__ = [
    "CAR_RULES",
    "DEFAULT",
    "DTM",
    "IMSA",
    "NEC",
    "CarRules",
    "PitstopRules",
    "Service",
    "describe",
    "detail",
    "find_car_rules",
    "headline",
    "normalize_tag",
    "ruleset_for_tag",
]
