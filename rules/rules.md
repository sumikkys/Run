规则引擎
复杂模型？逻辑回归？

ds：小红书自然语言转参数，赶得上的”概率“


## 三个程序的输入输出指南

### 1. `catch_probability.py`
用途：计算“现在出发能否赶上”的概率，并结合天气、早晚高峰、交通方式和用户历史画像做个性化修正。

输入：
- 函数 `compute_catch_probability(depart_dt, current_dt, travel_time_mean_min, travel_time_std_min, entry_buffer_min=20.0, context=None, user_id="default", store_path=None, use_ml=True)`
- `depart_dt`：目标出发时间，`datetime` 类型
- `current_dt`：当前时间，`datetime` 类型
- `travel_time_mean_min`：到站平均耗时，单位分钟
- `travel_time_std_min`：到站耗时标准差，单位分钟
- `entry_buffer_min`：进站/安检缓冲时间，默认 20 分钟
- `context`：场景信息，支持天气、交通方式、早晚高峰、周末、行李、拥堵程度、用户偏置
- `user_id`：本地用户标识，用于读取和更新个人画像
- `store_path`：本地 JSON 画像存储路径，不传则使用默认文件
- `use_ml`：是否启用 ML 概率融合，默认开启
- 命令行也支持同样参数，日期格式为 `YYYY-MM-DD HH:MM`

本地画像与自动训练：
- 每个 `user_id` 都有自己的本地画像，默认保存在 `catch_probability_state.json`
- 每次调用 `record_trip_outcome(...)` 时，会把当次出行的特征、结果和上下文写入本地
- 当单个用户样本数达到阈值后，系统会自动训练该用户的轻量逻辑回归模型
- 后续预测会自动融合：基础公式概率 + 用户模型概率

新增接口：
- `get_user_profile(user_id="default", store_path=None)`：读取某个用户的画像和训练状态
- `record_trip_outcome(user_id, depart_dt, current_dt, travel_time_mean_min, travel_time_std_min, caught, entry_buffer_min=20.0, context=None, store_path=None, predicted_probability=None, actual_travel_time_min=None)`：记录一次行程结果，并在样本足够时自动训练

输出：
- 返回一个 `dict`
- 主要字段：
	- `probability`：赶上的概率，范围 0~1
	- `baseline_probability`：不使用 ML 时的基础概率
	- `ml_probability`：ML 模型输出的概率，未训练完成时可能为 `None`
	- `ml_blend_weight`：基础概率与 ML 概率的融合权重
	- `ml_model_ready`：当前用户模型是否已经可用
	- `remaining_min`：距离发车还剩多少分钟
	- `travel_mean_min`：输入的平均耗时
	- `travel_std_min`：输入的标准差
	- `adjusted_travel_mean_min`：考虑场景因子后的修正均值
	- `adjusted_travel_std_min`：考虑场景因子后的修正标准差
	- `entry_buffer_min`：缓冲时间
	- `z`：标准化分数，仅在有标准差时返回
	- `profile`：当前用户画像摘要，包括偏置、方差缩放和历史样本数
	- `context`：本次预测使用的上下文
	- `feature_snapshot`：进入 ML 模型的特征快照
- CLI 输出为打印整个字典

推荐使用方式：
- 第一次预测时直接调用 `compute_catch_probability(...)`
- 行程结束后调用 `record_trip_outcome(...)` 写回结果
- 当 `sample_count` 增加后，系统会自动训练并逐步让个性化概率更准确

最小示例：
```python
from datetime import datetime
from catch_probability import TravelContext, compute_catch_probability, record_trip_outcome

result = compute_catch_probability(
		datetime(2026, 5, 25, 18, 30),
		datetime(2026, 5, 25, 17, 40),
		travel_time_mean_min=25,
		travel_time_std_min=5,
		context=TravelContext(weather="rain", rush_hour=True, transport_mode="subway"),
		user_id="alice",
)

record_trip_outcome(
	"alice",
	datetime(2026, 5, 25, 18, 30),
	datetime(2026, 5, 25, 17, 40),
	25,
	5,
	caught=True,
	context=TravelContext(weather="rain", rush_hour=True, transport_mode="subway"),
)
```

关键配置：
- 默认训练阈值：同一用户至少 8 条样本后开始训练
- 默认单用户样本上限：200 条，超出后会裁剪旧样本
- 默认画像文件：`catch_probability_state.json`

### 2. `latest_departure.py`
用途：计算“最晚什么时候出发还来得及”。

输入：
- 函数 `compute_latest_departure(depart_dt, travel_time_mean_min, travel_time_std_min=5.0, entry_buffer_min=20.0, safety_margin_min=5.0, confidence=0.999)`
- `depart_dt`：目标出发时间，`datetime` 类型
- `travel_time_mean_min`：到站平均耗时，单位分钟
- `travel_time_std_min`：到站耗时标准差，单位分钟
- `entry_buffer_min`：进站/安检缓冲时间
- `safety_margin_min`：额外安全余量
- `confidence`：期望保障概率，如 `0.95`、`0.99`、`0.999`
- 命令行参数格式同样为 `YYYY-MM-DD HH:MM` + 数值参数

输出：
- 返回 `(latest_departure_dt, details)`
- `latest_departure_dt`：最晚出发时间，`datetime` 类型
- `details`：`dict`，包含：
	- `travel_quantile_min`
	- `z`
	- `required_minutes`
	- `confidence`
	- `travel_mean_min`
	- `travel_std_min`
	- `entry_buffer_min`
	- `safety_margin_min`
- CLI 会先打印最晚出发时间，再打印详情字典

最小示例：
```python
from datetime import datetime
from latest_departure import compute_latest_departure

latest_dt, details = compute_latest_departure(
		datetime(2026, 5, 25, 18, 30),
		travel_time_mean_min=25,
		travel_time_std_min=5,
		entry_buffer_min=20,
		safety_margin_min=5,
		confidence=0.99,
)
```

### 3. `rule_engine.py`
用途：完整救急规则引擎，判断原站是否赶得上，并给出下游拦截、改签、航班兜底等方案。

输入：
- 核心入口 `RescueEngine.run_full_pipeline(order, user_state, eta_info, station_buffer, schedule_db, inventory_db, transfer_nodes)`
- `order`：`Order` 对象，包含车次、始发站、终到站、发车时间、日期、票价等
- `user_state`：`UserState` 对象，包含当前位置、当前时间、行李、步速、预算等
- `eta_info`：`ETAInfo` 对象，包含到原站和备选站的预计到达分钟数
- `station_buffer`：原站进站缓冲时间，单位分钟
- `schedule_db`：时刻表字典，键为车次号，值为经停站列表
- `inventory_db`：余票字典，键为车次号，值为余票和价格信息
- `transfer_nodes`：换乘节点字典，提供站点缓冲和步行时间

输出：
- 返回一个 `dict`
- 主要字段：
	- `status`：`NORMAL` 或 `RESCUE_READY`
	- `original_result`：原站判断结果
	- `candidate_plans`：过滤后的候选方案列表
	- `ranked`：多维排序结果
	- `best_plan`：最优方案对象
	- `search_log`：搜索过程日志
	- `anomalies`：历史异常记录
	- `state`：引擎当前状态
- 如果原站能赶上，只返回正常结果和 `action = 继续冲原站`
- 如果赶不上，会自动搜索下游拦截、改签和航班兜底方案

最小示例：
```python
engine = RescueEngine()
result = engine.run_full_pipeline(
		order,
		user_state,
		eta_info,
		station_buffer,
		schedule_db,
		inventory_db,
		transfer_nodes,
)
```

### 建议给前端的统一约定
- 时间字段统一用 `YYYY-MM-DD HH:MM`
- 距离和耗时统一用“分钟”
- 概率统一用 `0~1` 的小数
- 返回值里优先展示 `probability`、`latest_departure_dt`、`best_plan` 这三个核心结果
