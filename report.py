"""Render the dashboard: one PNG snapshot plus a standalone HTML report."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

import charts

WIDTH, HEIGHT = 1600, 1060
MARGIN = 40


def _delta(series: list[tuple[int, float]], window: int = 1) -> tuple[float, float]:
    """Absolute and percent change between the latest value and `window` points back."""
    if len(series) < 2:
        return 0.0, 0.0
    reference = series[max(0, len(series) - 1 - window)][1]
    latest = series[-1][1]
    absolute = latest - reference
    percent = (absolute / reference * 100.0) if reference else 0.0
    return absolute, percent


def _arrow(value: float) -> str:
    if value > 0:
        return "▲"
    if value < 0:
        return "▼"
    return "·"


def render(config: dict, store, output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (WIDTH, HEIGHT), charts.BG)
    draw = ImageDraw.Draw(image)

    fx_series = store.series("rate_USD_CNY", limit=400)
    temp_series = store.series("temp_c", limit=200)
    humidity_series = store.series("humidity_pct", limit=200)
    stats = store.stats()
    runs = store.recent_runs(1)

    # ---------------------------------------------------------------- header
    charts.text(draw, (MARGIN, 30), "PulseDash", 28, charts.TEXT, bold=True)
    charts.text(draw, (MARGIN + 168, 40), "公开数据采集与监控看板", 16, charts.MUTED)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    charts.text(draw, (WIDTH - MARGIN, 36), "生成于 " + stamp, 14, charts.MUTED, anchor="ra")
    charts.text(draw, (WIDTH - MARGIN, 58),
                "累计 %s 个观测点 / %d 次采集" % ("{:,}".format(stats["points"]), stats["runs"]),
                13, charts.MUTED, anchor="ra")
    draw.line((MARGIN, 96, WIDTH - MARGIN, 96), fill=charts.PANEL_EDGE, width=1)

    # ---------------------------------------------------------------- KPI row
    card_width = (WIDTH - MARGIN * 2 - 40) // 3
    card_top, card_bottom = 118, 232
    fx_abs, fx_pct = _delta(fx_series, window=min(20, max(1, len(fx_series) - 1)))
    charts.kpi_card(
        draw, (MARGIN, card_top, MARGIN + card_width, card_bottom),
        "USD / CNY 参考汇率",
        "%.4f" % fx_series[-1][1] if fx_series else "—",
        ("%s %.4f (%.2f%%) 近 30 天" % (_arrow(fx_abs), abs(fx_abs), abs(fx_pct))) if fx_series else "",
        accent=charts.ACCENT,
    )
    temperature = temp_series[-1][1] if temp_series else None
    humidity = humidity_series[-1][1] if humidity_series else None
    charts.kpi_card(
        draw, (MARGIN + card_width + 20, card_top, MARGIN + card_width * 2 + 20, card_bottom),
        "实时气温 / 湿度",
        ("%.0f°C" % temperature) if temperature is not None else "—",
        ("湿度 %.0f%% · 来源 wttr.in" % humidity) if humidity is not None else "",
        accent=charts.AMBER,
    )
    repo_rows = []
    total_stars = 0.0
    for full_name in (config.get("sources", {}).get("github", {}) or {}).get("repos", []) or []:
        latest = store.latest("%s::stars" % full_name)
        if latest:
            repo_rows.append((full_name, latest[1], "%s stars" % "{:,.0f}".format(latest[1])))
            total_stars += latest[1]
    repo_rows.sort(key=lambda row: row[1], reverse=True)
    charts.kpi_card(
        draw, (MARGIN + card_width * 2 + 40, card_top, WIDTH - MARGIN, card_bottom),
        "监控仓库总 Star",
        "{:,.0f}".format(total_stars) if repo_rows else "—",
        "%d 个仓库 · GitHub REST API" % len(repo_rows) if repo_rows else "",
        accent=charts.VIOLET,
    )

    # ---------------------------------------------------------------- charts row
    charts.panel(draw, (MARGIN, 252, 828, 588))
    charts.text(draw, (MARGIN + 20, 266), "汇率走势  USD → CNY", 15, charts.TEXT, bold=True)
    charts.text(draw, (MARGIN + 20, 288), "欧洲央行参考价 · 交易日粒度", 12, charts.MUTED)
    charts.timeseries(draw, (MARGIN + 12, 306, 816, 574), fx_series,
                      color=charts.ACCENT, decimals=2, x_ticks=5, x_format="%m-%d")

    charts.panel(draw, (848, 252, WIDTH - MARGIN, 588))
    charts.text(draw, (868, 266), "逐时气温  °C", 15, charts.TEXT, bold=True)
    charts.text(draw, (868, 288), "wttr.in · 3 天预报窗口", 12, charts.MUTED)
    charts.timeseries(draw, (860, 306, WIDTH - MARGIN - 6, 574), temp_series,
                      color=charts.AMBER, decimals=0, x_ticks=5)

    # ---------------------------------------------------------------- bottom row
    charts.panel(draw, (MARGIN, 608, 560, 906))
    charts.text(draw, (MARGIN + 20, 622), "湿度  %", 15, charts.TEXT, bold=True)
    charts.text(draw, (MARGIN + 20, 644), "同一采集批次", 12, charts.MUTED)
    charts.barchart(draw, (MARGIN + 10, 664, 548, 892), humidity_series, color=charts.VIOLET)

    charts.panel(draw, (580, 608, 1092, 906))
    charts.text(draw, (600, 622), "仓库热度排名  Star", 15, charts.TEXT, bold=True)
    charts.text(draw, (600, 644), "来源 GitHub REST API", 12, charts.MUTED)
    charts.hbar_rows(draw, (600, 672, 1072, 892), repo_rows,
                     color=charts.GREEN, label_width=250)

    charts.panel(draw, (1112, 608, WIDTH - MARGIN, 906))
    charts.text(draw, (1132, 622), "采集健康度", 15, charts.TEXT, bold=True)
    charts.text(draw, (1132, 644), "最近一次运行", 12, charts.MUTED)
    results = []
    if runs:
        try:
            results = json.loads(runs[0].get("detail") or "[]")
        except json.JSONDecodeError:
            results = []
    cursor_y = 672
    for item in results:
        ok = bool(item.get("ok"))
        label = "%s · %d 点 · %dms" % (item.get("name", "?"), item.get("points", 0),
                                       item.get("elapsed_ms", 0))
        if not ok:
            label = "%s · 失败" % item.get("name", "?")
        charts.badge(draw, (1132, cursor_y), label, ok=ok)
        cursor_y += 34
    if not results:
        charts.text(draw, (1132, cursor_y), "尚无运行记录", 13, charts.MUTED)
    charts.text(draw, (1132, 866), "源之间相互隔离：单个源失败", 12, charts.MUTED)
    charts.text(draw, (1132, 884), "不影响整批采集与看板渲染", 12, charts.MUTED)

    # ---------------------------------------------------------------- footer
    draw.line((MARGIN, 928, WIDTH - MARGIN, 928), fill=charts.PANEL_EDGE, width=1)
    charts.text(draw, (MARGIN, 942),
                "数据来源：api.frankfurter.app · wttr.in · api.github.com"
                "    存储：SQLite（唯一索引去重，可重复采集）", 12, charts.MUTED)
    charts.text(draw, (WIDTH - MARGIN, 942), "PulseDash v1.0", 12, charts.MUTED, anchor="ra")

    png_path = output_dir / "dashboard.png"
    image.save(png_path, "PNG")

    html_path = output_dir / "dashboard.html"
    html_path.write_text(_html(config, store, stats, repo_rows, fx_series), encoding="utf-8")
    return [str(png_path), str(html_path)]


def _html(config: dict, store, stats: dict, repo_rows, fx_series) -> str:
    rows = []
    for full_name in store.metric_names():
        if "::" not in full_name:
            continue
        latest = store.latest(full_name)
        if latest:
            rows.append((full_name, latest[1], datetime.fromtimestamp(latest[0]).strftime("%Y-%m-%d %H:%M")))
    repo_html = "\n".join(
        "<tr><td>%s</td><td class='num'>%s</td><td>%s</td></tr>" % (
            name, "{:,.0f}".format(value), when)
        for name, value, when in rows) or "<tr><td colspan='3'>暂无数据</td></tr>"
    fx_latest = "%.4f" % fx_series[-1][1] if fx_series else "—"
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>PulseDash 看板</title>
<style>
body{{background:#0f172a;color:#e2e8f0;font-family:"Microsoft YaHei",system-ui,sans-serif;margin:0;padding:32px}}
h1{{font-size:26px;margin:0 0 4px}} .sub{{color:#94a3b8;font-size:14px;margin-bottom:24px}}
img{{width:100%;max-width:1600px;border-radius:12px;border:1px solid #334155;display:block}}
table{{border-collapse:collapse;margin-top:24px;font-size:14px;min-width:520px}}
th,td{{border-bottom:1px solid #334155;padding:8px 14px;text-align:left}}
th{{color:#94a3b8;font-weight:500}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
.card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px 24px;margin-top:24px}}
.k{{color:#94a3b8;font-size:13px}} .v{{font-size:22px;margin-top:6px}}
</style></head><body>
<h1>PulseDash 公开数据监控看板</h1>
<div class="sub">生成于 {stamp} · 累计 {points} 个观测点 · {runs} 次采集</div>
<img src="dashboard.png" alt="dashboard">
<div class="card"><div class="k">USD / CNY 最新参考汇率</div><div class="v">{fx}</div></div>
<div class="card"><div class="k">仓库指标（最新）</div>
<table><tr><th>指标</th><th style="text-align:right">数值</th><th>更新时间</th></tr>
{repo_rows}
</table></div>
</body></html>""".format(
        stamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        points="{:,}".format(stats["points"]),
        runs=stats["runs"],
        fx=fx_latest,
        repo_rows=repo_html,
    )
