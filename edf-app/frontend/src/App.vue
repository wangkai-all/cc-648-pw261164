<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from './api.js'
import WaveformChart from './components/WaveformChart.vue'
import FileOverview from './components/FileOverview.vue'
import ChannelTable from './components/ChannelTable.vue'
import EventTable from './components/EventTable.vue'
import SampleInspector from './components/SampleInspector.vue'

const samples = ref([])
const fileId = ref('')
const overview = ref(null)
const selectedChannel = ref(0)
const tStart = ref(0)
const tEnd = ref(2)
const segment = ref(null)
const loading = ref(false)
const error = ref('')
const mode = ref('physical')
const hoverInfo = ref(null)
const uploadBusy = ref(false)

const currentChannel = computed(() =>
  overview.value?.channels.find((c) => c.signal_index === selectedChannel.value) || null,
)
const timelineEnd = computed(() => overview.value?.timeline.timeline_end_s ?? 0)

onMounted(async () => {
  try {
    const data = await api.samples()
    samples.value = data.samples
    if (samples.value.length) await loadSample(samples.value[2].file_id) // 默认 EDF+D
  } catch (e) {
    error.value = '无法连接后端：' + e.message
  }
})

async function loadSample(id) {
  error.value = ''
  fileId.value = id
  overview.value = null
  segment.value = null
  try {
    overview.value = await api.overview(id)
    selectedChannel.value = overview.value.channels[0]?.signal_index ?? 0
    tStart.value = 0
    tEnd.value = Math.min(3, timelineEnd.value)
    await fetchSegment()
  } catch (e) {
    error.value = e.message
  }
}

async function fetchSegment() {
  if (!fileId.value || selectedChannel.value < 0) return
  loading.value = true
  error.value = ''
  try {
    segment.value = await api.segment(
      fileId.value, selectedChannel.value, tStart.value, tEnd.value,
    )
  } catch (e) {
    error.value = e.message
    segment.value = null
  } finally {
    loading.value = false
  }
}

watch([selectedChannel], fetchSegment)

function onBrush(range) {
  tStart.value = Number(range.tStart.toFixed(4))
  tEnd.value = Number(range.tEnd.toFixed(4))
  fetchSegment()
}

function resetWindow() {
  tStart.value = 0
  tEnd.value = Math.min(3, timelineEnd.value)
  fetchSegment()
}

function fullWindow() {
  tStart.value = 0
  tEnd.value = timelineEnd.value
  fetchSegment()
}

async function onUpload(event) {
  const file = event.target.files[0]
  if (!file) return
  uploadBusy.value = true
  error.value = ''
  try {
    const fd = new FormData()
    fd.append('file', file)
    const body = await api.upload(fd)
    fileId.value = body.file_id
    overview.value = body.overview
    selectedChannel.value = overview.value.channels[0]?.signal_index ?? 0
    tStart.value = 0
    tEnd.value = Math.min(3, timelineEnd.value)
    await fetchSegment()
  } catch (e) {
    error.value = '上传失败：' + e.message
  } finally {
    uploadBusy.value = false
    event.target.value = ''
  }
}

const segmentCsv = computed(() =>
  fileId.value
    ? api.segmentCsvUrl(fileId.value, selectedChannel.value, tStart.value, tEnd.value)
    : '#',
)
const eventsCsv = computed(() =>
  fileId.value ? api.eventsCsvUrl(fileId.value, tStart.value, tEnd.value) : '#',
)
</script>

<template>
  <h1>EDF / EDF+ 文件接入演示</h1>
  <p class="subtitle">
    全部为合成信号，仅整理测量数据（波形 / 单位 / 采样率 / 注释），不做任何生理结论解释。
    连续 EDF、EDF+C 走 pyedflib；EDF+D 间断文件走自实现受限读取器，保留时间缺口。
  </p>

  <div v-if="error" class="error">{{ error }}</div>

  <!-- 样例与上传 -->
  <div class="panel">
    <h2>选择内置合成样例</h2>
    <div class="row">
      <button v-for="s in samples" :key="s.file_id"
              class="sample-btn" :class="{ active: s.file_id === fileId }"
              @click="loadSample(s.file_id)">
        {{ s.filename }}
        <span class="note">{{ s.note }}</span>
      </button>
    </div>
    <h3>或上传自己的 EDF / EDF+ 文件</h3>
    <label class="upload-zone">
      <input type="file" accept=".edf,.edf+" style="display:none"
             @change="onUpload" :disabled="uploadBusy" />
      {{ uploadBusy ? '上传解析中…' : '点击选择 .edf / .edf+ 文件（≤200MB）' }}
    </label>
  </div>

  <template v-if="overview">
    <FileOverview :overview="overview" />
    <ChannelTable :channels="overview.channels" :selected="selectedChannel"
                  @select="selectedChannel = $event" />

    <!-- 波形与时间窗控制 -->
    <div class="panel">
      <h2>
        波形：{{ currentChannel?.label }}
        <span class="muted small">（{{ currentChannel?.sample_rate_hz }} Hz，
          单位 {{ currentChannel?.physical_dimension || '—' }}）</span>
      </h2>
      <div class="row" style="margin-bottom:12px">
        <label class="field">起点(s)
          <input type="number" v-model.number="tStart" min="0" step="0.1" />
        </label>
        <label class="field">终点(s)
          <input type="number" v-model.number="tEnd" min="0" step="0.1" />
        </label>
        <button class="primary" @click="fetchSegment" :disabled="loading">
          {{ loading ? '读取中…' : '应用时间窗' }}
        </button>
        <button @click="resetWindow">重置窗口</button>
        <button @click="fullWindow">整个时间轴</button>
        <label class="field">
          <input type="radio" value="physical" v-model="mode" :disabled="segment?.calibration_rejected" />
          物理值
        </label>
        <label class="field">
          <input type="radio" value="digital" v-model="mode" /> 数字值
        </label>
      </div>
      <p class="small muted" style="margin-top:0">
        在图上按住鼠标框选可放大该时间段；橙色阴影为 EDF+D 时间缺口（折线在此断开，不连线）；
        紫色为注释事件。
      </p>

      <WaveformChart v-if="segment"
                     :plot="segment.plot"
                     :gaps="segment.gaps_inside"
                     :events="overview.events"
                     :t-start="tStart" :t-end="tEnd"
                     :unit="currentChannel?.physical_dimension || ''"
                     :mode="mode"
                     @brush="onBrush" @hover="hoverInfo = $event" />
      <div v-if="hoverInfo" class="small muted mono" style="margin-top:6px">
        悬停样本 t={{ hoverInfo.time?.toFixed(4) }}s，
        {{ mode === 'digital' ? '数字值' : '物理值' }}={{ hoverInfo.value }}
      </div>
      <div class="legend">
        <span><span class="swatch" style="background:var(--accent)"></span>波形（缺口处断开）</span>
        <span><span class="swatch" style="background:var(--gap-edge)"></span>EDF+D 时间缺口</span>
        <span><span class="swatch" style="background:var(--ann)"></span>注释事件</span>
        <span v-if="segment?.plot.stride > 1" class="muted">
          绘图降采样步长 {{ segment.plot.stride }}（原始 {{ segment.plot.original_sample_count }} 点，核对表仍为全量）
        </span>
      </div>
    </div>

    <SampleInspector :segment="segment" :channel="currentChannel" />
    <EventTable :events="overview.events" />

    <!-- 导出 -->
    <div class="panel">
      <h2>导出当前所选片段与事件表（CSV）</h2>
      <div class="row">
        <a class="download" :href="segmentCsv">
          ⬇ 导出通道「{{ currentChannel?.label }}」窗口 [{{ tStart }}, {{ tEnd }}) 逐样本 CSV
        </a>
        <a class="download" :href="eventsCsv">⬇ 导出窗口内注释事件 CSV</a>
      </div>
    </div>
  </template>
</template>
