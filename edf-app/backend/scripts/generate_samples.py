#!/usr/bin/env python3
"""生成全套合成 EDF 演示记录（运行：python -m scripts.generate_samples）。

生成文件（全部为合成信号，无真实生理数据）：

  1. continuous_plain.edf     连续 EDF，4 通道、4 种采样率，无注释通道
  2. continuous_plusc.edf     连续 EDF+C，含瞬时/持续/跨记录注释
  3. discontinuous_plusd.edf  EDF+D，记录 2、5 后各有时间缺口，含跨缺口注释
  4. truncated_plusc.edf      EDF+C 截断：头部声明 10 条记录，仅完整写入 6 条
                              外加半条残片
  5. bad_calibration_plusc.edf
                              EDF+C，温度通道物理上下限相等（标定拒收），
                              其余通道正常

每个文件的精确构造（记录序号、时间轴、注释字节偏移）在生成后打印，
供人工核对应用报告中的处理位置。
"""

from __future__ import annotations

import os
from datetime import datetime

from app.edf_format import build_tal
from app.edf_writer import (
    EDFWriter,
    annotation_spec,
    render_record,
    standard_signal_specs,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(os.path.dirname(HERE), "samples")
START = datetime(2026, 11, 23, 9, 0, 0)


def _write_continuous(path: str, variant: str, num_records: int) -> None:
    specs = standard_signal_specs()
    if variant != "EDF":
        specs = specs + [annotation_spec()]
    with EDFWriter(path, START, 1.0, specs, num_records, variant=variant) as w:
        for r in range(num_records):
            record = render_record(specs, float(r))
            tals = b""
            if variant != "EDF":
                # 每条记录首条 TAL 为 time-keeping：onset = 记录起点。
                tals += build_tal(float(r), 0.0, [])
                if r == 1:
                    tals += build_tal(1.30, 0.0, ["Stimulus A"])
                if r == 3:
                    # 持续 2.4 s：跨过记录 3、4 的边界（跨记录注释）。
                    tals += build_tal(3.50, 2.40, ["Synthetic segment"])
                if r == 7:
                    tals += build_tal(7.20, 0.50, ["Marker X"])
            w.write_record(record, tals)


def _write_discontinuous(path: str) -> None:
    """记录时间轴（秒）：0,1,2, [缺口 3s] ,6,7, [缺口 5s] ,13,14,15。"""
    num_records = 8
    specs = standard_signal_specs() + [annotation_spec()]
    # 每条记录在文件时间轴上的真实起点（缺口体现为相邻起点不连续）。
    record_times = [0.0, 1.0, 2.0, 6.0, 7.0, 13.0, 14.0, 15.0]
    with EDFWriter(path, START, 1.0, specs, num_records, variant="EDF+D") as w:
        for r, t0 in enumerate(record_times):
            record = render_record(specs, t0)
            tals = build_tal(t0, 0.0, [])  # time-keeping TAL
            if r == 1:
                tals += build_tal(1.50, 0.0, ["Stimulus A"])
            if r == 2:
                # 从 2.5s 持续到 6.5s：跨过第一个 3s 缺口（跨间断注释）。
                tals += build_tal(2.50, 4.00, ["Cross-gap segment"])
            if r == 5:
                # 从 13.2s 持续到 15.6s：跨过记录 5/6/7 的记录边界。
                tals += build_tal(13.20, 2.40, ["Long annotated window"])
            if r == 7:
                tals += build_tal(15.70, 0.0, ["Marker X"])
            w.write_record(record, tals)


def _write_truncated(path: str) -> None:
    num_records = 10  # 头部声明 10 条
    specs = standard_signal_specs() + [annotation_spec()]
    with EDFWriter(path, START, 1.0, specs, num_records, variant="EDF+C") as w:
        for r in range(6):  # 只完整写入 6 条
            record = render_record(specs, float(r))
            tals = build_tal(float(r), 0.0, [])
            if r == 2:
                tals += build_tal(2.10, 0.0, ["Stimulus before truncation"])
            w.write_record(record, tals)
        # 再写第 7 条记录的前 40 字节残片（EEG 通道 100 样本需 200 字节，
        # 残片甚至不够一个通道），用于验证截断定位而非整体拒收。
        import struct

        partial_record = render_record(specs, 6.0)
        w.write_partial(struct.pack("<20h", *partial_record[0][:20]))


def _write_bad_calibration(path: str) -> None:
    num_records = 8
    specs = standard_signal_specs(bad_calibration=True) + [annotation_spec()]
    with EDFWriter(path, START, 1.0, specs, num_records, variant="EDF+C") as w:
        for r in range(num_records):
            # 坏标定通道仍按 37.0 上下限写入量化值（实际写数字 0）。
            record = render_record(specs, float(r))
            tals = build_tal(float(r), 0.0, [])
            if r == 4:
                tals += build_tal(4.00, 1.00, ["Note on valid channels"])
            w.write_record(record, tals)


def main() -> None:
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    jobs = [
        ("continuous_plain.edf", lambda p: _write_continuous(p, "EDF", 10)),
        ("continuous_plusc.edf", lambda p: _write_continuous(p, "EDF+C", 10)),
        ("discontinuous_plusd.edf", _write_discontinuous),
        ("truncated_plusc.edf", _write_truncated),
        ("bad_calibration_plusc.edf", _write_bad_calibration),
    ]
    for name, fn in jobs:
        path = os.path.join(SAMPLE_DIR, name)
        fn(path)
        size = os.path.getsize(path)
        print(f"generated {name:28s} {size:8d} bytes")


if __name__ == "__main__":
    main()
