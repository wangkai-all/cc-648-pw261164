"""FastAPI 端到端测试：引擎选择、片段、导出、错误处理。"""

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_and_samples():
    assert client.get("/api/health").json() == {"status": "ok"}
    ids = [s["file_id"] for s in client.get("/api/samples").json()["samples"]]
    assert set(ids) == {
        "continuous_plain", "continuous_plusc", "discontinuous_plusd",
        "truncated", "bad_calibration",
    }


def test_engine_selection_continuous_uses_pyedflib():
    ov = client.get("/api/files/continuous_plusc/overview").json()
    assert ov["file"]["engine"] == "pyedflib"
    seg = client.get(
        "/api/files/continuous_plusc/segment",
        params={"signal_index": 0, "t_start": 0, "t_end": 2},
    ).json()
    # 连续文件必须带 pyedflib 交叉校验且样本数一致。
    cc = seg["pyedflib_crosscheck"]
    assert cc["sample_count"] == seg["total_samples"]


def test_engine_selection_plusd_uses_custom():
    ov = client.get("/api/files/discontinuous_plusd/overview").json()
    assert ov["file"]["engine"] == "custom"
    assert "EDF+D" in ov["file"]["engine_reasons"][0]
    seg = client.get(
        "/api/files/discontinuous_plusd/segment",
        params={"signal_index": 0, "t_start": 1.5, "t_end": 7.5},
    ).json()
    assert seg["pyedflib_crosscheck"] is None
    assert seg["gaps_inside"]  # 窗口内含缺口
    # 绘图序列在缺口处插入 null 断点。
    assert seg["plot"]["times"].count(None) >= 1


def test_truncated_overview_exposes_exact_position():
    ov = client.get("/api/files/truncated/overview").json()
    assert ov["file"]["engine"] == "custom"
    t = ov["truncation"]
    assert (t["complete_records"], t["partial_record_bytes"],
            t["missing_records"]) == (6, 40, 4)
    assert t["first_record_byte_offset"] == ov["file"]["header_bytes"]


def test_bad_calibration_segment_keeps_digital_drops_physical():
    seg = client.get(
        "/api/files/bad_calibration/segment",
        params={"signal_index": 2, "t_start": 0, "t_end": 2},
    ).json()
    assert seg["calibration_rejected"] is True
    assert seg["calibration_reject_reason"]
    # 绘图物理值全部为 None（不换算），数字值仍提供核对。
    non_null = [v for v in seg["plot"]["physical"] if v is not None]
    assert non_null == []
    assert seg["total_samples"] == 2
    assert len([d for d in seg["plot"]["digital"] if d is not None]) > 0


def test_segment_window_validation():
    # t_end <= t_start：Pydantic 查询校验（422）或路由内 400 都属于拒绝。
    r = client.get(
        "/api/files/continuous_plusc/segment",
        params={"signal_index": 0, "t_start": 5, "t_end": 5},
    )
    assert r.status_code in (400, 422)


def test_annotation_channel_has_no_waveform():
    # 注释通道序号 = 4（plusc 文件），请求波形应被拒。
    r = client.get(
        "/api/files/continuous_plusc/segment",
        params={"signal_index": 4, "t_start": 0, "t_end": 1},
    )
    assert r.status_code == 422


def test_events_csv_export():
    r = client.get("/api/files/discontinuous_plusd/export/events.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("onset_s,duration_s,end_s,annotation")
    assert any("Cross-gap segment" in ln for ln in lines)
    # 每行带记录序号与字节偏移（确切处理位置）。
    data_lines = [ln for ln in lines[1:] if ln]
    for ln in data_lines:
        cols = ln.split(",")
        assert cols[4].isdigit()  # record_index


def test_segment_csv_export_has_digital_and_physical():
    r = client.get(
        "/api/files/continuous_plusc/export/segment.csv",
        params={"signal_index": 2, "t_start": 0, "t_end": 3},
    )
    lines = r.text.strip().splitlines()
    assert lines[0] == (
        "time_s,record_index,sample_index_in_record,"
        "digital_value,physical_value,byte_offset"
    )
    assert len(lines) == 4  # 表头 + 3 个 1Hz 样本


def test_unknown_file_404():
    assert client.get("/api/files/nonexistent/overview").status_code == 404


def test_upload_rejects_non_edf():
    r = client.post(
        "/api/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400


def test_upload_valid_edf(sample_dir):
    p = os.path.join(sample_dir, "continuous_plain.edf")
    with open(p, "rb") as fp:
        r = client.post(
            "/api/upload",
            files={"file": ("continuous_plain.edf", fp.read(), "application/octet-stream")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["overview"]["file"]["variant"] == "EDF"
    # 上传后可用返回的 file_id 拉取概览。
    fid = body["file_id"]
    assert client.get(f"/api/files/{fid}/overview").status_code == 200


def test_upload_corrupt_edf_rejected():
    r = client.post(
        "/api/upload",
        files={"file": ("broken.edf", b"not an edf file at all" * 20,
                        "application/octet-stream")},
    )
    assert r.status_code == 422
