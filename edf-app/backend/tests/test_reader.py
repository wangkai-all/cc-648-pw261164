"""自实现 EDFReader 的关键保证测试：独立采样、缺口、截断、标定拒收。"""

import os

import pyedflib
import pytest

from app.edf_format import calibration_problem
from app.edf_reader import EDFReader


def path(sample_dir, name):
    return os.path.join(sample_dir, name)


# ---------------------------------------------------------------------------
# 头部与多采样率
# ---------------------------------------------------------------------------
def test_distinct_sample_rates_not_aligned(sample_dir):
    r = EDFReader(path(sample_dir, "continuous_plusc.edf"))
    rates = {s.label: s.num_samples for s in r.header.signals
             if not s.is_annotation}
    assert rates == {"EEG Synth": 100, "ECG Synth": 250,
                     "Temp Synth": 1, "Resp Synth": 20}
    # 同一 1 秒窗，各通道按自身采样位置取到不同数量样本。
    counts = {}
    for si, s in enumerate(r.header.signals):
        if s.is_annotation:
            continue
        seg = r.read_segment(si, 1.0, 2.0)
        counts[s.label] = seg.total_samples
    assert counts == {"EEG Synth": 100, "ECG Synth": 250,
                      "Temp Synth": 1, "Resp Synth": 20}


def test_physical_conversion_matches_formula(sample_dir):
    r = EDFReader(path(sample_dir, "continuous_plusc.edf"))
    sig = r.header.signals[0]
    seg = r.read_segment(0, 0.0, 1.0)
    sp = seg.spans[0]
    gain = (sig.physical_max - sig.physical_min) / (
        sig.digital_max - sig.digital_min
    )
    expected = (sp.digital[0] - sig.digital_min) * gain + sig.physical_min
    assert sp.physical[0] == pytest.approx(expected, rel=1e-9)


def test_pyedflib_and_custom_physical_agree(sample_dir):
    """连续完整文件：自实现换算与 pyedflib 的物理值一致。"""
    p = path(sample_dir, "continuous_plusc.edf")
    r = EDFReader(p)
    seg = r.read_segment(1, 0.0, 2.0)  # ECG
    with pyedflib.EdfReader(p) as f:
        ref = f.readSignal(1, 0, 500)
    got = [x for sp in seg.spans for x in sp.physical]
    assert len(got) == len(ref)
    for a, b in zip(got, ref):
        assert a == pytest.approx(b, abs=1e-9)


# ---------------------------------------------------------------------------
# EDF+D 间断
# ---------------------------------------------------------------------------
def test_plusd_record_starts_have_gaps(sample_dir):
    r = EDFReader(path(sample_dir, "discontinuous_plusd.edf"))
    assert r.is_discontinuous
    assert r.record_starts == [0.0, 1.0, 2.0, 6.0, 7.0, 13.0, 14.0, 15.0]
    assert len(r.gaps) == 2
    g0, g1 = r.gaps
    assert (g0.start_time, g0.end_time, g0.duration) == (3.0, 6.0, 3.0)
    assert (g1.start_time, g1.end_time, g1.duration) == (8.0, 13.0, 5.0)


def test_plusd_gap_not_bridged(sample_dir):
    """缺口两侧样本的时间戳保留间隔，不能被连成连续信号。"""
    r = EDFReader(path(sample_dir, "discontinuous_plusd.edf"))
    seg = r.read_segment(0, 1.5, 7.5)
    # 覆盖记录 1,2,3,4；记录 2 末与记录 3 首之间隔着 3.0-6.0 缺口。
    records = [sp.record_index for sp in seg.spans]
    assert records == [1, 2, 3, 4]
    last_before = seg.spans[1].sample_times[-1]   # 记录 2 末（约 2.99）
    first_after = seg.spans[2].sample_times[0]    # 记录 3 首（6.0）
    assert first_after - last_before > 3.0
    assert any(g.after_record == 2 for g in seg.gaps_inside)


def test_plusd_cross_gap_annotation_preserved(sample_dir):
    r = EDFReader(path(sample_dir, "discontinuous_plusd.edf"))
    cross = [e for e in r.events if e.text == "Cross-gap segment"][0]
    # 2.5 起、持续 4s，跨过 3.0-6.0 缺口到 6.5，原样保留不拆分。
    assert cross.onset == 2.5
    assert cross.duration == 4.0
    assert cross.end == 6.5


# ---------------------------------------------------------------------------
# 截断
# ---------------------------------------------------------------------------
def test_truncation_reported_not_globally_rejected(sample_dir):
    r = EDFReader(path(sample_dir, "truncated_plusc.edf"))
    t = r.truncation
    assert t.header_declared_records == 10
    assert t.complete_records == 6
    assert t.partial_record_bytes == 40
    assert t.missing_records == 4
    # 现存 6 条仍可正常读取。
    seg = r.read_segment(0, 0.0, 6.0)
    assert sum(len(sp.digital) for sp in seg.spans) == 600
    # 尾部不完整记录不可被当作完整记录读取。
    with pytest.raises(Exception):
        r.read_digital(0, 6)


# ---------------------------------------------------------------------------
# 标定拒收（按通道单独）
# ---------------------------------------------------------------------------
def test_bad_calibration_channel_rejected_others_ok(sample_dir):
    r = EDFReader(path(sample_dir, "bad_calibration_plusc.edf"))
    temp = r.header.signals[2]
    eeg = r.header.signals[0]
    assert calibration_problem(temp) is not None
    assert calibration_problem(eeg) is None
    bad_seg = r.read_segment(2, 0.0, 2.0)
    good_seg = r.read_segment(0, 0.0, 1.0)
    assert bad_seg.rejected and bad_seg.spans[0].physical == []
    assert bad_seg.total_samples > 0  # 数字值仍可读
    assert not good_seg.rejected and good_seg.spans[0].physical


def test_out_of_range_detection(sample_dir):
    r = EDFReader(path(sample_dir, "continuous_plusc.edf"))
    result = r.out_of_range_samples(0)
    assert result["count"] == 0  # 合成信号量化后均在标定范围内


# ---------------------------------------------------------------------------
# 字节定位
# ---------------------------------------------------------------------------
def test_channel_byte_offsets_are_distinct(sample_dir):
    r = EDFReader(path(sample_dir, "continuous_plusc.edf"))
    offsets = [r.channel_byte_position(si, 0)
               for si in range(len(r.header.signals))]
    assert len(set(offsets)) == len(offsets)  # 各通道在记录内偏移唯一
