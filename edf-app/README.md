# EDF / EDF+ 文件接入演示（FastAPI + Vue 3）

上传合成 EDF/EDF+ 演示记录后，按通道查看波形、单位、采样率与注释事件。
**所有数据均为确定性合成信号，应用只整理测量数据，不解释任何生理结论。**

## 需求到实现的对应关系

| 需求 | 实现位置 |
| --- | --- |
| 同一数据记录内各通道样本数可不同，截取时间窗按各通道采样位置读取，不强行对齐到同一行 | `app/edf_reader.py: EDFReader.read_segment()`（每通道独立 `sample_period` 与下标区间） |
| 数字值按各通道上下限换算为物理值 | `app/edf_format.py: build_calibration()` |
| 异常标定范围按通道单独拒收（不影响其他通道） | `app/edf_format.py: calibration_problem()` + `services.py` 引擎选择 |
| EDF+D 间断保留为时间缺口，不把缺口两侧连成连续信号 | `edf_reader.py` 用每记录首条 TAL 的 onset 建立时间轴，`Gap` 显式建模；绘图序列在缺口插入 `null` 断点 |
| 注释 onset/duration 按文件含义展开，跨记录/跨间断原样保留 | `edf_format.py: parse_tal_block()`；事件不拼接、不拆分 |
| 连续 EDF / EDF+C 用 pyedflib | `services.py: EDFService._decide_engine()`，且用自实现读取器交叉校验 |
| 间断 EDF+D 单独实现受限读取器 | `edf_format.py`（头部/TAL）+ `edf_reader.py`（数据记录/时间轴） |
| 截断文件：确切位置而非成功计数 | `EDFReader.truncation`（完整记录数、残片字节数、缺失记录数、首记录偏移） |
| 框选时间、核对原始样本与物理值、导出片段和事件表 | `WaveformChart.vue` 框选、`SampleInspector.vue`、两个 CSV 导出端点 |

## 明确支持的格式子集

- EDF（连续，无注释通道）、EDF+C（连续，含 `EDF Annotations`）、EDF+D（间断，含 `EDF Annotations`）；
- 每数据记录 16 bit 小端整数，单文件信号数 1–9999；
- 注释通道 label 固定为 `EDF Annotations`，TAL 以 0x00 分隔；时间戳带显式正负号；
  0x14(DC4) 分隔 onset/duration，0x15(NAK) 分隔时间字段与注释文本（解析端对两者角色做容错）；
- **不支持** BDF（24 bit）、非 ASCII 头部、压缩/加密 EDF、`hh.mm.ss` 风格时间戳。

样例文件严格按同一子集生成（`scripts/generate_samples.py`），并可被 pyedflib 0.1.42 打开
（EDF+D 与截断文件除外——pyedflib 本身不支持读取，这正是自实现读取器的覆盖范围）。

## 双引擎策略

1. 先用自实现字节级读取器解析头部、截断、标定、时间轴（对坏标定/截断不整体拒收）；
2. 连续、完整、标定全部有效的文件 → 波形优先用 **pyedflib**，并与自实现读取器做物理值交叉校验；
3. EDF+D、截断文件、含坏标定通道的文件 → 一律走**自实现读取器**，保证缺口可见、按通道拒收、截断可定位。

## 内置合成样例（`backend/samples/`）

| 文件 | 演示点 |
| --- | --- |
| `continuous_plain.edf` | 连续 EDF，4 通道 4 种采样率 100/250/1/20 Hz，无注释 |
| `continuous_plusc.edf` | EDF+C，瞬时事件、持续事件、跨记录注释 |
| `discontinuous_plusd.edf` | EDF+D，记录 2、4 后各有缺口（3–6s、8–13s），含跨间断注释 `Cross-gap segment`（2.5s 起持续 4s） |
| `truncated_plusc.edf` | 头部声明 10 条记录，实际仅 6 条完整 + 40 字节残片 |
| `bad_calibration_plusc.edf` | 温度通道物理上下限相等（37/37），该通道标定被单独拒收，其余通道正常 |

每个事件/缺口/样本都带**数据记录序号与文件字节偏移**，界面和 CSV 中可直接核对处理位置。

## 运行

### 后端（FastAPI，端口 8000）

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m scripts.generate_samples     # 生成 5 个合成样例
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 前端（Vue 3 + Vite，端口 5173）

```bash
cd frontend
npm install
npm run dev          # 开发模式，/api 自动代理到 8000
# 或 npm run build，后端会在 http://127.0.0.1:8000/ 直接托管 dist/
```

### 测试

```bash
cd backend && .venv/bin/python -m pytest tests/ -q
```

## 目录结构

```
backend/
  app/
    edf_format.py    # 头部/标定/TAL 的纯字节解析与构造
    edf_reader.py    # 受限 EDF/EDF+ 读取器：数据记录、时间轴、缺口、截断
    edf_writer.py    # 合成 EDF 写入器（样例/夹具用）
    services.py      # 双引擎编排、交叉校验、导出数据组装
    main.py          # FastAPI 路由
  scripts/generate_samples.py
  tests/             # 33 个 pytest（TAL、读取器保证、API 端到端）
  samples/           # 5 个合成 .edf
frontend/
  src/
    App.vue
    api.js
    components/      # 波形图、概览、通道表、样本核对表、事件表
```

## 安全说明

- 上传文件按服务端生成的随机 id 存储，不拼接客户端文件名做路径；
- 单文件上限 200MB；非 `.edf/.edf+` 后缀直接拒绝；解析失败返回 422 且不落盘保留；
- 本项目处理的演示文件均为本地合成数据，无真实生理信息。
