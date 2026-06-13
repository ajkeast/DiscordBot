"""Collect expected/actual values for CI job summaries."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

SECTION_UNIT = "Unit tests"
SECTION_COMMANDS = "Command handlers"
SECTION_LIVE_XAI = "Live API (xAI)"
SECTION_LIVE_DB = "Live database"
SECTION_WIRING = "Bot wiring"

DEFAULT_REPORT_PATH = "ci-test-report.md"
MAX_CELL_LENGTH = 400

_collector: TestReportCollector | None = None


@dataclass
class ReportRow:
    test: str
    section: str
    field: str
    expected: str
    actual: str


@dataclass
class TestReportCollector:
    rows: list[ReportRow] = field(default_factory=list)

    def add(
        self,
        test: str,
        section: str,
        field: str,
        expected,
        actual,
    ) -> None:
        self.rows.append(
            ReportRow(
                test=test,
                section=section,
                field=field,
                expected=_stringify(expected),
                actual=_stringify(actual),
            )
        )

    def to_markdown(self) -> str:
        if not self.rows:
            return "## Test details\n\nNo detailed results were recorded.\n"

        lines = ["## Test details", ""]
        by_section: dict[str, list[ReportRow]] = {}
        for row in self.rows:
            by_section.setdefault(row.section, []).append(row)

        for section in (
            SECTION_WIRING,
            SECTION_UNIT,
            SECTION_COMMANDS,
            SECTION_LIVE_XAI,
            SECTION_LIVE_DB,
        ):
            rows = by_section.pop(section, [])
            if not rows:
                continue
            lines.extend(_section_table(section, rows))

        for section, rows in sorted(by_section.items()):
            lines.extend(_section_table(section, rows))

        return "\n".join(lines).rstrip() + "\n"


def get_collector() -> TestReportCollector:
    global _collector
    if _collector is None:
        _collector = TestReportCollector()
    return _collector


def reset_collector() -> None:
    global _collector
    _collector = TestReportCollector()


def write_report(path: str | None = None) -> str:
    path = path or os.environ.get("CI_TEST_REPORT", DEFAULT_REPORT_PATH)
    content = get_collector().to_markdown()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def assert_eq(report, section: str, field: str, expected, actual) -> None:
    """Record expected/actual and assert equality."""
    report.record(field, expected, actual, section=section)
    assert expected == actual


def _section_table(section: str, rows: list[ReportRow]) -> list[str]:
    lines = [
        f"### {section}",
        "",
        "| Test | Field | Expected | Actual |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| `{test}` | {field} | {expected} | {actual} |".format(
                test=_escape_cell(row.test),
                field=_escape_cell(row.field),
                expected=_escape_cell(_truncate(row.expected)),
                actual=_escape_cell(_truncate(row.actual)),
            )
        )
    lines.append("")
    return lines


def _stringify(value) -> str:
    if value is None:
        return "None"
    if isinstance(value, bytes):
        return f"{len(value)} bytes"
    return str(value)


def _truncate(value: str) -> str:
    compact = " ".join(value.split())
    if len(compact) <= MAX_CELL_LENGTH:
        return compact
    return compact[: MAX_CELL_LENGTH - 3] + "..."


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
