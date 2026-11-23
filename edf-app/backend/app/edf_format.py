"""EDF / EDF+ 底层格式常量与纯函数解析。

本模块只做与字节布局直接相关的工作，不选择读取引擎、不做业务判断：

* 固定头部（256 字节）与每个信号头部（每信号 256 字节）的切片；
* 小端 16 bit 数据记录样本解码；
* EDF+ 注释通道（TAL, Time-stamped Annotation Lists）的解析与构造。

参考：EGI/EEG 社区维护的 EDF/EDF+ 规范
(https://edfplus.info/specs/edfplus.html)。

支持的格式子集（应用全局只承诺该子集，见 README）：
  * EDF（连续）、EDF+C（连续，含注释通道）、EDF+D（间断，含注释通道）；
  * 每数据记录 16 bit 小端整数，单文件信号数 <= 9999；
  * 注释通道 label 为 "EDF Annotations"，记录内 TAL 以 0x00 分隔，
    时间戳为纯数字 + 可选 ``+/-<duration>``，不支持 ``hh.mm.ss`` 写法；
  * 不支持 BDF（24 bit）、非 ASCII 头部、压缩/加密 EDF。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 固定头部布局（共 256 字节）。每个元素：(字段名, 字节长度)。
# ---------------------------------------------------------------------------
FIXED_FIELDS: Sequence[Tuple[str, int]] = (
    ("version", 8),
    ("patient", 80),
    ("recording", 80),
    ("start_date", 16),
    ("header_bytes", 8),
    ("reserved", 44),
    ("num_records", 8),
    ("record_duration", 8),
    ("num_signals", 4),
)
FIXED_HEADER_SIZE = 256

# 每个信号头部字段（每信号 256 字节，字段长度需乘以信号数）。
SIGNAL_FIELDS: Sequence[Tuple[str, int]] = (
    ("label", 16),
    ("transducer", 80),
    ("physical_dim", 8),
    ("physical_min", 8),
    ("physical_max", 8),
    ("digital_min", 8),
    ("digital_max", 8),
    ("prefilter", 80),
    ("num_samples", 8),
    ("reserved", 32),
)
SIGNAL_HEADER_SIZE = 256

ANNOTATION_LABEL = "EDF Annotations"
# EDF+ TAL 分隔符（按 edfplus.info 规范，与 pyedflib 实测输出一致）：
#   0x14 (DC4) 分隔 onset 与 duration；
#   0x15 (NAK) 分隔时间字段与注释文本（多条注释各用一个 0x15）。
# 零时长/无文本时对应字段留空，产生相邻分隔符（如 +0\\x14\\x14\\x00）。
TAL_DURATION_SEPARATOR = 0x14
TAL_ANNOTATION_SEPARATOR = 0x15
TAL_TERMINATOR = 0x00


class EDFFormatError(ValueError):
    """文件不符合所支持的 EDF 子集时抛出。"""


@dataclass
class FixedHeader:
    version: str
    patient: str
    recording: str
    start_date_raw: str
    header_bytes: int
    reserved: str
    num_records: int          # 头部声明的数据记录数（-1 表示未知）
    record_duration: float    # 单记录持续时间（秒）
    num_signals: int
    start_datetime: Optional[datetime]


@dataclass
class SignalHeader:
    index: int
    label: str
    transducer: str
    physical_dim: str
    physical_min: float
    physical_max: float
    digital_min: int
    digital_max: int
    prefilter: str
    num_samples: int          # 该信号每数据记录的样本数（各通道可不同）
    reserved: str
    is_annotation: bool


@dataclass
class EDFHeader:
    fixed: FixedHeader
    signals: List[SignalHeader]
    annotation_signal_index: Optional[int]
    file_size: int

    @property
    def data_record_size(self) -> int:
        """一条数据记录的字节数 = sum(各通道样本数 * 2)。"""
        return sum(s.num_samples * 2 for s in self.signals)


def _ascii(raw: bytes) -> str:
    # EDF 头部一律 ASCII，尾部空格为填充；非 ASCII 字节按替换字符处理，
    # 不抛异常，以便上层把问题记录到处理报告而不是直接崩溃。
    return raw.decode("ascii", errors="replace").strip()


def parse_start_datetime(raw: str) -> Optional[datetime]:
    """解析 EDF 固定头部的起始时间字段。

    两种 16 字符布局都接受：规范常见的 ``dd.mm.yy HH.MM.SS``（空格分隔，
    实际占 16 字符时通常是紧凑形式）以及 pyedflib 写出的
    ``dd.mm.yyHH.MM.SS`` 无空格紧凑形式。
    """
    digits = raw.replace(" ", "")
    if len(digits) < 12:
        return None
    try:
        dd = int(digits[0:2])
        mm = int(digits[3:5])
        yy = int(digits[6:8])
        hh = int(digits[8:10])
        mi = int(digits[11:13])
        ss = int(digits[14:16]) if len(digits) >= 16 else 0
    except ValueError:
        return None
    # EDF 两位年份：85-99 -> 19xx，00-84 -> 20xx（EDF+ 规范约定）。
    year = 2000 + yy if yy < 85 else 1900 + yy
    try:
        return datetime(year, mm, dd, hh, mi, ss)
    except ValueError:
        return None


def parse_header(buf: bytes, file_size: int) -> EDFHeader:
    """从字节缓冲解析完整头部（固定头 + 信号头），不触碰数据记录。"""
    if len(buf) < FIXED_HEADER_SIZE:
        raise EDFFormatError(
            f"文件仅 {len(buf)} 字节，小于 EDF 固定头部 256 字节"
        )
    offset = 0
    values = {}
    for name, width in FIXED_FIELDS:
        values[name] = _ascii(buf[offset:offset + width])
        offset += width

    try:
        header_bytes = int(values["header_bytes"])
        num_signals = int(values["num_signals"])
    except ValueError as exc:
        raise EDFFormatError(f"头部整数字段无法解析：{exc}") from exc
    if not 1 <= num_signals <= 9999:
        raise EDFFormatError(f"信号数 {num_signals} 超出 1..9999")
    expected_header = FIXED_HEADER_SIZE + SIGNAL_HEADER_SIZE * num_signals
    if header_bytes != expected_header:
        raise EDFFormatError(
            f"头部声明 {header_bytes} 字节，与 {num_signals} 个信号所需的 "
            f"{expected_header} 字节不一致"
        )
    if len(buf) < header_bytes:
        raise EDFFormatError(
            f"文件在信号头部处截断：需要 {header_bytes} 字节，"
            f"实际 {len(buf)} 字节"
        )
    try:
        num_records = int(values["num_records"])
    except ValueError:
        num_records = -1  # EDF 允许 -1 表示记录数未知
    try:
        record_duration = float(values["record_duration"])
    except ValueError as exc:
        raise EDFFormatError(f"记录持续时间无法解析：{exc}") from exc
    if record_duration <= 0:
        raise EDFFormatError(f"记录持续时间必须为正数，得到 {record_duration}")

    fixed = FixedHeader(
        version=values["version"],
        patient=values["patient"],
        recording=values["recording"],
        start_date_raw=values["start_date"],
        header_bytes=header_bytes,
        reserved=values["reserved"],
        num_records=num_records,
        record_duration=record_duration,
        num_signals=num_signals,
        start_datetime=parse_start_datetime(values["start_date"]),
    )

    signals: List[SignalHeader] = []
    ann_index: Optional[int] = None
    # 信号头按「字段交织」存储：先全部 label，再全部 transducer……
    field_offsets = {}
    cursor = FIXED_HEADER_SIZE
    for name, width in SIGNAL_FIELDS:
        field_offsets[name] = (cursor, width)
        cursor += width * num_signals

    for i in range(num_signals):
        row = {}
        for name, width in SIGNAL_FIELDS:
            base, w = field_offsets[name]
            row[name] = _ascii(buf[base + i * w: base + (i + 1) * w])
        try:
            phys_min = float(row["physical_min"])
            phys_max = float(row["physical_max"])
            dig_min = int(row["digital_min"])
            dig_max = int(row["digital_max"])
            n_samples = int(row["num_samples"])
        except ValueError as exc:
            raise EDFFormatError(
                f"信号 {i}（{row['label']}）头部数值字段无法解析：{exc}"
            ) from exc
        if n_samples <= 0:
            raise EDFFormatError(
                f"信号 {i}（{row['label']}）每记录样本数必须为正数"
            )
        is_ann = row["label"] == ANNOTATION_LABEL
        if is_ann:
            if ann_index is not None:
                raise EDFFormatError("存在多个 EDF Annotations 通道")
            ann_index = i
        signals.append(
            SignalHeader(
                index=i,
                label=row["label"],
                transducer=row["transducer"],
                physical_dim=row["physical_dim"],
                physical_min=phys_min,
                physical_max=phys_max,
                digital_min=dig_min,
                digital_max=dig_max,
                prefilter=row["prefilter"],
                num_samples=n_samples,
                reserved=row["reserved"],
                is_annotation=is_ann,
            )
        )

    return EDFHeader(
        fixed=fixed,
        signals=signals,
        annotation_signal_index=ann_index,
        file_size=file_size,
    )


# ---------------------------------------------------------------------------
# 标定换算
# ---------------------------------------------------------------------------
@dataclass
class Calibration:
    gain: float
    offset: float

    def to_physical(self, digital: float) -> float:
        return digital * self.gain + self.offset


def build_calibration(sig: SignalHeader) -> Calibration:
    """按 EDF 规范把数字值线性换算为物理值。

    physical = (digital - digital_min) *
        (physical_max - physical_min) / (digital_max - digital_min)
        + physical_min
    """
    gain = (sig.physical_max - sig.physical_min) / (
        sig.digital_max - sig.digital_min
    )
    offset = sig.physical_min - sig.digital_min * gain
    return Calibration(gain=gain, offset=offset)


def calibration_problem(sig: SignalHeader) -> Optional[str]:
    """返回标定被拒收的原因；返回 None 表示该通道标定有效。

    注释通道不参与物理量换算，单独放行。
    """
    if sig.is_annotation:
        return None
    if sig.digital_max <= sig.digital_min:
        return (
            f"数字上下限无效：digital_max={sig.digital_max} "
            f"<= digital_min={sig.digital_min}"
        )
    if sig.physical_max <= sig.physical_min:
        return (
            f"物理上下限无效：physical_max={sig.physical_max} "
            f"<= physical_min={sig.physical_min}"
        )
    return None


# ---------------------------------------------------------------------------
# TAL（EDF+ 注释）解析与构造
# ---------------------------------------------------------------------------
@dataclass
class TalAnnotation:
    onset: float                 # 相对记录起点的秒数（文件语义时间）
    duration: float              # 0 表示瞬时事件
    text: str                    # 空字符串表示该 TAL 只用于时间保持
    record_index: int = -1       # 所在数据记录序号（解析时回填）
    byte_offset: int = -1        # TAL 首字节在整个文件中的偏移


def parse_tal_block(
    block: bytes, record_index: int, record_byte_offset: int
) -> List[TalAnnotation]:
    """解析一条注释通道数据记录中的全部 TAL。

    以 edfplus.info 规范及 pyedflib 实测字节为准（每个 TAL 以 0x00 结尾）::

        time-keeping : +onset \\x14 \\x14 \\x00
        瞬时事件     : +onset \\x14 \\x15 text \\x14 \\x00
        持续事件     : +onset \\x14 duration \\x15 text \\x14 \\x00

    规则：第一个 0x14 结束 onset；其后若紧跟 0x15 则 duration 为空（瞬时），
    否则到 0x15 前是 duration；0x15 之后是文本，文本以 0x14 收尾。
    解析端对分隔符角色做容错（也接受 0x14/0x15 互换的非标准排列）。
    """
    results: List[TalAnnotation] = []
    pos = 0
    n = len(block)
    while pos < n:
        if block[pos] == TAL_TERMINATOR:
            pos += 1
            continue
        end = block.find(TAL_TERMINATOR, pos)
        if end == -1:
            raise EDFFormatError(
                f"记录 {record_index} 的 TAL 缺少 0x00 终止符"
            )
        tal_start = pos
        tal_bytes = block[pos:end]

        # 字节文法（综合 pyedflib 实测）：
        #   +onset [\x15 duration] \x14 [text \x14]
        # 即 onset 后首个分隔符：0x15 表示其后到下一个 0x14 是 duration；
        # 直接是 0x14 表示无 duration（瞬时/time-keeping）。
        first_sep = -1
        for i, b in enumerate(tal_bytes):
            if b in (TAL_DURATION_SEPARATOR, TAL_ANNOTATION_SEPARATOR):
                first_sep = i
                break
        if first_sep == -1:
            raise EDFFormatError(f"TAL 缺少分隔符：{tal_bytes!r}")
        onset_b = tal_bytes[:first_sep]
        if tal_bytes[first_sep] == TAL_ANNOTATION_SEPARATOR:
            p14 = tal_bytes.find(TAL_DURATION_SEPARATOR, first_sep)
            if p14 == -1:
                raise EDFFormatError(f"持续事件缺少 0x14：{tal_bytes!r}")
            duration_b = tal_bytes[first_sep + 1:p14]
            text_region = tal_bytes[p14 + 1:]
        else:
            duration_b = b""
            text_region = tal_bytes[first_sep + 1:]
        # 文本区以收尾 0x14 结束；多条注释以 0x14/0x15 分隔。
        text_region = text_region.rstrip(
            bytes([TAL_DURATION_SEPARATOR, TAL_ANNOTATION_SEPARATOR])
        )
        try:
            onset = float(onset_b.decode("ascii"))
            duration = (
                float(duration_b.decode("ascii")) if duration_b.strip() else 0.0
            )
        except ValueError as exc:
            raise EDFFormatError(f"TAL 时间戳无法解析：{tal_bytes!r}") from exc
        if duration < 0:
            raise EDFFormatError(f"TAL 持续时间为负：{tal_bytes!r}")
        # 把两类分隔符统一后再切分文本（split 多字节只匹配连续序列，故先替换）。
        normalized = text_region.replace(
            bytes([TAL_ANNOTATION_SEPARATOR]),
            bytes([TAL_DURATION_SEPARATOR]),
        )
        texts = [
            p.decode("ascii", errors="replace").strip()
            for p in normalized.split(bytes([TAL_DURATION_SEPARATOR]))
        ] if normalized else []
        texts = [t for t in texts if t]
        if not texts:
            # 无文本条目：EDF+ 用这种 TAL 标记记录真实起点（时间保持）。
            texts = [""]
        for text in texts:
            results.append(
                TalAnnotation(
                    onset=onset,
                    duration=duration,
                    text=text,
                    record_index=record_index,
                    byte_offset=record_byte_offset + tal_start,
                )
            )
        pos = end + 1
    return results


def format_tal_time(value: float) -> str:
    """EDF+ 时间戳写法：显式正负号，去掉浮点多余的 0。"""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    if not text.startswith(("+", "-")):
        text = ("+" if value >= 0 else "-") + text
    return text


def build_tal(onset: float, duration: float, texts: Sequence[str]) -> bytes:
    """构造单个 TAL（含结尾 0x00），字节布局与 pyedflib 实测一致。

    edfplus 标准：0x14 分隔 onset/duration 与时间/文本，0x15 仅在存在
    duration 时出现，位于 duration 与文本之间。

    无文本（time-keeping）：``+onset\\x14\\x14\\x00``；
    瞬时事件：``+onset\\x14text\\x14\\x00``（无 duration，不出现 0x15）；
    持续事件：``+onset\\x14duration\\x15text\\x14\\x00``。
    """
    out = format_tal_time(onset).encode("ascii")
    if duration > 0:
        # 持续事件：+onset \x15 duration \x14 text \x14 \x00
        out += bytes([TAL_ANNOTATION_SEPARATOR])
        out += format_tal_time(duration).lstrip("+").encode("ascii")
        out += bytes([TAL_DURATION_SEPARATOR])
    else:
        # 无 duration：onset 后直接 \x14
        out += bytes([TAL_DURATION_SEPARATOR])
    if texts:
        # 每条注释文本以 0x14 收尾。
        for text in texts:
            out += text.encode("ascii", errors="replace")
            out += bytes([TAL_DURATION_SEPARATOR])
    else:
        # 无文本 time-keeping：再补一个 0x14 形成 +onset\x14\x14。
        out += bytes([TAL_DURATION_SEPARATOR])
    out += bytes([TAL_TERMINATOR])
    return out


def pad_annotation_block(tals: bytes, capacity: int) -> bytes:
    """把若干 TAL 填充到注释通道单记录容量（字节），超容则报错。"""
    if len(tals) > capacity:
        raise EDFFormatError(
            f"注释数据 {len(tals)} 字节超过通道容量 {capacity} 字节"
        )
    return tals + bytes(capacity - len(tals))


def decode_digital_samples(raw: bytes) -> "list[int]":
    """解码一条数据记录内某通道的小端 16 bit 整数样本。"""
    if len(raw) % 2 != 0:
        raise EDFFormatError("数字样本字节数不是 2 的倍数")
    return list(struct.unpack("<%dh" % (len(raw) // 2), raw))
