from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .env_loader import load_dotenv
from .llm_client import generate_action_card, generate_transfer_advice
from .rescue_engine import DEFAULT_ORDER, RescueEngine, RescueOrder


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Run! Rescue Demo</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #f6f7f9; color: #16202a; }
    main { max-width: 1120px; margin: 0 auto; padding: 24px; }
    header { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
    h1 { margin: 0; font-size: 30px; letter-spacing: 0; }
    .sub { margin: 6px 0 0; color: #5b6570; }
    .toolbar { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 10px; background: #fff; border: 1px solid #dde2e8; padding: 14px; border-radius: 8px; }
    label { display: grid; gap: 5px; font-size: 12px; color: #5b6570; }
    input, select, button { height: 38px; border: 1px solid #cfd6df; border-radius: 6px; padding: 0 10px; font: inherit; background: #fff; color: #16202a; }
    button { border-color: #16202a; background: #16202a; color: white; cursor: pointer; }
    button.secondary { background: #fff; color: #16202a; }
    .grid { display: grid; grid-template-columns: 0.85fr 1.15fr; gap: 14px; margin-top: 14px; }
    section, .card { background: #fff; border: 1px solid #dde2e8; border-radius: 8px; padding: 16px; }
    h2 { margin: 0 0 12px; font-size: 18px; }
    h3 { margin: 12px 0 6px; font-size: 15px; }
    .status { display: flex; gap: 8px; flex-wrap: wrap; }
    .pill { padding: 6px 9px; border-radius: 999px; font-size: 13px; background: #edf1f5; }
    .ok { background: #e9f7ee; color: #176c35; }
    .bad { background: #fdecec; color: #a42a2a; }
    .warn { background: #fff4df; color: #8a5800; }
    .route { display: grid; gap: 10px; }
    .big { font-size: 22px; font-weight: 700; }
    .muted { color: #5b6570; }
    pre { white-space: pre-wrap; word-break: break-word; margin: 0; background: #101820; color: #eef6ff; padding: 14px; border-radius: 8px; max-height: 360px; overflow: auto; }
    .two { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    @media (max-width: 880px) { .toolbar, .grid, .two { grid-template-columns: 1fr; } header { display: block; } }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Run! 寻路算法 Demo</h1>
      <p class="sub">北京出发误车救急：原站失败后，搜索下游站拦截原车。</p>
    </div>
    <button class="secondary" id="refresh">重新计算</button>
  </header>

  <div class="toolbar">
    <label>车次 <input id="trainNo" value="D29"></label>
    <label>原上车站 <input id="originStation" value="北京西"></label>
    <label>目的站 <input id="destinationStation" value="西安"></label>
    <label>当前位置 <input id="currentLocation" value="北京中关村学院"></label>
    <label>第一次提醒 <input id="firstTime" value="14:15"></label>
    <label>第二次提醒 <input id="secondTime" value="15:25"></label>
    <label>城市 ETA <select id="mode"><option value="driving">驾车/打车</option><option value="transit">公交地铁</option></select></label>
    <label>ETA 异常 +分钟 <input id="etaDelay" type="number" value="0"></label>
    <label>进站排队 +分钟 <input id="stationDelay" type="number" value="0"></label>
    <label>余票消失 <input id="soldOut" placeholder="如 G317"></label>
    <label>DeepSeek <select id="ai"><option value="1">生成行动卡</option><option value="0">关闭</option></select></label>
    <label>&nbsp;<button id="run">启动救急搜索</button></label>
  </div>

  <div class="grid">
    <section>
      <h2>状态</h2>
      <div class="status" id="status"></div>
      <h3>推荐行动</h3>
      <div class="route" id="recommendation"></div>
      <h3>AI 行动卡</h3>
      <div class="card muted" id="aiCard">等待计算</div>
    </section>
    <section>
      <h2>搜索过程</h2>
      <div id="search"></div>
    </section>
  </div>

  <section style="margin-top:14px">
    <h2>规则引擎 JSON</h2>
    <pre id="raw">等待计算</pre>
  </section>
</main>

<script>
const $ = (id) => document.getElementById(id);

function params() {
  const query = new URLSearchParams({
    train_no: $('trainNo').value,
    origin_station: $('originStation').value,
    destination_station: $('destinationStation').value,
    current_location: $('currentLocation').value,
    first_time: $('firstTime').value,
    second_time: $('secondTime').value,
    mode: $('mode').value,
    eta_delay: $('etaDelay').value,
    station_delay: $('stationDelay').value,
    sold_out: $('soldOut').value,
    ai: $('ai').value
  });
  return query.toString();
}

function pill(text, cls) {
  return `<span class="pill ${cls || ''}">${text}</span>`;
}

function render(data) {
  const first = data.first_reminder;
  const second = data.second_reminder;
  const rescue = data.rescue_search;
  const rec = rescue.recommendation;
  $('status').innerHTML = [
    pill(`第一次: ${first.can_catch ? '可赶上' : '赶不上'} ${first.safety_margin_min} 分钟`, first.can_catch ? 'ok' : 'bad'),
    pill(`第二次: ${second.can_catch ? '可赶上' : '赶不上'} ${second.safety_margin_min} 分钟`, second.can_catch ? 'ok' : 'bad'),
    pill(`高德来源: ${second.eta_to_origin.source}`, second.eta_to_origin.source.includes('amap') ? 'ok' : 'warn'),
  ].join('');

  if (rec) {
    const train = rec.rescue_train;
    const rejoin = rec.original_train_rejoin;
    $('recommendation').innerHTML = `
      <div class="big">${rec.title}</div>
      <div class="two">
        <div class="card"><b>补救车</b><br>${train.from_station} ${train.departure_time} → ${train.to_station} ${train.arrival_time}<br><span class="muted">${train.train_type || ''} ${train.service_origin}→${train.service_destination}</span></div>
        <div class="card"><b>接回原车</b><br>${rejoin.station} ${rejoin.departure_time} 开<br><span class="muted">换乘余量 ${rec.transfer_margin_min} 分钟</span></div>
      </div>
      <div>${pill(`余票: ${rec.inventory.status}`, rec.inventory.status === 'available' ? 'ok' : 'warn')} ${pill(`票价约 ${rec.inventory.price_cny || '未知'} 元`)}</div>
      <div class="muted">数据源: ${rec.data_sources.join(' / ')}</div>`;
  } else {
    $('recommendation').innerHTML = `<div class="big">暂无可行追车方案</div><div class="muted">请进入改签、退票重买或航班兜底。</div>`;
  }

  $('aiCard').textContent = data.ai_action_card ? data.ai_action_card.text : 'DeepSeek 未开启或调用失败，已使用规则结果。';
  $('search').innerHTML = `
    <div>${pill(`候选 ${rescue.candidates.length} 条`, 'ok')} ${pill(`过滤 ${rescue.filter_reasons.length} 条`)}</div>
    <h3>前 5 条候选</h3>
    ${(rescue.candidates || []).slice(0, 5).map((item, idx) => `<div class="card"><b>${idx + 1}. ${item.title}</b><br><span class="muted">score ${item.score}; 换乘余量 ${item.transfer_margin_min} 分钟; 余票 ${item.inventory.status}</span></div>`).join('')}
    <h3>过滤原因样例</h3>
    ${(rescue.filter_reasons || []).slice(0, 6).map(item => `<div class="card muted">${JSON.stringify(item)}</div>`).join('')}
  `;
  $('raw').textContent = JSON.stringify(data, null, 2);
}

async function run() {
  $('raw').textContent = '计算中...';
  const res = await fetch(`/api/demo?${params()}`);
  const data = await res.json();
  render(data);
}

$('run').addEventListener('click', run);
$('refresh').addEventListener('click', run);
run();
</script>
</body>
</html>"""


class DemoHandler(BaseHTTPRequestHandler):
    engine = RescueEngine()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/demo":
            self._handle_demo(parsed.query)
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _handle_demo(self, query: str) -> None:
        params = parse_qs(query)

        def get(name: str, default: str) -> str:
            return params.get(name, [default])[0]

        order = RescueOrder(
            train_no=get("train_no", DEFAULT_ORDER.train_no),
            origin_station=get("origin_station", DEFAULT_ORDER.origin_station),
            destination_station=get("destination_station", DEFAULT_ORDER.destination_station),
            current_location=get("current_location", DEFAULT_ORDER.current_location),
            first_reminder_time=get("first_time", DEFAULT_ORDER.first_reminder_time),
            second_reminder_time=get("second_time", DEFAULT_ORDER.second_reminder_time),
        )
        sold_out = {item.strip() for item in get("sold_out", "").split(",") if item.strip()}
        result = self.engine.run_demo(
            order,
            mode=get("mode", "driving"),
            extra_eta_min=int(get("eta_delay", "0") or 0),
            extra_station_buffer_min=int(get("station_delay", "0") or 0),
            sold_out_trains=sold_out,
        )
        recommendation = result["rescue_search"].get("recommendation")
        if get("ai", "1") == "1":
            result["ai_action_card"] = generate_action_card(result)
            if recommendation:
                entry = recommendation.get("transfer_buffer", {}).get("entry")
                result["ai_transfer_advice"] = generate_transfer_advice(entry, recommendation)
        body = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        print(format % args)


def main() -> None:
    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    print(f"Run! demo server listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
