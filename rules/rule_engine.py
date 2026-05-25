#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run！规则引擎 - 黑客松完整可运行版
功能：原站可达判断、下游拦截搜索、方案排序、异常重算
"""

import json
import time
import sys
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta


# ==================== 枚举与状态 ====================

class PlanType(Enum):
    """方案类型"""
    CONTINUE_ORIGINAL = "继续冲原站"
    DOWNSTREAM_INTERCEPT = "下游站拦截原车"
    ALTERNATIVE_TRAIN = "改签后续车"
    FLIGHT_FALLBACK = "航空兜底"
    ABANDON = "放弃止损"


class RiskLevel(Enum):
    """风险等级"""
    LOW = "低风险"
    MEDIUM = "中风险"
    HIGH = "高风险"


class AnomalyType(Enum):
    """异常类型"""
    ETA_INCREASE = "ETA增加"
    INVENTORY_LOST = "余票消失"
    ORIGINAL_DELAY = "原车晚点"
    RESCUE_DELAY = "补救车晚点"
    TRANSFER_FAIL = "换乘失败"
    USER_MISSED = "用户没赶上"


# ==================== 数据模型 ====================

@dataclass
class Order:
    """用户订单"""
    train_no: str
    origin_station: str
    dest_station: str
    depart_time: str  # "HH:MM"
    depart_date: str  # "2026-05-25"
    seat_class: str = "二等座"
    price: float = 0.0


@dataclass
class UserState:
    """用户状态"""
    current_location: str
    current_time: str  # "HH:MM"
    luggage: str = "light"  # light / heavy
    walk_speed: str = "normal"  # fast / normal / slow
    ignore_reminder: bool = False
    budget_limit: float = 500.0


@dataclass
class ETAInfo:
    """ETA信息"""
    to_origin: float  # 分钟
    to_alternatives: Dict[str, float] = field(default_factory=dict)
    source: str = "high德实时"
    confidence: str = "high"


@dataclass
class TransferNode:
    """换乘节点"""
    station_id: str
    station_name: str
    entry_buffer: float  # 进站安检缓冲时间（分钟）
    transfer_time: Dict[str, float]  # 换乘节点间步行时间


@dataclass
class TrainStop:
    """列车经停站"""
    station: str
    arrive_time: str
    depart_time: str
    stay_minutes: float


@dataclass
class Inventory:
    """余票信息"""
    train_no: str
    seat_class: str
    available: int
    price: float
    status: str  # available / few / sold_out


@dataclass
class RescuePlan:
    """救急方案"""
    plan_id: str
    plan_type: PlanType
    summary: str
    steps: List[Dict]
    total_time_min: float
    additional_cost: float
    saved_original_value: float
    final_arrival_time: str
    delay_vs_original: float
    success_probability: float
    risk_level: RiskLevel
    failure_threshold: str
    rejected_reason: Optional[str] = None


# ==================== 规则引擎 ====================

class RescueEngine:
    """Run！规则引擎核心"""
    
    def __init__(self):
        self.current_state = "NORMAL"  # NORMAL / REMINDER_IGNORED / ORIGINAL_FAILED / RESCUE_SEARCHING / RESCUE_READY
        self.anomalies = []
        self.last_plan = None
        self.search_log = []
        
    def evaluate_original_station(self, order: Order, user_state: UserState, 
                                   eta_info: ETAInfo, station_buffer: float) -> Dict:
        """
        判断原站是否还能赶上
        炫技点：精确计算安全余量，可视化展示
        """
        # 解析时间
        current_dt = datetime.strptime(f"{order.depart_date} {user_state.current_time}", "%Y-%m-%d %H:%M")
        depart_dt = datetime.strptime(f"{order.depart_date} {order.depart_time}", "%Y-%m-%d %H:%M")
        
        # 计算到达时间
        eta_minutes = eta_info.to_origin
        arrival_dt = current_dt + timedelta(minutes=eta_minutes)
        
        # 计算安全余量
        required_buffer = station_buffer + 5  # 额外缓冲5分钟
        latest_safe_arrival = depart_dt - timedelta(minutes=required_buffer)
        
        # 判断能否赶上
        can_catch = arrival_dt <= latest_safe_arrival
        
        # 计算剩余时间
        remaining_minutes = (latest_safe_arrival - current_dt).total_seconds() / 60
        
        result = {
            "can_catch": can_catch,
            "arrival_time": arrival_dt.strftime("%H:%M"),
            "latest_safe_arrival": latest_safe_arrival.strftime("%H:%M"),
            "required_buffer_min": required_buffer,
            "remaining_minutes": max(0, remaining_minutes),
            "reason": "正常赶上" if can_catch else f"即使最快到达，也已超过安全截止时间"
        }
        
        if not can_catch:
            self.current_state = "ORIGINAL_FAILED"
            self.search_log.append(f"原站判断失败：到达时间{result['arrival_time']} > 安全截止{result['latest_safe_arrival']}")
        
        return result
    
    def find_downstream_intercepts(self, order: Order, user_state: UserState,
                                    schedule_db: Dict, inventory_db: Dict,
                                    eta_info: ETAInfo, transfer_nodes: Dict) -> List[RescuePlan]:
        """
        搜索下游站拦截方案 - 核心炫技点
        展示：系统能智能发现"在石家庄拦截原车"这种非直觉方案
        """
        plans = []
        self.search_log.append("开始搜索下游拦截方案...")
        
        # 获取原车经停表
        train_stops = schedule_db.get(order.train_no, [])
        if not train_stops:
            self.search_log.append(f"⚠️ 未找到车次{order.train_no}的经停表")
            return plans
        
        # 找到当前站之后的经停站
        origin_index = -1
        for i, stop in enumerate(train_stops):
            if stop['station'] == order.origin_station:
                origin_index = i
                break
        
        if origin_index == -1:
            self.search_log.append(f"⚠️ 未找到原上车站{order.origin_station}在经停表中")
            return plans
        
        downstream_stops = train_stops[origin_index+1:]
        self.search_log.append(f"找到{len(downstream_stops)}个下游经停站: {[s['station'] for s in downstream_stops]}")
        
        # 对每个下游站尝试搜索补救车
        for stop in downstream_stops[:3]:  # 只查前3个站，避免过多
            station = stop['station']
            
            # 1. 检查用户能否到达补救车出发站
            alternatives = self._find_alternative_trains(order, station, schedule_db, inventory_db)
            
            for alt_train in alternatives:
                # 计算是否能赶上补救车
                alt_depart_time = alt_train['depart_time']
                alt_depart_dt = datetime.strptime(f"{order.depart_date} {alt_depart_time}", "%Y-%m-%d %H:%M")
                
                # 到补救车出发站的ETA
                eta_to_alt = eta_info.to_alternatives.get(alt_train['depart_station'], 30)
                
                # 进站缓冲
                buffer = transfer_nodes.get(alt_train['depart_station'], {}).get('entry_buffer', 10)
                
                # 判断能否赶上补救车
                current_dt = datetime.strptime(f"{order.depart_date} {user_state.current_time}", "%Y-%m-%d %H:%M")
                arrival_dt = current_dt + timedelta(minutes=eta_to_alt + buffer)
                
                if arrival_dt > alt_depart_dt:
                    self.search_log.append(f"❌ 错过补救车{alt_train['train_no']}：到达{arrival_dt.strftime('%H:%M')} > 发车{alt_depart_time}")
                    continue
                
                # 判断补救车是否能早于原车到达下游站
                alt_arrive_dt = datetime.strptime(f"{order.depart_date} {alt_train['arrive_time']}", "%Y-%m-%d %H:%M")
                original_arrive_dt = datetime.strptime(f"{order.depart_date} {stop['arrive_time']}", "%Y-%m-%d %H:%M")
                
                if alt_arrive_dt > original_arrive_dt - timedelta(minutes=10):
                    self.search_log.append(f"❌ 补救车到达太晚：{alt_arrive_dt.strftime('%H:%M')} > 原车到达{stop['arrive_time']} - 10min")
                    continue
                
                # 检查换乘是否足够
                if alt_arrive_dt < original_arrive_dt:
                    transfer_buffer = transfer_nodes.get(station, {}).get('transfer_time', {}).get('default', 5)
                    if alt_arrive_dt + timedelta(minutes=transfer_buffer) > original_arrive_dt:
                        self.search_log.append(f"❌ 换乘时间不足：需要{transfer_buffer}分钟")
                        continue
                
                # 成功找到一个方案！
                self.search_log.append(f"✅ 找到方案：{alt_train['train_no']} → {station} 拦截原车")
                
                plan = RescuePlan(
                    plan_id=f"intercept_{station}",
                    plan_type=PlanType.DOWNSTREAM_INTERCEPT,
                    summary=f"在{station}站拦截原车{order.train_no}",
                    steps=[
                        {"step": 1, "action": f"前往{alt_train['depart_station']}", "eta": eta_to_alt, "buffer": buffer},
                        {"step": 2, "action": f"乘坐{alt_train['train_no']}", "from": alt_train['depart_station'], 
                         "to": station, "depart": alt_train['depart_time'], "arrive": alt_train['arrive_time']},
                        {"step": 3, "action": f"同站换乘{order.train_no}", "from": station, "to": order.dest_station,
                         "depart": stop['depart_time'], "arrive": order.depart_time}
                    ],
                    total_time_min=(alt_arrive_dt - current_dt).total_seconds() / 60 + 
                                   (original_arrive_dt - alt_arrive_dt).total_seconds() / 60,
                    additional_cost=alt_train['price'],
                    saved_original_value=order.price,
                    final_arrival_time=stop['arrive_time'],
                    delay_vs_original=0,
                    success_probability=0.85,
                    risk_level=RiskLevel.MEDIUM,
                    failure_threshold=f"若{alt_train['train_no']}晚点超过10分钟，将无法赶上原车"
                )
                plans.append(plan)
        
        return plans
    
    def _find_alternative_trains(self, order: Order, target_station: str,
                                   schedule_db: Dict, inventory_db: Dict) -> List[Dict]:
        """查找从北京到目标站的替代列车"""
        alternatives = []
        
        # 模拟数据：从北京各站到目标站的列车
        mock_trains = [
            {"train_no": "G1234", "depart_station": "北京南", "depart_time": "16:50", 
             "arrive_time": "17:45", "price": 128.0, "seat_class": "二等座"},
            {"train_no": "G5678", "depart_station": "北京西", "depart_time": "17:10", 
             "arrive_time": "18:05", "price": 135.0, "seat_class": "二等座"},
            {"train_no": "G9012", "depart_station": "北京丰台", "depart_time": "17:30", 
             "arrive_time": "18:25", "price": 120.0, "seat_class": "二等座"},
        ]
        
        for train in mock_trains:
            if train['arrive_time'] < order.depart_time:  # 必须在原车发车前到达
                # 检查余票
                inv = inventory_db.get(train['train_no'], {})
                if inv.get('available', 0) > 0:
                    alternatives.append(train)
        
        return alternatives

    def find_change_options(self, order: Order, user_state: UserState,
                             schedule_db: Dict, inventory_db: Dict, eta_info: ETAInfo) -> List[RescuePlan]:
        """
        搜索改签（免费）选项 — 假设改签不收手续费，仅考虑时间与能否到达
        """
        plans = []
        self.search_log.append("开始搜索改签（免费）选项...")

        # 搜索同始发站的其他车次
        for train_no, stops in schedule_db.items():
            # 跳过原车
            if train_no == order.train_no:
                continue

            # 找站点信息
            if not stops:
                continue

            first_stop = stops[0]
            if first_stop['station'] != order.origin_station:
                continue

            depart_time = first_stop.get('depart_time')
            try:
                depart_dt = datetime.strptime(f"{order.depart_date} {depart_time}", "%Y-%m-%d %H:%M")
            except Exception:
                continue

            # 估算用户到达出发站的时间
            eta_to_origin = eta_info.to_origin
            entry_buffer = 10
            current_dt = datetime.strptime(f"{order.depart_date} {user_state.current_time}", "%Y-%m-%d %H:%M")
            arrival_dt = current_dt + timedelta(minutes=eta_to_origin + entry_buffer)

            if arrival_dt > depart_dt:
                self.search_log.append(f"改签候选{train_no}无法赶上：到达{arrival_dt.strftime('%H:%M')} > 发车{depart_time}")
                continue

            # 检查余票
            inv = inventory_db.get(train_no, {})
            if inv.get('available', 0) <= 0:
                self.search_log.append(f"改签候选{train_no}无余票")
                continue

            # 构造方案（改签免费，新增成本优先为0）
            plan = RescuePlan(
                plan_id=f"change_{train_no}",
                plan_type=PlanType.ALTERNATIVE_TRAIN,
                summary=f"改签至{train_no}（免费改签）",
                steps=[
                    {"step": 1, "action": f"前往{order.origin_station}并办理改签", "eta": eta_to_origin},
                    {"step": 2, "action": f"乘坐{train_no}", "depart": depart_time}
                ],
                total_time_min=(depart_dt - current_dt).total_seconds() / 60,
                additional_cost=0.0,
                saved_original_value=order.price,
                final_arrival_time=stops[-1].get('arrive_time', ''),
                delay_vs_original=0,
                success_probability=0.95,
                risk_level=RiskLevel.LOW,
                failure_threshold="改签窗口关闭或票面信息变更"
            )
            plans.append(plan)

        self.search_log.append(f"改签候选搜索完成，共{len(plans)}个")
        return plans

    def find_flight_fallbacks(self, order: Order, user_state: UserState,
                              eta_info: ETAInfo, flight_db: Optional[List[Dict]] = None) -> List[RescuePlan]:
        """
        航班兜底方案搜索（航班方案）
        简单模拟：使用传入或默认的航班池，判断到机场时间和安检缓冲是否满足
        """
        plans = []
        self.search_log.append("开始搜索航班兜底方案...")

        # 默认模拟航班
        flights = flight_db or [
            {"flight_no": "CA123", "depart_airport": "首都机场", "depart_time": "18:30", "arrive_airport": "西安咸阳", "arrive_time": "20:00", "price": 600.0},
            {"flight_no": "MU456", "depart_airport": "北京大兴", "depart_time": "17:40", "arrive_airport": "西安咸阳", "arrive_time": "19:20", "price": 520.0},
        ]

        # 机场安检缓冲（分钟）
        airport_buffer = 40

        current_dt = datetime.strptime(f"{order.depart_date} {user_state.current_time}", "%Y-%m-%d %H:%M")

        for f in flights:
            depart_dt = datetime.strptime(f"{order.depart_date} {f['depart_time']}", "%Y-%m-%d %H:%M")

            # 估算到机场时间（使用 eta_info.to_alternatives 中的机场键）
            eta_to_airport = eta_info.to_alternatives.get(f['depart_airport'], 60)
            arrival_airport_dt = current_dt + timedelta(minutes=eta_to_airport)

            # 需要提前到达机场进行安检
            if arrival_airport_dt + timedelta(minutes=airport_buffer) > depart_dt:
                self.search_log.append(f"航班{f['flight_no']}不可行：到达机场{arrival_airport_dt.strftime('%H:%M')} + 安检{airport_buffer} > 起飞{f['depart_time']}")
                continue

            # 构造方案
            plan = RescuePlan(
                plan_id=f"flight_{f['flight_no']}",
                plan_type=PlanType.FLIGHT_FALLBACK,
                summary=f"航班兜底：{f['flight_no']} 从{f['depart_airport']}起飞",
                steps=[
                    {"step": 1, "action": f"前往{f['depart_airport']}机场", "eta": eta_to_airport},
                    {"step": 2, "action": f"办理值机并乘坐{f['flight_no']}", "depart": f['depart_time'], "arrive": f['arrive_time']}
                ],
                total_time_min=(depart_dt - current_dt).total_seconds() / 60,
                additional_cost=f['price'],
                saved_original_value=order.price,
                final_arrival_time=f['arrive_time'],
                delay_vs_original=0,
                success_probability=0.7,
                risk_level=RiskLevel.MEDIUM,
                failure_threshold="航班座位有限或交通拥堵导致无法按时到达机场"
            )
            plans.append(plan)

        self.search_log.append(f"航班候选搜索完成，共{len(plans)}个")
        return plans
    
    def filter_plans(self, plans: List[RescuePlan], user_state: UserState) -> List[RescuePlan]:
        """
        过滤方案 - 炫技点：展示每个方案被过滤的原因
        """
        filtered = []
        
        for plan in plans:
            # 预算过滤
            if plan.additional_cost > user_state.budget_limit:
                plan.rejected_reason = f"超出预算：{plan.additional_cost} > {user_state.budget_limit}"
                self.search_log.append(f"❌ {plan.summary} 被过滤：{plan.rejected_reason}")
                continue
            
            # 风险过滤
            if plan.risk_level == RiskLevel.HIGH:
                plan.rejected_reason = "风险过高"
                self.search_log.append(f"❌ {plan.summary} 被过滤：{plan.rejected_reason}")
                continue
            
            # 成功概率过滤
            if plan.success_probability < 0.5:
                plan.rejected_reason = f"成功概率过低：{plan.success_probability:.0%}"
                self.search_log.append(f"❌ {plan.summary} 被过滤：{plan.rejected_reason}")
                continue
            
            filtered.append(plan)
            self.search_log.append(f"✅ {plan.summary} 通过过滤")
        
        return filtered
    
    def rank_plans(self, plans: List[RescuePlan]) -> Dict:
        """
        方案排序 - 炫技点：多维度排序，展示不同视角
        """
        if not plans:
            return {"best": None, "all": []}
        
        # 1. 综合损失最低（默认）
        def loss_score(p):
            return p.additional_cost / p.saved_original_value * 0.5 + (1 - p.success_probability) * 0.5
        
        sorted_by_loss = sorted(plans, key=loss_score)
        
        # 2. 最早到达
        sorted_by_time = sorted(plans, key=lambda p: p.total_time_min)
        
        # 3. 最省钱
        sorted_by_cost = sorted(plans, key=lambda p: p.additional_cost)
        
        # 4. 保票率最高
        sorted_by_save = sorted(plans, key=lambda p: p.saved_original_value, reverse=True)
        
        result = {
            "best": sorted_by_loss[0] if sorted_by_loss else None,
            "by_loss": sorted_by_loss,
            "by_time": sorted_by_time,
            "by_cost": sorted_by_cost,
            "by_save": sorted_by_save,
            "count": len(plans)
        }
        
        if result["best"]:
            self.search_log.append(f"🏆 最佳方案：{result['best'].summary}，综合损失最低")
        
        return result
    
    def handle_anomaly(self, anomaly_type: AnomalyType, params: Dict) -> Dict:
        """
        异常处理 - 炫技点：任何异常触发完整重算
        """
        self.anomalies.append({
            "type": anomaly_type.value,
            "params": params,
            "timestamp": time.time()
        })
        
        self.search_log.append(f"⚡ 触发异常：{anomaly_type.value} {params}")
        self.current_state = "RESCUE_SEARCHING"
        
        # 根据异常类型调整参数
        if anomaly_type == AnomalyType.ETA_INCREASE:
            params['delta'] = params.get('delta', 8)
        elif anomaly_type == AnomalyType.INVENTORY_LOST:
            params['train_no'] = params.get('train_no', 'G1234')
        elif anomaly_type == AnomalyType.ORIGINAL_DELAY:
            params['delay_min'] = params.get('delay_min', 12)
        elif anomaly_type == AnomalyType.RESCUE_DELAY:
            params['delay_min'] = params.get('delay_min', 8)
        
        return {
            "anomaly_type": anomaly_type.value,
            "params": params,
            "triggered_recomputation": True,
            "state": self.current_state
        }
    
    def run_full_pipeline(self, order: Order, user_state: UserState,
                           eta_info: ETAInfo, station_buffer: float,
                           schedule_db: Dict, inventory_db: Dict,
                           transfer_nodes: Dict) -> Dict:
        """
        完整救急流程 - 一键运行
        """
        self.search_log = []
        self.search_log.append("🚀 Run！救急引擎启动")
        
        # Step 1: 判断原站
        original_result = self.evaluate_original_station(order, user_state, eta_info, station_buffer)
        self.search_log.append(f"📊 原站判断结果：{'可赶上' if original_result['can_catch'] else '赶不上'}")
        
        if original_result['can_catch']:
            return {
                "status": "NORMAL",
                "original_result": original_result,
                "action": "继续冲原站",
                "search_log": self.search_log
            }
        
        # Step 2: 搜索救急候选（下游拦截、改签、航班）
        plans = []
        intercepts = self.find_downstream_intercepts(order, user_state, schedule_db, inventory_db, eta_info, transfer_nodes)
        plans.extend(intercepts)
        change_options = self.find_change_options(order, user_state, schedule_db, inventory_db, eta_info)
        plans.extend(change_options)
        flight_options = self.find_flight_fallbacks(order, user_state, eta_info)
        plans.extend(flight_options)
        self.search_log.append(f"🔍 搜索完成，找到{len(plans)}个候选方案（拦截{len(intercepts)}，改签{len(change_options)}，航班{len(flight_options)}）")
        
        # Step 3: 过滤方案
        filtered_plans = self.filter_plans(plans, user_state)
        self.search_log.append(f"🔎 过滤后剩余{len(filtered_plans)}个方案")
        
        # Step 4: 排序
        ranked = self.rank_plans(filtered_plans)
        
        # Step 5: 生成输出
        output = {
            "status": "RESCUE_READY",
            "original_result": original_result,
            "candidate_plans": filtered_plans,
            "ranked": ranked,
            "best_plan": ranked["best"],
            "search_log": self.search_log,
            "anomalies": self.anomalies,
            "state": self.current_state
        }
        
        return output


# ==================== 演示运行 ====================

def run_demo():
    """运行完整Demo"""
    print("=" * 60)
    print("  Run！规则引擎 - 黑客松演示")
    print("=" * 60)
    
    # 1. 初始化数据
    engine = RescueEngine()
    
    # 用户订单：G651 北京西→西安北
    order = Order(
        train_no="G651",
        origin_station="北京西",
        dest_station="西安北",
        depart_time="16:30",
        depart_date="2026-05-25",
        seat_class="二等座",
        price=298.0
    )
    
    # 用户状态：中关村学院，当前15:50
    user_state = UserState(
        current_location="中关村学院",
        current_time="15:50",
        luggage="light",
        walk_speed="normal",
        ignore_reminder=True,
        budget_limit=500.0
    )
    
    # ETA信息
    eta_info = ETAInfo(
        to_origin=35.0,  # 到北京西35分钟
        to_alternatives={
            "北京南": 32.0,
            "北京丰台": 28.0
        },
        source="高德缓存",
        confidence="high"
    )
    
    # 车站缓冲
    station_buffer = 15.0  # 北京西站进站缓冲15分钟
    
    # 时刻表模拟
    schedule_db = {
        "G651": [
            {"station": "北京西", "arrive_time": "16:30", "depart_time": "16:30"},
            {"station": "石家庄", "arrive_time": "17:58", "depart_time": "18:00"},
            {"station": "郑州东", "arrive_time": "19:20", "depart_time": "19:22"},
            {"station": "西安北", "arrive_time": "20:30", "depart_time": "20:30"}
        ]
    }
    
    # 余票模拟
    inventory_db = {
        "G1234": {"available": 20, "price": 128.0},
        "G5678": {"available": 5, "price": 135.0},
        "G9012": {"available": 0, "price": 120.0}
    }
    
    # 换乘节点
    transfer_nodes = {
        "北京南": {"entry_buffer": 12.0, "transfer_time": {"default": 5.0}},
        "北京西": {"entry_buffer": 15.0, "transfer_time": {"default": 5.0}},
        "北京丰台": {"entry_buffer": 10.0, "transfer_time": {"default": 5.0}},
        "石家庄": {"entry_buffer": 8.0, "transfer_time": {"default": 5.0}}
    }
    
    # ===== 运行主流程 =====
    print("\n[1/4] 启动救急流程...")
    result = engine.run_full_pipeline(
        order, user_state, eta_info, station_buffer, 
        schedule_db, inventory_db, transfer_nodes
    )
    
    print("\n[2/4] 原站判断结果：")
    print(f"  - 能否赶上：{'可以' if result['original_result']['can_catch'] else '不行'}")
    print(f"  - 到达时间：{result['original_result']['arrival_time']}")
    print(f"  - 安全截止：{result['original_result']['latest_safe_arrival']}")
    print(f"  - 需要缓冲：{result['original_result']['required_buffer_min']}分钟")
    if result['status'] == 'RESCUE_READY':
        print("\n[3/4] 搜索日志（透明展示）：")
        def _safe_print(s: str):
            try:
                print(s)
            except UnicodeEncodeError:
                enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
                print(s.encode(enc, errors='replace').decode(enc))

        for i, log in enumerate(result['search_log'][-6:]):  # 展示最近6条
            _safe_print(f"  {log}")

        print(f"\n[4/4] 最终推荐方案：")
        best = result['best_plan']
        if best:
            print(f"  - 方案类型：{best.plan_type.value}")
            print(f"  - 方案摘要：{best.summary}")
            print(f"  - 总耗时：{best.total_time_min:.0f}分钟")
            print(f"  - 新增成本：¥{best.additional_cost}")
            print(f"  - 保票价值：¥{best.saved_original_value}")
            print(f"  - 成功概率：{best.success_probability:.0%}")
            print(f"  - 风险等级：{best.risk_level.value}")
            print(f"  - 失败阈值：{best.failure_threshold}")
            print(f"\n  - 行动步骤：")
            for step in best.steps:
                print(f"    - {step['action']}")
        else:
            print("  未找到有效救急方案")
    
    # ===== 异常演示 =====
    print("\n" + "=" * 60)
    print("  异常重算演示（炫技点）")
    print("=" * 60)
    
    # 注入异常：ETA增加8分钟
    print("\n[异常1] 打车堵车，ETA +8分钟")
    engine.handle_anomaly(AnomalyType.ETA_INCREASE, {"delta": 8})
    
    # 重新运行（实际场景中自动触发）
    result2 = engine.run_full_pipeline(
        order, user_state, eta_info, station_buffer, 
        schedule_db, inventory_db, transfer_nodes
    )
    
    print(f"\n  -> 重新计算后，最佳方案：{result2['best_plan'].summary if result2['best_plan'] else '无'}")
    
    # 注入异常：补救车余票消失
    print("\n[异常2] 补救车G1234余票消失")
    inventory_db["G1234"]["available"] = 0
    engine.handle_anomaly(AnomalyType.INVENTORY_LOST, {"train_no": "G1234"})
    
    result3 = engine.run_full_pipeline(
        order, user_state, eta_info, station_buffer, 
        schedule_db, inventory_db, transfer_nodes
    )
    
    print(f"\n  -> 重新计算后，最佳方案：{result3['best_plan'].summary if result3['best_plan'] else '无'}")
    if result3['best_plan']:
        print(f"  -> 系统自动切换到Plan B：{result3['best_plan'].summary}")
    
    # ===== 统计信息 =====
    print("\n" + "=" * 60)
    print("  引擎统计")
    print("=" * 60)
    print(f"  总异常数：{len(engine.anomalies)}")
    print(f"  搜索日志：{len(engine.search_log)}条")
    print(f"  最终状态：{engine.current_state}")
    print("\n  规则引擎运行完毕！")
    print("=" * 60)


if __name__ == "__main__":
    run_demo()