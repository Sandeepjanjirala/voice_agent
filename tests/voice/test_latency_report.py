import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "latency_report.py"
spec = importlib.util.spec_from_file_location("latency_report", SCRIPT)
latency_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(latency_report)


def test_summary_uses_only_rows_that_carry_a_stage() -> None:
    rows = [
        {"role": "user", "end_of_turn_delay": 0.5},
        {"role": "assistant", "e2e_latency": 1.0, "llm_node_ttft": 0.4},
        {"role": "assistant", "e2e_latency": 2.0, "llm_node_ttft": 0.6},
        {"role": "assistant", "e2e_latency": 3.0},
    ]

    summary = latency_report.summarise(rows)

    assert summary["e2e_latency"] == {"count": 3, "p50": 2.0, "p90": 3.0, "max": 3.0}
    assert summary["llm_node_ttft"]["count"] == 2
    assert summary["end_of_turn_delay"]["count"] == 1
    assert "tts_node_ttfb" not in summary


def test_corrupt_lines_are_skipped(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    log.write_text(
        json.dumps({"role": "assistant", "e2e_latency": 1.2}) + '\n{"role": "assi\n\n',
        encoding="utf-8",
    )

    assert latency_report.load_rows(log) == [{"role": "assistant", "e2e_latency": 1.2}]


def test_report_counts_interruptions_and_missing_stages() -> None:
    rows = [
        {"session": "a", "role": "assistant", "interrupted": True, "e2e_latency": 1.0},
        {"session": "b", "role": "assistant", "interrupted": False},
    ]

    report = latency_report.format_report(rows)

    assert "Sessions: 2" in report
    assert "Agent turns interrupted: 1/2" in report
    assert "Perceived turn latency (e2e)        1     1.000" in report


def test_missing_log_is_reported_not_raised(tmp_path: Path, capsys) -> None:
    code = latency_report.main(["latency_report.py", str(tmp_path / "none.jsonl")])

    assert code == 1
    assert "No metrics log" in capsys.readouterr().out
