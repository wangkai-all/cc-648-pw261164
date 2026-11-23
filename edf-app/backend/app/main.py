"""FastAPI 路由：上传、概览、波形片段、样本核对、事件、CSV 导出。

所有上传文件保存在独立的 data/uploads 目录，按服务端生成的 id 管理，
不沿用客户端文件名做路径拼接（避免路径穿越）。
"""

from __future__ import annotations

import csv
import io
import os
import re
import uuid
from typing import Dict

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .edf_format import EDFFormatError
from .services import EDFService

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "uploads")
SAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 服务实例缓存：file_id -> EDFService（进程内，演示规模足够）。
_SERVICES: Dict[str, EDFService] = {}
# 内置样例的固定 id 映射。
_SAMPLE_IDS = {
    "continuous_plain": "continuous_plain.edf",
    "continuous_plusc": "continuous_plusc.edf",
    "discontinuous_plusd": "discontinuous_plusd.edf",
    "truncated": "truncated_plusc.edf",
    "bad_calibration": "bad_calibration_plusc.edf",
}

app = FastAPI(
    title="EDF/EDF+ 接入演示",
    version="1.0.0",
    description=(
        "合成信号 EDF/EDF+ 波形与注释查看。只整理测量数据，不做生理结论。"
        "支持 EDF、EDF+C（pyedflib + 自实现双引擎）与 EDF+D（自实现受限读取器）。"
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _service_for(file_id: str) -> EDFService:
    if file_id in _SERVICES:
        return _SERVICES[file_id]
    # 内置样例。
    if file_id in _SAMPLE_IDS:
        path = os.path.join(SAMPLE_DIR, _SAMPLE_IDS[file_id])
    else:
        # 仅允许定位到上传目录内的 id 文件，防止路径穿越。
        candidate = os.path.join(UPLOAD_DIR, file_id + ".edf")
        path = os.path.realpath(candidate)
        if not path.startswith(os.path.realpath(UPLOAD_DIR) + os.sep):
            raise HTTPException(404, "文件不存在")
    if not os.path.exists(path):
        raise HTTPException(404, f"文件 {file_id} 不存在或已过期")
    try:
        svc = EDFService(path)
    except EDFFormatError as exc:
        raise HTTPException(422, f"EDF 解析失败：{exc}") from exc
    _SERVICES[file_id] = svc
    return svc


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/samples")
def list_samples() -> dict:
    """内置合成样例清单（含每个文件演示的处理要点）。"""
    notes = {
        "continuous_plain": "连续 EDF，4 通道 4 种采样率(100/250/1/20Hz)，无注释",
        "continuous_plusc": "连续 EDF+C，瞬时/持续/跨记录注释",
        "discontinuous_plusd": "EDF+D，2 处时间缺口，含跨间断注释",
        "truncated": "EDF+C 截断：声明10条仅完整6条+40字节残片",
        "bad_calibration": "EDF+C，温度通道物理上下限相等被单独拒收",
    }
    return {
        "samples": [
            {"file_id": k, "filename": v, "note": notes[k]}
            for k, v in _SAMPLE_IDS.items()
        ]
    }


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    if not file.filename:
        raise HTTPException(400, "缺少文件名")
    suffix = os.path.splitext(file.filename)[1].lower()
    if suffix not in (".edf", ".edf+", ""):
        raise HTTPException(400, "仅接受 .edf / .edf+ 文件")
    file_id = uuid.uuid4().hex
    safe_stem = _SAFE_NAME.sub("_", os.path.splitext(file.filename)[0])[:60]
    stored_name = f"{file_id}_{safe_stem}.edf"
    dest = os.path.join(UPLOAD_DIR, stored_name)
    # 流式落盘，限制单文件 200MB。
    max_bytes = 200 * 1024 * 1024
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                out.close()
                os.remove(dest)
                raise HTTPException(413, "文件超过 200MB 限制")
            out.write(chunk)
    try:
        svc = EDFService(dest)
    except EDFFormatError as exc:
        os.remove(dest)
        raise HTTPException(422, f"EDF 解析失败：{exc}") from exc
    _SERVICES[file_id] = svc
    return {
        "file_id": file_id,
        "original_name": file.filename,
        "stored_name": stored_name,
        "size_bytes": size,
        "overview": svc.overview(),
    }


@app.get("/api/files/{file_id}/overview")
def get_overview(file_id: str) -> dict:
    return _service_for(file_id).overview()


@app.get("/api/files/{file_id}/segment")
def get_segment(
    file_id: str,
    signal_index: int = Query(..., ge=0),
    t_start: float = Query(..., ge=0),
    t_end: float = Query(..., gt=0),
    max_points: int = Query(4000, ge=10, le=50000),
) -> dict:
    if t_end <= t_start:
        raise HTTPException(400, "t_end 必须大于 t_start")
    try:
        return _service_for(file_id).segment(
            signal_index, t_start, t_end, max_points
        )
    except EDFFormatError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/files/{file_id}/channels/{signal_index}/out-of-range")
def get_out_of_range(file_id: str, signal_index: int) -> dict:
    svc = _service_for(file_id)
    if not (0 <= signal_index < len(svc.reader.header.signals)):
        raise HTTPException(404, "通道不存在")
    return svc.out_of_range(signal_index)


@app.get("/api/files/{file_id}/export/segment.csv")
def export_segment(
    file_id: str,
    signal_index: int = Query(..., ge=0),
    t_start: float = Query(..., ge=0),
    t_end: float = Query(..., gt=0),
) -> StreamingResponse:
    svc = _service_for(file_id)
    try:
        rows = svc.export_segment_rows(signal_index, t_start, t_end)
    except EDFFormatError as exc:
        raise HTTPException(422, str(exc)) from exc
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "time_s", "record_index", "sample_index_in_record",
            "digital_value", "physical_value", "byte_offset",
        ],
    )
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    fname = f"{file_id}_ch{signal_index}_{t_start}_{t_end}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/files/{file_id}/export/events.csv")
def export_events(
    file_id: str,
    t_start: float = Query(0.0, ge=0),
    t_end: float = Query(1e12, gt=0),
) -> StreamingResponse:
    svc = _service_for(file_id)
    rows = svc.export_event_rows(t_start, t_end)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "onset_s", "duration_s", "end_s", "annotation",
            "record_index", "byte_offset",
        ],
    )
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    fname = f"{file_id}_events_{t_start}_{t_end}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# 生产模式托管前端构建产物（开发时由 Vite 代理 /api，两者不冲突）。
_DIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "frontend", "dist",
)
_DIST = os.path.normpath(_DIST)
if os.path.isdir(_DIST):
    app.mount(
        "/",
        StaticFiles(directory=_DIST, html=True),
        name="frontend",
    )
