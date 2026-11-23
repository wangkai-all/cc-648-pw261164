<script setup>
defineProps({ overview: { type: Object, required: true } })
const fmt = (v) => (v === null || v === undefined ? '—' : String(v))
</script>

<template>
  <div class="panel">
    <h2>文件与处理引擎</h2>
    <div class="grid-2">
      <dl class="kv">
        <dt>文件</dt><dd>{{ overview.file.name }}</dd>
        <dt>大小</dt><dd>{{ overview.file.size_bytes }} 字节</dd>
        <dt>变体</dt>
        <dd>
          <span class="badge" :class="overview.file.variant === 'EDF+D' ? 'warn' : 'info'">
            {{ overview.file.variant }}
          </span>
        </dd>
        <dt>起始时间</dt><dd>{{ fmt(overview.file.start_datetime) }}</dd>
        <dt>记录时长</dt><dd>{{ overview.file.record_duration_s }} s</dd>
        <dt>单记录大小</dt><dd>{{ overview.file.data_record_size_bytes }} 字节</dd>
        <dt>头部字节</dt><dd>{{ overview.file.header_bytes }}</dd>
      </dl>
      <dl class="kv">
        <dt>读取引擎</dt>
        <dd>
          <span class="badge" :class="overview.file.engine === 'pyedflib' ? 'ok' : 'warn'">
            {{ overview.file.engine }}
          </span>
        </dd>
        <dt>引擎选择依据</dt>
        <dd v-for="(r, i) in overview.file.engine_reasons" :key="i" class="muted small" style="font-family:inherit">
          {{ r }}
        </dd>
      </dl>
    </div>

    <h3>截断情况</h3>
    <div class="pill-row">
      <span class="badge" :class="overview.truncation.missing_records ? 'danger' : 'ok'">
        完整记录 {{ overview.truncation.complete_records }} /
        声明 {{ overview.truncation.header_declared_records }}
      </span>
      <span class="badge" :class="overview.truncation.partial_record_bytes ? 'warn' : 'ok'">
        尾部残片 {{ overview.truncation.partial_record_bytes }} 字节
      </span>
      <span class="badge info">缺失完整记录 {{ overview.truncation.missing_records }}</span>
      <span class="muted small mono">首记录偏移 {{ overview.truncation.first_record_byte_offset }}</span>
    </div>

    <h3>时间缺口（EDF+D）</h3>
    <p v-if="!overview.timeline.gaps.length" class="muted small">无缺口（连续时间轴）。</p>
    <table v-else>
      <thead>
        <tr><th>#</th><th>缺口前记录</th><th>起点(s)</th><th>终点(s)</th><th>时长(s)</th><th>后记录字节偏移</th></tr>
      </thead>
      <tbody>
        <tr v-for="g in overview.timeline.gaps" :key="g.index">
          <td class="mono">{{ g.index }}</td>
          <td class="mono">{{ g.after_record }}</td>
          <td class="mono">{{ g.start_time_s }}</td>
          <td class="mono">{{ g.end_time_s }}</td>
          <td class="mono">{{ g.duration_s }}</td>
          <td class="mono">{{ g.byte_offset }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
