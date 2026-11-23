"""受限的 EDF/EDF+ 自实现读取器。

设计原则（对应需求中的硬性约束）：

1. **按通道各自的采样位置读取**。每条数据记录内各通道样本数可以不同，
   读取某通道某记录时按该通道在记录内的字节偏移切片，绝不同步对齐到
   统一行数；同一时间窗在不同通道返回不同数量的样本。
2. **EDF+D 间断保留为时间缺口**。记录起点取注释通道首条 TAL 的 onset
   （EDF+ 规范的 time-keeping TAL），相邻记录起点不连续即形成 gap，
   样本时间戳挂在各自记录起点上，缺口两侧不会被连成连续信号。
3. **注释按文件含义展开**。TAL 的 onset 是相对文件起点的绝对秒数（跨
   记录、跨间断均成立），duration 直接给出持续时间；跨记录的长注释
   不需要拼接，原样保留起止区间。
4. **截断显式可见**。只返回文件中完整存在的数据记录，部分写入的尾记录
   计入 truncation 报告，读取位置全部带文件字节偏移与记录序号。

该读取器覆盖 README 声明的格式子集；连续 EDF / EDF+C 的业务读取在
:mod:`app.services` 中优先走 pyedflib，本读取器承担 EDF+D 与截断文件。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .edf_format import (
    ANNOTATION_LABEL,
    EDFHeader,
    EDFFormatError,
    TalAnnotation,
    build_calibration,
    calibration_problem,
    decode_digital_samples,
    parse_header,
    parse_tal_block,
)

# 时间比较容差：EDF+ 时间戳为十进制秒，浮点/序列化误差控制在 1ms 内。
TIME_EPS = 1e-3


@dataclass
class TruncationInfo:
    header_declared_records: int          # 头部 num_records（-1 未知）
    complete_records: int                 # 文件中完整可读的记录数
    partial_record_bytes: int             # 尾部不完整记录的字节数
    expected_record_bytes: int
    missing_records: int                  # 相对头部声明缺少的完整记录数


@dataclass
class Gap:
    index: int                  # 第几个缺口（从 0 开始）
    after_record: int           # 缺口前一条记录序号
    start_time: float           # 文件时间轴上的缺口起点（秒）
    end_time: float             # 缺口终点（秒）
    duration: float
    byte_offset: int            # 缺口后一条记录的文件字节偏移


@dataclass
class AnnotationEvent:
    onset: float
    duration: float
    end: float
    text: str
    record_index: int
    byte_offset: int

    @property
    def is_duration_annotated(self) -> bool:
        return self.duration > 0


@dataclass
class RecordSpan:
    """某通道在一条数据记录内、落入查询时间窗的样本片段。"""

    record_index: int
    record_start: float               # 该记录在文件时间轴上的起点（秒）
    record_byte_offset: int           # 记录首字节在文件中的偏移
    channel_byte_offset: int          # 通道样本首字节在文件中的偏移
    sample_index_start: int           # 片段在该记录通道样本中的起始下标
    sample_index_end: int             # 结束下标（不含）
    sample_times: List[float]         # 每个样本的文件时间轴时间（秒）
    digital: List[int]
    physical: List[float]
    rejected: bool = False            # 标定被拒收时只有数字值，物理值为空


@dataclass
class SegmentResult:
    signal_index: int
    label: str
    t_start: float
    t_end: float
    spans: List[RecordSpan]
    gaps_inside: List[Gap]
    rejected: bool
    reject_reason: Optional[str]

    @property
    def total_samples(self) -> int:
        return sum(len(s.digital) for s in self.spans)


class EDFReader:
    """基于文件字节偏移的随机访问读取器（线程安全：每次调用独立 fd）。"""

    def __init__(self, path: str):
        self.path = path
        self.file_size = os.path.getsize(path)
        with open(path, "rb") as fp:
            head_buf = fp.read(min(self.file_size, 65536))
        self.header: EDFHeader = parse_header(head_buf, self.file_size)
        self._record_size = self.header.data_record_size
        self._channel_offsets: List[int] = []
        cursor = 0
        for sig in self.header.signals:
            self._channel_offsets.append(cursor)
            cursor += sig.num_samples * 2

        complete, partial = divmod(
            max(0, self.file_size - self.header.fixed.header_bytes),
            self._record_size,
        )
        declared = self.header.fixed.num_records
        self.truncation = TruncationInfo(
            header_declared_records=declared,
            complete_records=complete,
            partial_record_bytes=partial,
            expected_record_bytes=self._record_size,
            missing_records=max(0, declared - complete) if declared >= 0 else 0,
        )

        self._record_starts: Optional[List[float]] = None
        self._gaps: Optional[List[Gap]] = None
        self._events: Optional[List[AnnotationEvent]] = None
        self._tals_by_record: Optional[List[List[TalAnnotation]]] = None

    # ------------------------------------------------------------------
    # 变体与时间轴
    # ------------------------------------------------------------------
    @property
    def variant(self) -> str:
        reserved = self.header.fixed.reserved
        if reserved.startswith("EDF+D"):
            return "EDF+D"
        if reserved.startswith("EDF+C"):
            return "EDF+C"
        if self.header.annotation_signal_index is not None:
            return "EDF+"
        return "EDF"

    @property
    def is_discontinuous(self) -> bool:
        return self.variant == "EDF+D"

    def record_byte_offset(self, record_index: int) -> int:
        return self.header.fixed.header_bytes + record_index * self._record_size

    def _ensure_timeline(self) -> None:
        if self._record_starts is not None:
            return
        tals_per_record = [
            self._read_tals(r) for r in range(self.truncation.complete_records)
        ]
        self._tals_by_record = tals_per_record

        record_duration = self.header.fixed.record_duration
        starts: List[float] = []
        gaps: List[Gap] = []
        for r, tals in enumerate(tals_per_record):
            if tals:
                # EDF+ 规范：每条注释记录的首个 TAL 标记该数据记录起点。
                start = tals[0].onset
            else:
                # 无注释通道或该记录无 TAL：退化为连续等距时间轴。
                start = r * record_duration
            if starts:
                expected = starts[-1] + record_duration
                if start > expected + TIME_EPS:
                    gaps.append(
                        Gap(
                            index=len(gaps),
                            after_record=r - 1,
                            start_time=expected,
                            end_time=start,
                            duration=start - expected,
                            byte_offset=self.record_byte_offset(r),
                        )
                    )
            starts.append(start)
        self._record_starts = starts
        self._gaps = gaps

        events: List[AnnotationEvent] = []
        for tals in tals_per_record:
            for tal in tals:
                # 空文本 TAL 只承担 time-keeping，不是业务注释事件。
                if not tal.text:
                    continue
                events.append(
                    AnnotationEvent(
                        onset=tal.onset,
                        duration=tal.duration,
                        end=tal.onset + tal.duration,
                        text=tal.text,
                        record_index=tal.record_index,
                        byte_offset=tal.byte_offset,
                    )
                )
        events.sort(key=lambda e: (e.onset, e.byte_offset))
        self._events = events

    @property
    def record_starts(self) -> List[float]:
        self._ensure_timeline()
        return self._record_starts  # type: ignore[return-value]

    @property
    def gaps(self) -> List[Gap]:
        self._ensure_timeline()
        return self._gaps  # type: ignore[return-value]

    @property
    def events(self) -> List[AnnotationEvent]:
        self._ensure_timeline()
        return self._events  # type: ignore[return-value]

    @property
    def timeline_end(self) -> float:
        """文件时间轴末端（含最后一条记录自身时长）。"""
        starts = self.record_starts
        if not starts:
            return 0.0
        return starts[-1] + self.header.fixed.record_duration

    # ------------------------------------------------------------------
    # 字节级读取
    # ------------------------------------------------------------------
    def _read_record_bytes(self, record_index: int) -> bytes:
        if not 0 <= record_index < self.truncation.complete_records:
            raise EDFFormatError(
                f"记录 {record_index} 不存在或未完整写入"
                f"（完整记录数 {self.truncation.complete_records}）"
            )
        offset = self.record_byte_offset(record_index)
        with open(self.path, "rb") as fp:
            fp.seek(offset)
            raw = fp.read(self._record_size)
        if len(raw) != self._record_size:
            raise EDFFormatError(f"记录 {record_index} 读取不完整")
        return raw

    def _read_tals(self, record_index: int) -> List[TalAnnotation]:
        ann_idx = self.header.annotation_signal_index
        if ann_idx is None:
            return []
        raw = self._read_record_bytes(record_index)
        start = self._channel_offsets[ann_idx]
        block = raw[start: start + self.header.signals[ann_idx].num_samples * 2]
        # 注释块以 0x00 填充，解析器内部自行截断。
        return parse_tal_block(
            block, record_index, self.record_byte_offset(record_index) + start
        )

    def read_digital(self, signal_index: int, record_index: int) -> List[int]:
        """读取指定通道、指定记录的原始数字样本（不做任何跨通道对齐）。"""
        sig = self.header.signals[signal_index]
        raw = self._read_record_bytes(record_index)
        start = self._channel_offsets[signal_index]
        block = raw[start: start + sig.num_samples * 2]
        return decode_digital_samples(block)

    def channel_byte_position(self, signal_index: int, record_index: int) -> int:
        """通道样本首字节在整个文件中的绝对偏移（用于前端核对定位）。"""
        return self.record_byte_offset(record_index) + self._channel_offsets[signal_index]

    # ------------------------------------------------------------------
    # 时间窗读取：按各通道采样位置独立截取
    # ------------------------------------------------------------------
    def read_segment(
        self,
        signal_index: int,
        t_start: float,
        t_end: float,
    ) -> SegmentResult:
        if t_end < t_start:
            raise EDFFormatError("时间窗终点早于起点")
        sig = self.header.signals[signal_index]
        if sig.is_annotation:
            raise EDFFormatError("注释通道不提供波形样本")
        reason = calibration_problem(sig)
        cal = None if reason else build_calibration(sig)
        record_duration = self.header.fixed.record_duration
        n_per_record = sig.num_samples
        sample_period = record_duration / n_per_record
        starts = self.record_starts

        spans: List[RecordSpan] = []
        for r, rec_start in enumerate(starts):
            rec_end = rec_start + record_duration
            if rec_end <= t_start or rec_start >= t_end:
                continue
            # 该通道在本记录内的采样位置（各通道独立，不与其他通道对齐）。
            # 用容差处理浮点边界：落在窗口边界上的样本（如 1Hz 通道的 t=1.5
            # 恰好处于 [1.25,1.75) 计算边界）不应被舍入误差排除。
            k_lo = max(
                0, int(math.floor((t_start - rec_start) / sample_period + 1e-9))
            )
            k_hi = min(
                n_per_record,
                int(math.ceil((t_end - rec_start) / sample_period - 1e-9))
                + 1,
            )
            # 再按真实样本时间精确过滤，避免 k_lo 多取边界外样本。
            while k_lo < k_hi and rec_start + k_lo * sample_period < t_start - 1e-9:
                k_lo += 1
            while k_hi > k_lo and rec_start + (k_hi - 1) * sample_period >= t_end - 1e-9:
                k_hi -= 1
            if k_hi <= k_lo:
                continue
            digital_all = self.read_digital(signal_index, r)
            digital = digital_all[k_lo:k_hi]
            times = [
                rec_start + (k_lo + j) * sample_period for j in range(len(digital))
            ]
            physical = [] if reason else [cal.to_physical(d) for d in digital]  # type: ignore[union-attr]
            spans.append(
                RecordSpan(
                    record_index=r,
                    record_start=rec_start,
                    record_byte_offset=self.record_byte_offset(r),
                    channel_byte_offset=self.channel_byte_position(signal_index, r)
                    + k_lo * 2,
                    sample_index_start=k_lo,
                    sample_index_end=k_hi,
                    sample_times=times,
                    digital=digital,
                    physical=physical,
                    rejected=bool(reason),
                )
            )

        inside_gaps = [
            g for g in self.gaps if g.end_time > t_start and g.start_time < t_end
        ]
        return SegmentResult(
            signal_index=signal_index,
            label=sig.label,
            t_start=t_start,
            t_end=t_end,
            spans=spans,
            gaps_inside=inside_gaps,
            rejected=bool(reason),
            reject_reason=reason,
        )

    # ------------------------------------------------------------------
    # 诊断信息
    # ------------------------------------------------------------------
    def out_of_range_samples(
        self, signal_index: int, limit: int = 20
    ) -> Dict[str, object]:
        """扫描完整记录中超出数字标定范围的样本位置（截断文件只扫现存部分）。"""
        sig = self.header.signals[signal_index]
        if sig.is_annotation:
            return {"count": 0, "first_positions": []}
        positions: List[Dict[str, int]] = []
        total = 0
        for r in range(self.truncation.complete_records):
            for k, value in enumerate(self.read_digital(signal_index, r)):
                if value < sig.digital_min or value > sig.digital_max:
                    total += 1
                    if len(positions) < limit:
                        positions.append(
                            {
                                "record_index": r,
                                "sample_index": k,
                                "digital_value": value,
                                "byte_offset": self.channel_byte_position(
                                    signal_index, r
                                )
                                + k * 2,
                            }
                        )
        return {"count": total, "first_positions": positions}
