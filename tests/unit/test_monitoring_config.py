"""Static validation only: syntax and that every referenced metric is actually exported.
This does NOT prove Prometheus/Grafana/Loki load these files (see README: not verified here)."""
import json
import re
from pathlib import Path

import yaml
from prometheus_client import generate_latest

from safeloop.observability import metrics as m

ROOT = Path(__file__).resolve().parents[2] / "monitoring"


def _exported_names():
    # touch labelled metrics so every family appears in exposition output
    m.record_usage(m.ModelUsage(provider="p", model="m", input_tokens=1, output_tokens=1))
    text = generate_latest(m.REGISTRY).decode()
    return set(re.findall(r"^# TYPE (safeloop_\w+)", text, re.MULTILINE))


def _refs(blob: str) -> set[str]:
    return set(re.findall(r"safeloop_[a-z_]+", blob))


def _base(name: str) -> str:
    for suf in ("_bucket", "_count", "_sum"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def test_yaml_configs_parse():
    for f in ROOT.rglob("*.yml"):
        assert yaml.safe_load(f.read_text()) is not None, f


def test_dashboards_valid_and_reference_real_metrics():
    exported = _exported_names()
    files = sorted((ROOT / "grafana/dashboards").glob("*.json"))
    assert len(files) == 5
    uids = set()
    for f in files:
        d = json.loads(f.read_text())
        uids.add(d["uid"])
        assert d["panels"], f
        for ref in _refs(f.read_text()):
            assert _base(ref) in exported or ref in exported, (f.name, ref)
    assert len(uids) == 5


def test_alerts_reference_real_metrics():
    exported = _exported_names()
    alerts = yaml.safe_load((ROOT / "prometheus/alerts.yml").read_text())
    rules = alerts["groups"][0]["rules"]
    assert len(rules) >= 5
    for r in rules:
        for ref in _refs(r["expr"]):
            assert _base(ref) in exported or ref in exported, (r["alert"], ref)
