"""合成 EDF / EDF+ 文件写入器。

只用于生成演示记录与测试夹具：信号全部是确定性合成波形（正弦叠加 +
慢漂移），不包含任何真实生理数据，应用也只做测量数据整理、不做生理结论。

覆盖的格式子集与 :mod:`app.edf_format` 声明一致：
连续 EDF、EDF+C、EDF+D（含 time-keeping TAL 形成的时间缺口）。
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

from .edf_format import (
    ANNOTATION_LABEL,
    EDFFormatError,
    build_tal,
    pad_annotation_block,
)

MONTH_ABBR = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)


def _field(text: str, width: int, right: bool = False) -> bytes:
    text = text[:width]
    if right:
        out = text.rjust(width)
    else:
        out = text.ljust(width)
    return out.encode("ascii", errors="replace")


@dataclass
class SignalSpec:
    label: str
    transducer: str
    physical_dim: str
    physical_min: float
    physical_max: float
    digital_min: int
    digital_max: int
    prefilter: str
    num_samples: int          # 每数据记录样本数（各通道可不同）

    def quantize(self, physical: Sequence[float]) -> List[int]:
        """按本通道标定把物理值量化回数字值（夹在数字上下限内）。"""
        span = self.physical_max - self.physical_min
        if span == 0:
            # 坏标定样例：无法建立线性映射，写入固定数字值；
            # 读取端会把该通道标为标定拒收，物理值不做换算。
            return [0] * len(physical)
        gain = span / (self.digital_max - self.digital_min)
        out: List[int] = []
        for value in physical:
            digital = round((value - self.physical_min) / gain + self.digital_min)
            out.append(max(self.digital_min, min(self.digital_max, digital)))
        return out


class EDFWriter:
    """顺序写入 EDF；记录数在头部预先声明，允许提前关闭制造截断文件。"""

    def __init__(
        self,
        path: str,
        start: datetime,
        record_duration: float,
        signals: Sequence[SignalSpec],
        num_records: int,
        variant: str = "EDF",
        patient: str = "X X X X",
        recording_suffix: str = "X X X",
    ):
        if variant not in ("EDF", "EDF+C", "EDF+D"):
            raise EDFFormatError(f"不支持的变体：{variant}")
        self.path = path
        self.start = start
        self.record_duration = record_duration
        self.signals = list(signals)
        self.num_records = num_records
        self.variant = variant
        self.annotation_index: Optional[int] = next(
            (i for i, s in enumerate(self.signals) if s.label == ANNOTATION_LABEL),
            None,
        )
        self._fp = open(path, "wb")
        self._fp.write(self._build_header(patient, recording_suffix))
        self.records_written = 0

    # ------------------------------------------------------------------
    def _build_header(self, patient: str, recording_suffix: str) -> bytes:
        n = len(self.signals)
        startdate = f"{self.start.day:02d}-{MONTH_ABBR[self.start.month - 1]}-{self.start.year}"
        fixed = b"".join([
            _field("0", 8),
            _field(patient, 80),
            _field(f"Startdate {startdate} {recording_suffix}", 80),
            # 16 字符定长：dd.mm.yy（8）+ HH.MM.SS（8），无分隔空格，
            # 与 pyedflib 写出的 EDF 参考文件布局一致。
            _field(self.start.strftime("%d.%m.%y%H.%M.%S"), 16),
            _field(str(256 + 256 * n), 8),
            _field(self.variant if self.variant != "EDF" else " " * 44, 44),
            _field(str(self.num_records), 8),
            _field(f"{self.record_duration:g}", 8),
            _field(str(n), 4),
        ])
        assert len(fixed) == 256

        def signal_column(attr: str, width: int) -> bytes:
            # EDF 规范的 ASCII 数字字段一律左对齐、尾部空格填充
            # （pyedflib/edflib 的严格校验拒绝前导空格的数字）。
            return b"".join(_field(str(getattr(s, attr)), width) for s in self.signals)

        header = fixed + b"".join([
            signal_column("label", 16),
            signal_column("transducer", 80),
            signal_column("physical_dim", 8),
            signal_column("physical_min", 8),
            signal_column("physical_max", 8),
            signal_column("digital_min", 8),
            signal_column("digital_max", 8),
            signal_column("prefilter", 80),
            signal_column("num_samples", 8),
            _field(" ", 32) * len(self.signals),
        ])
        assert len(header) == 256 + 256 * n
        return header

    # ------------------------------------------------------------------
    def write_record(
        self,
        digital_by_signal: Sequence[Sequence[int]],
        tals: bytes = b"",
    ) -> None:
        if len(digital_by_signal) != len(self.signals):
            raise EDFFormatError("写入通道数与头部声明不一致")
        chunks: List[bytes] = []
        for i, (spec, values) in enumerate(zip(self.signals, digital_by_signal)):
            if i == self.annotation_index:
                chunks.append(pad_annotation_block(tals, spec.num_samples * 2))
                continue
            if len(values) != spec.num_samples:
                raise EDFFormatError(
                    f"通道 {spec.label} 记录样本数 {len(values)} "
                    f"!= 声明 {spec.num_samples}"
                )
            chunks.append(struct.pack("<%dh" % spec.num_samples, *values))
        self._fp.write(b"".join(chunks))
        self.records_written += 1

    def write_partial(self, raw: bytes) -> None:
        """写入不足一条记录的残片（截断样例专用）。"""
        self._fp.write(raw)

    def close(self) -> None:
        self._fp.close()

    def __enter__(self) -> "EDFWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# 合成波形（确定性，纯数学信号）
# ---------------------------------------------------------------------------
def synth_eeg(t: float) -> float:
    """30 uV 的 10 Hz 正弦 + 8 uV 慢漂移。"""
    return 30.0 * math.sin(2 * math.pi * 10.0 * t) + 8.0 * math.sin(
        2 * math.pi * 0.5 * t
    )


def synth_ecg(t: float) -> float:
    """类周期合成波形（1.2 Hz 基波 + 5 Hz 谐波），单位 mV。"""
    return 0.6 * math.sin(2 * math.pi * 1.2 * t) + 0.15 * math.sin(
        2 * math.pi * 5.0 * t
    )


def synth_temp(t: float) -> float:
    """36.8 C 基线 + 60 秒周期慢变。"""
    return 36.8 + 0.3 * math.sin(2 * math.pi * t / 60.0)


def synth_resp(t: float) -> float:
    """0.25 Hz 呼吸样正弦，单位 V。"""
    return 0.5 * math.sin(2 * math.pi * 0.25 * t)


SYNTH_FUNCTIONS = {
    "EEG Synth": synth_eeg,
    "ECG Synth": synth_ecg,
    "Temp Synth": synth_temp,
    "Resp Synth": synth_resp,
}


def standard_signal_specs(bad_calibration: bool = False) -> List[SignalSpec]:
    """四种不同采样率的通道：100 / 250 / 1 / 20 Hz（记录时长 1 s）。"""
    specs = [
        SignalSpec("EEG Synth", "Synthetic electrode", "uV", -100.0, 100.0,
                   -32768, 32767, "HP:0.5Hz LP:40Hz", 100),
        SignalSpec("ECG Synth", "Synthetic electrode", "mV", -1.0, 1.0,
                   -32768, 32767, "HP:0.05Hz LP:100Hz", 250),
        SignalSpec("Temp Synth", "Synthetic thermistor", "Celsius", 35.0, 40.0,
                   -32768, 32767, "LP:0.1Hz", 1),
        SignalSpec("Resp Synth", "Synthetic piezo", "V", -1.0, 1.0,
                   -32768, 32767, "LP:5Hz", 20),
    ]
    if bad_calibration:
        # 物理上下限相等 -> 标定无法建立线性映射，该通道必须被单独拒收。
        specs[2] = SignalSpec("Temp Synth", "Synthetic thermistor", "Celsius",
                              37.0, 37.0, -32768, 32767, "LP:0.1Hz", 1)
    return specs


def annotation_spec(samples_per_record: int = 64) -> SignalSpec:
    # EDF+ 注释通道规范：物理维度留空，物理上下限 -1/1，数字上下限 -32768/32767
    # （与 pyedflib/edflib 写出的参考文件一致）。
    return SignalSpec(
        ANNOTATION_LABEL, "", "", -1.0, 1.0, -32768, 32767,
        "", samples_per_record,
    )


def render_record(specs: Sequence[SignalSpec], record_start: float) -> List[List[int]]:
    """按各通道自己的采样位置渲染一条记录的数字样本。"""
    out: List[List[int]] = []
    for spec in specs:
        if spec.label == ANNOTATION_LABEL:
            out.append([])  # 注释通道由 TAL 填充
            continue
        fn = SYNTH_FUNCTIONS[spec.label]
        period = 1.0 / spec.num_samples  # 记录时长固定 1 s
        values = [
            fn(record_start + k * period) for k in range(spec.num_samples)
        ]
        out.append(spec.quantize(values))
    return out
