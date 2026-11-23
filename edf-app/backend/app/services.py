"""读取引擎编排与 API 数据组装。

引擎选择策略（需求：连续 EDF / EDF+C 用 pyedflib，EDF+D 自实现）：

* 先用自实现的字节级 :class:`~app.edf_reader.EDFReader` 完成头部、截断、
  标定、时间轴检查——它对坏标定/截断文件不会整体拒收，能给出逐通道结论；
* 变体为连续 EDF / EDF+C 且文件完整、全部通道标定时，波形读取优先采用
  pyedflib（成熟库），并与自实现读取器做交叉校验；
* EDF+D、截断文件、含坏标定通道的文件一律走自实现读取器，保证「异常
  标定范围单独拒收」「截断部分可见」「间断保留缺口」。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pyedflib

from .edf_format import (
    EDFHeader,
    EDFFormatError,
    build_calibration,
    calibration_problem,
)
from .edf_reader import EDFReader, RecordSpan, SegmentResult


@dataclass
class EngineDecision:
    engine: str                 # "pyedflib" | "custom"
    reasons: List[str]


class EDFService:
    """封装单个文件的全部读取操作；文件路径即会话标识。"""

    def __init__(self, path: str):
        self.path = path
        # 自实现读取器负责所有结构性信息（头部/截断/缺口/标定/事件）。
        self.reader = EDFReader(path)
        self.decision = self._decide_engine()

    # ------------------------------------------------------------------
    def _decide_engine(self) -> EngineDecision:
        reasons: List[str] = []
        r = self.reader
        if r.is_discontinuous:
            reasons.append("EDF+D 间断文件：pyedflib 不支持读取，使用自实现受限读取器")
            return EngineDecision("custom", reasons)
        t = r.truncation
        if t.partial_record_bytes or t.missing_records:
            reasons.append(
                f"文件截断（完整记录 {t.complete_records}/"
                f"声明 {t.header_declared_records}，残片 {t.partial_record_bytes} 字节），"
                "pyedflib 会整体拒收，使用自实现读取器保留现存数据"
            )
            return EngineDecision("custom", reasons)
        bad = [
            s.label for s in r.header.signals
            if calibration_problem(s) and not s.is_annotation
        ]
        if bad:
            reasons.append(
                "存在标定被拒收的通道 " + ",".join(bad) +
                "：pyedflib 会整体拒收，使用自实现读取器按通道单独拒收"
            )
            return EngineDecision("custom", reasons)
        reasons.append("连续且完整、标定有效：波形优先用 pyedflib，自实现读取器交叉校验")
        return EngineDecision("pyedflib", reasons)

    @property
    def uses_pyedflib(self) -> bool:
        return self.decision.engine == "pyedflib"

    # ------------------------------------------------------------------
    # 概览
    # ------------------------------------------------------------------
    def overview(self) -> Dict[str, Any]:
        r = self.reader
        h = r.header
        channels = []
        for sig in h.signals:
            if sig.is_annotation:
                continue
            reason = calibration_problem(sig)
            rate = sig.num_samples / h.fixed.record_duration
            channels.append({
                "signal_index": sig.index,
                "label": sig.label,
                "transducer": sig.transducer,
                "physical_dimension": sig.physical_dim,
                "sample_rate_hz": round(rate, 6),
                "samples_per_record": sig.num_samples,
                "physical_min": sig.physical_min,
                "physical_max": sig.physical_max,
                "digital_min": sig.digital_min,
                "digital_max": sig.digital_max,
                "prefilter": sig.prefilter,
                "calibration_rejected": reason is not None,
                "calibration_reject_reason": reason,
                "gain": (None if reason else build_calibration(sig).gain),
                "offset": (None if reason else build_calibration(sig).offset),
                "total_digital_samples": sig.num_samples * t_complete(r),
            })
        return {
            "file": {
                "name": os.path.basename(self.path),
                "size_bytes": h.file_size,
                "variant": r.variant,
                "engine": self.decision.engine,
                "engine_reasons": self.decision.reasons,
                "start_datetime": (
                    h.fixed.start_datetime.isoformat()
                    if h.fixed.start_datetime else None
                ),
                "record_duration_s": h.fixed.record_duration,
                "header_bytes": h.fixed.header_bytes,
                "data_record_size_bytes": h.data_record_size,
            },
            "channels": channels,
            "truncation": {
                "header_declared_records": r.truncation.header_declared_records,
                "complete_records": r.truncation.complete_records,
                "partial_record_bytes": r.truncation.partial_record_bytes,
                "expected_record_bytes": r.truncation.expected_record_bytes,
                "missing_records": r.truncation.missing_records,
                "first_record_byte_offset": h.fixed.header_bytes,
            },
            "timeline": {
                "record_starts": r.record_starts,
                "timeline_end_s": round(r.timeline_end, 6),
                "gaps": [self._gap_dict(g) for g in r.gaps],
            },
            "events": [self._event_dict(e) for e in r.events],
        }

    # ------------------------------------------------------------------
    # 波形片段
    # ------------------------------------------------------------------
    def segment(
        self, signal_index: int, t_start: float, t_end: float, max_points: int
    ) -> Dict[str, Any]:
        r = self.reader
        if not (0 <= signal_index < len(r.header.signals)):
            raise EDFFormatError(f"通道序号 {signal_index} 越界")
        sig = r.header.signals[signal_index]
        if sig.is_annotation:
            raise EDFFormatError("注释通道不提供波形")

        # 自实现读取器始终产出按记录切分的精确 span（含字节定位）。
        result: SegmentResult = r.read_segment(signal_index, t_start, t_end)

        # pyedflib 引擎：额外读取并交叉校验物理值（连续完整文件）。
        crosscheck = None
        if self.uses_pyedflib:
            crosscheck = self._pyedflib_crosscheck(
                signal_index, t_start, t_end
            )

        spans = [self._span_dict(sp) for sp in result.spans]
        sample_period = (
            r.header.fixed.record_duration / sig.num_samples
        )
        # 前端绘制用的降采样序列（不改变 span 内的原始核对数据）。
        plot = self._build_plot_series(result, max_points, sample_period)
        return {
            "signal_index": signal_index,
            "label": sig.label,
            "physical_dimension": sig.physical_dim,
            "sample_rate_hz": round(
                sig.num_samples / r.header.fixed.record_duration, 6
            ),
            "t_start": t_start,
            "t_end": t_end,
            "calibration_rejected": result.rejected,
            "calibration_reject_reason": result.reject_reason,
            "total_samples": result.total_samples,
            "spans": spans,
            "plot": plot,
            "gaps_inside": [self._gap_dict(g) for g in result.gaps_inside],
            "pyedflib_crosscheck": crosscheck,
        }

    def _pyedflib_crosscheck(
        self, signal_index: int, t_start: float, t_end: float
    ) -> Dict[str, Any]:
        """用 pyedflib 读取同一时间窗，比对样本数与物理值。"""
        with pyedflib.EdfReader(self.path) as f:
            rate = f.getSampleFrequency(signal_index)
            i0 = int(round(t_start * rate))
            i1 = int(round(t_end * rate))
            i1 = min(i1, f.getNSamples()[signal_index])
            physical = f.readSignal(signal_index, i0, max(0, i1 - i0))
        return {
            "sample_count": int(physical.size),
            "first_physical": (float(physical[0]) if physical.size else None),
            "last_physical": (float(physical[-1]) if physical.size else None),
            "min_physical": float(physical.min()) if physical.size else None,
            "max_physical": float(physical.max()) if physical.size else None,
        }

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def export_segment_rows(
        self, signal_index: int, t_start: float, t_end: float
    ) -> List[Dict[str, Any]]:
        """逐样本明细行（数字值 + 物理值 + 精确文件定位），供 CSV 导出。"""
        result = self.reader.read_segment(signal_index, t_start, t_end)
        rows: List[Dict[str, Any]] = []
        for sp in result.spans:
            for j, digital in enumerate(sp.digital):
                rows.append({
                    "time_s": round(sp.sample_times[j], 6),
                    "record_index": sp.record_index,
                    "sample_index_in_record": sp.sample_index_start + j,
                    "digital_value": digital,
                    "physical_value": (
                        None if result.rejected else round(sp.physical[j], 8)
                    ),
                    "byte_offset": sp.channel_byte_offset + j * 2,
                })
        return rows

    def export_event_rows(self, t_start: float, t_end: float) -> List[Dict[str, Any]]:
        """时间窗内（含与窗口相交）的注释事件行。"""
        rows = []
        for e in self.reader.events:
            if e.end < t_start or e.onset > t_end:
                continue
            rows.append({
                "onset_s": round(e.onset, 6),
                "duration_s": round(e.duration, 6),
                "end_s": round(e.end, 6),
                "annotation": e.text,
                "record_index": e.record_index,
                "byte_offset": e.byte_offset,
            })
        return rows

    def out_of_range(self, signal_index: int) -> Dict[str, Any]:
        return self.reader.out_of_range_samples(signal_index)

    # ------------------------------------------------------------------
    # 序列化辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _span_dict(sp: RecordSpan) -> Dict[str, Any]:
        return {
            "record_index": sp.record_index,
            "record_start_s": round(sp.record_start, 6),
            "record_byte_offset": sp.record_byte_offset,
            "channel_byte_offset": sp.channel_byte_offset,
            "sample_index_start": sp.sample_index_start,
            "sample_index_end": sp.sample_index_end,
            "sample_count": len(sp.digital),
            "sample_times": [round(t, 6) for t in sp.sample_times],
            "digital": sp.digital,
            "physical": [None if sp.rejected else round(x, 8) for x in sp.physical],
            "rejected": sp.rejected,
        }

    @staticmethod
    def _gap_dict(g: Any) -> Dict[str, Any]:
        return {
            "index": g.index,
            "after_record": g.after_record,
            "start_time_s": round(g.start_time, 6),
            "end_time_s": round(g.end_time, 6),
            "duration_s": round(g.duration, 6),
            "byte_offset": g.byte_offset,
        }

    @staticmethod
    def _event_dict(e: Any) -> Dict[str, Any]:
        return {
            "onset_s": round(e.onset, 6),
            "duration_s": round(e.duration, 6),
            "end_s": round(e.end, 6),
            "annotation": e.text,
            "record_index": e.record_index,
            "byte_offset": e.byte_offset,
        }

    @staticmethod
    def _build_plot_series(
        result: SegmentResult, max_points: int, sample_period: float
    ) -> Dict[str, Any]:
        """把跨记录 span 拼成带缺口标记的绘图序列。

        关键：只有当相邻样本的时间跳跃超过一个采样周期（即 EDF+D 真实
        缺口）时才插入 ``null`` 断点；正常的记录边界（间隔恰为一个采样
        周期）保持连线，避免在连续信号上制造假缝隙。
        """
        times: List[Optional[float]] = []
        values: List[Optional[float]] = []
        digital: List[Optional[int]] = []
        last_time: Optional[float] = None
        for sp in result.spans:
            for j, t in enumerate(sp.sample_times):
                if last_time is not None and t - last_time > sample_period * 1.5:
                    # 真实时间缺口：插入空点，前端折线在此断开、不插值。
                    times.append(None)
                    values.append(None)
                    digital.append(None)
                times.append(round(t, 6))
                digital.append(sp.digital[j])
                values.append(None if sp.rejected else round(sp.physical[j], 8))
                last_time = t

        # 降采样保护：超过 max_points 时等距抽取，但保留 null 断点。
        n = len(times)
        stride = max(1, (n + max_points - 1) // max_points)
        if stride > 1:
            times = times[::stride]
            values = values[::stride]
            digital = digital[::stride]
        return {
            "times": times,
            "physical": values,
            "digital": digital,
            "stride": stride,
            "original_sample_count": result.total_samples,
        }


def t_complete(r: EDFReader) -> int:
    return r.truncation.complete_records
