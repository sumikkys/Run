# Run! Pathfinding Demo V0

本版本先服务黑客松主线：已购 `D29` 从北京西出发去西安，第一次提醒还能赶上，用户忽略后原站失败，系统搜索下游站拦截原车。

## 当前闭环

- 铁路时刻：读取 `列车时刻表20260501.csv`，查原车经停和补救车。
- 城市 ETA：高德优先，缓存到 `data/amap_cache.json`；失败时走 mock 兜底。
- 人工微动线：从桌面 docx 抽取到 `data/manual_transfers.json`，北京西等有标注时优先用于进站/换乘缓冲。
- 规则引擎：先判断原站是否可达，再枚举原车下游站和北京各枢纽补救车，过滤无票、赶不上、到达太晚、换乘不足。
- DeepSeek：只生成行动卡和自然语言建议，不参与严肃判断。

## 运行

```powershell
$env:AMAP_API_KEY="..."
$env:DEEPSEEK_API_KEY="..."
python -m src.backend.extract_manual_transfers
python -m src.backend.rescue_demo --summary --with-ai
python -m src.backend.demo_server --host 127.0.0.1 --port 8765
```

打开：

```text
http://127.0.0.1:8765/
```

API：

```text
GET /api/demo?ai=1
GET /api/demo?sold_out=G317&ai=1
GET /api/demo?eta_delay=8&station_delay=10&ai=0
```

## 默认主 Demo

- 原车：`D29`，北京西 `15:59` 发，石家庄 `18:24` 到、`18:30` 发，西安 `03:47` 到。
- 第一次提醒：`14:15`，高德 ETA + 北京西人工进站缓冲后仍可赶上。
- 第二次提醒：`15:25`，原站失败。
- 推荐方案：买 `G317` 北京西 `17:00` 到石家庄 `18:00`，在石家庄接回 `D29`。
- 异常样例：`sold_out=G317` 后会重算并推荐 `G337`。

## 前端替换接口

前端只需要消费 `/api/demo` 的 JSON：

- `first_reminder`：第一次提醒可达判断。
- `second_reminder`：第二次提醒可达判断。
- `rescue_search.recommendation`：当前推荐行动卡核心数据。
- `rescue_search.candidates`：候选方案列表。
- `rescue_search.filter_reasons`：过滤原因。
- `ai_action_card.text`：DeepSeek 行动卡文案。

