<script setup>
// 纯 SVG 波形图：不依赖图表库。
// 关键处理：plot 序列中的 null 是 EDF+D 缺口/记录间断，必须断开折线，
// 绝不跨缺口连线；缺口区间叠加橙色阴影，注释事件画紫色标记。
import { computed, ref } from 'vue'

const props = defineProps({
  plot: { type: Object, required: true },
  gaps: { type: Array, default: () => [] },
  events: { type: Array, default: () => [] },
  tStart: { type: Number, required: true },
  tEnd: { type: Number, required: true },
  unit: { type: String, default: '' },
  mode: { type: String, default: 'physical' }, // physical | digital
})
const emit = defineEmits(['brush', 'hover'])

const W = 1200
const H = 340
const PAD_L = 64
const PAD_R = 16
const PAD_T = 16
const PAD_B = 34
const plotW = W - PAD_L - PAD_R
const plotH = H - PAD_T - PAD_B

const svgEl = ref(null)
const brush = ref(null) // {x0, x1} 像素坐标
const dragging = ref(false)

const values = computed(() =>
  props.mode === 'digital' ? props.plot.digital : props.plot.physical,
)

// 只统计非空点用于纵轴量程。
const stats = computed(() => {
  const vals = (values.value || []).filter((v) => v !== null && v !== undefined)
  if (!vals.length) return { min: 0, max: 1 }
  let min = Infinity
  let max = -Infinity
  for (const v of vals) {
    if (v < min) min = v
    if (v > max) max = v
  }
  if (min === max) {
    min -= 1
    max += 1
  }
  const pad = (max - min) * 0.08
  return { min: min - pad, max: max + pad }
})

function xOf(t) {
  return PAD_L + ((t - props.tStart) / (props.tEnd - props.tStart)) * plotW
}
function yOf(v) {
  const { min, max } = stats.value
  return PAD_T + (1 - (v - min) / (max - min)) * plotH
}

// 把含 null 的序列切成若干连续线段（null 处断开，不连线）。
const segments = computed(() => {
  const times = props.plot.times || []
  const vals = values.value || []
  const out = []
  let current = []
  for (let i = 0; i < times.length; i++) {
    const t = times[i]
    const v = vals[i]
    if (t === null || t === undefined || v === null || v === undefined) {
      if (current.length) {
        out.push(current)
        current = []
      }
      continue
    }
    current.push([xOf(t), yOf(v)])
  }
  if (current.length) out.push(current)
  return out
})

const pathStrings = computed(() =>
  segments.value.map((seg) => {
    if (!seg.length) return ''
    return seg.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ')
  }),
)

const yTicks = computed(() => {
  const { min, max } = stats.value
  const n = 5
  const ticks = []
  for (let i = 0; i <= n; i++) {
    const v = min + ((max - min) * i) / n
    ticks.push({ y: yOf(v), label: v.toFixed(3) })
  }
  return ticks
})

const xTicks = computed(() => {
  const n = 8
  const ticks = []
  for (let i = 0; i <= n; i++) {
    const t = props.tStart + ((props.tEnd - props.tStart) * i) / n
    ticks.push({ x: xOf(t), label: t.toFixed(2) + 's' })
  }
  return ticks
})

function eventX(t) {
  if (t < props.tStart || t > props.tEnd) return null
  return xOf(t)
}

// ---- 框选 ----
function pxToEvent(clientX) {
  const rect = svgEl.value.getBoundingClientRect()
  return ((clientX - rect.left) / rect.width) * W
}
function pxToTime(px) {
  const ratio = (px - PAD_L) / plotW
  return props.tStart + ratio * (props.tEnd - props.tStart)
}
function onDown(e) {
  dragging.value = true
  const x = pxToEvent(e.clientX)
  brush.value = { x0: x, x1: x }
}
function onMove(e) {
  const x = pxToEvent(e.clientX)
  if (dragging.value && brush.value) brush.value.x1 = x
  // 悬停：找最近样本
  const t = pxToTime(x)
  const times = props.plot.times || []
  let best = -1
  let bestDt = Infinity
  for (let i = 0; i < times.length; i++) {
    if (times[i] === null) continue
    const dt = Math.abs(times[i] - t)
    if (dt < bestDt) {
      bestDt = dt
      best = i
    }
  }
  if (best >= 0) emit('hover', { index: best, time: times[best], value: values.value[best] })
}
function onUp() {
  if (!dragging.value || !brush.value) return
  dragging.value = false
  const lo = Math.min(brush.value.x0, brush.value.x1)
  const hi = Math.max(brush.value.x0, brush.value.x1)
  brush.value = null
  if (hi - lo < 6) return // 视为点击而非框选
  emit('brush', { tStart: pxToTime(lo), tEnd: pxToTime(hi) })
}

const brushRect = computed(() => {
  if (!brush.value) return null
  const x = Math.min(brush.value.x0, brush.value.x1)
  const w = Math.abs(brush.value.x1 - brush.value.x0)
  return { x, w }
})
</script>

<template>
  <div class="chart-wrap">
    <svg
      ref="svgEl"
      class="wave"
      :viewBox="`0 0 ${W} ${H}`"
      preserveAspectRatio="none"
      @mousedown="onDown"
      @mousemove="onMove"
      @mouseup="onUp"
      @mouseleave="onUp"
    >
      <!-- 网格与纵轴刻度 -->
      <g v-for="tk in yTicks" :key="'y' + tk.label">
        <line :x1="PAD_L" :x2="W - PAD_R" :y1="tk.y" :y2="tk.y"
              stroke="#2e3a47" stroke-width="1" stroke-dasharray="3 4" />
        <text :x="PAD_L - 8" :y="tk.y + 4" fill="#8a99a8" font-size="11"
              text-anchor="end" class="mono">{{ tk.label }}</text>
      </g>
      <!-- 横轴刻度 -->
      <g v-for="tk in xTicks" :key="'x' + tk.label">
        <line :x1="tk.x" :x2="tk.x" :y1="PAD_T" :y2="H - PAD_B"
              stroke="#232d38" stroke-width="1" />
        <text :x="tk.x" :y="H - PAD_B + 18" fill="#8a99a8" font-size="11"
              text-anchor="middle" class="mono">{{ tk.label }}</text>
      </g>

      <!-- EDF+D 缺口阴影 -->
      <g v-for="g in gaps" :key="'gap' + g.index">
        <rect
          :x="xOf(Math.max(g.start_time_s, tStart))"
          :y="PAD_T"
          :width="xOf(Math.min(g.end_time_s, tEnd)) - xOf(Math.max(g.start_time_s, tStart))"
          :height="plotH"
          fill="var(--gap)" stroke="var(--gap-edge)" stroke-dasharray="4 3"
        />
        <text :x="(xOf(Math.max(g.start_time_s, tStart)) + xOf(Math.min(g.end_time_s, tEnd))) / 2"
              :y="PAD_T + 14" fill="var(--warn)" font-size="10" text-anchor="middle">
          GAP {{ g.duration_s }}s
        </text>
      </g>

      <!-- 注释事件标记（瞬时竖线 + 持续区间） -->
      <g v-for="(ev, i) in events" :key="'ev' + i">
        <template v-if="eventX(ev.onset_s) !== null">
          <line v-if="ev.duration_s === 0"
                :x1="eventX(ev.onset_s)" :x2="eventX(ev.onset_s)"
                :y1="PAD_T" :y2="H - PAD_B" stroke="var(--ann)" stroke-width="1.4" />
          <rect v-else
                :x="eventX(Math.max(ev.onset_s, tStart))" :y="PAD_T"
                :width="Math.max(2, eventX(Math.min(ev.end_s, tEnd)) - eventX(Math.max(ev.onset_s, tStart)))"
                :height="plotH" fill="rgba(192,132,252,0.12)"
                stroke="var(--ann)" stroke-width="1" stroke-dasharray="2 3" />
          <text :x="eventX(ev.onset_s)" :y="PAD_T + 4" fill="var(--ann)"
                font-size="9" text-anchor="start">{{ ev.annotation }}</text>
        </template>
      </g>

      <!-- 波形（按 null 断开的多条 path） -->
      <path v-for="(d, i) in pathStrings" :key="'p' + i" :d="d" fill="none"
            stroke="var(--accent)" stroke-width="1.3" stroke-linejoin="round"
            stroke-linecap="round" />

      <!-- 框选矩形 -->
      <rect v-if="brushRect" :x="brushRect.x" :y="PAD_T" :width="brushRect.w"
            :height="plotH" fill="rgba(77,163,255,0.18)"
            stroke="var(--accent)" stroke-width="1" />

      <!-- 轴单位 -->
      <text :x="PAD_L - 50" :y="PAD_T - 2" fill="#8a99a8" font-size="11">{{ unit }}</text>
    </svg>
  </div>
</template>
