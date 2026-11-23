"""TAL 注释解析与构造的单元测试（含 pyedflib 实测字节布局）。"""

import pytest

from app.edf_format import (
    TAL_ANNOTATION_SEPARATOR,
    TAL_DURATION_SEPARATOR,
    TAL_TERMINATOR,
    build_tal,
    parse_tal_block,
)


def test_build_timekeeping_matches_pyedflib_layout():
    assert build_tal(0.0, 0.0, []) == b"+0" + bytes(
        [TAL_DURATION_SEPARATOR, TAL_DURATION_SEPARATOR, TAL_TERMINATOR]
    )


def test_build_instant_event_has_no_0x15():
    tal = build_tal(1.3, 0.0, ["Stimulus A"])
    assert TAL_ANNOTATION_SEPARATOR not in tal  # 瞬时事件不出现 0x15
    assert tal == b"+1.3" + bytes([TAL_DURATION_SEPARATOR]) + b"Stimulus A" + bytes(
        [TAL_DURATION_SEPARATOR, TAL_TERMINATOR]
    )


def test_build_duration_event_ordering():
    tal = build_tal(3.5, 2.4, ["Synthetic segment"])
    # +onset \x15 duration \x14 text \x14 \x00
    assert tal.startswith(b"+3.5" + bytes([TAL_ANNOTATION_SEPARATOR]) + b"2.4")
    assert tal.endswith(b"Synthetic segment" + bytes(
        [TAL_DURATION_SEPARATOR, TAL_TERMINATOR]
    ))


def test_round_trip_instant():
    tal = build_tal(1.3, 0.0, ["Stimulus A"])
    parsed = parse_tal_block(tal, 0, 0)
    assert len(parsed) == 1
    assert parsed[0].onset == pytest.approx(1.3)
    assert parsed[0].duration == 0.0
    assert parsed[0].text == "Stimulus A"


def test_round_trip_duration():
    tal = build_tal(3.5, 2.4, ["Synthetic segment"])
    parsed = parse_tal_block(tal, 2, 100)
    assert parsed[0].onset == pytest.approx(3.5)
    assert parsed[0].duration == pytest.approx(2.4)
    assert parsed[0].text == "Synthetic segment"
    assert parsed[0].record_index == 2
    assert parsed[0].byte_offset == 100


def test_round_trip_timekeeping_empty_text():
    tal = build_tal(6.0, 0.0, [])
    parsed = parse_tal_block(tal, 3, 0)
    assert parsed[0].onset == pytest.approx(6.0)
    assert parsed[0].duration == 0.0
    assert parsed[0].text == ""  # 纯时间保持 TAL


def test_parse_pyedflib_actual_bytes():
    # 直接取自 pyedflib 写出文件的真实字节序列。
    instant = b"+1.3000" + bytes([TAL_DURATION_SEPARATOR]) + b"Stimulus A" + bytes(
        [TAL_DURATION_SEPARATOR, TAL_TERMINATOR]
    )
    duration = (
        b"+3.5000" + bytes([TAL_ANNOTATION_SEPARATOR]) + b"2.4000"
        + bytes([TAL_DURATION_SEPARATOR]) + b"Synthetic segment"
        + bytes([TAL_DURATION_SEPARATOR, TAL_TERMINATOR])
    )
    p1 = parse_tal_block(instant, 1, 0)[0]
    p2 = parse_tal_block(duration, 0, 0)[0]
    assert (p1.onset, p1.duration, p1.text) == (1.3, 0.0, "Stimulus A")
    assert p2.onset == pytest.approx(3.5)
    assert p2.duration == pytest.approx(2.4)
    assert p2.text == "Synthetic segment"


def test_negative_onset_allowed():
    tal = build_tal(-1.5, 0.0, ["Before start"])
    parsed = parse_tal_block(tal, 0, 0)[0]
    assert parsed.onset == pytest.approx(-1.5)
    assert parsed.text == "Before start"


def test_negative_duration_rejected():
    bad = b"+1.0" + bytes([TAL_ANNOTATION_SEPARATOR]) + b"-0.5" + bytes(
        [TAL_DURATION_SEPARATOR, TAL_TERMINATOR]
    )
    with pytest.raises(Exception):
        parse_tal_block(bad, 0, 0)


def test_block_with_multiple_tals_and_padding():
    block = (
        build_tal(0.0, 0.0, [])
        + build_tal(1.3, 0.0, ["Stimulus A"])
        + bytes(20)  # 0x00 填充
    )
    parsed = parse_tal_block(block, 1, 500)
    assert len(parsed) == 2
    assert parsed[0].text == ""
    assert parsed[1].text == "Stimulus A"
    assert parsed[1].byte_offset >= 500
