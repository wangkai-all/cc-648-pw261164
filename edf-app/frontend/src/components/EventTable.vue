<script setup>
defineProps({ events: { type: Array, default: () => [] } })
</script>

<template>
  <div class="panel">
    <h2>注释事件（按文件 onset/duration 展开，跨记录/跨间断原样保留）</h2>
    <p v-if="!events.length" class="muted small">该文件无注释事件。</p>
    <div v-else class="scroll">
      <table>
        <thead>
          <tr>
            <th>onset(s)</th><th>duration(s)</th><th>end(s)</th>
            <th>注释</th><th>所在记录</th><th>TAL 字节偏移</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(e, i) in events" :key="i">
            <td class="mono">{{ e.onset_s }}</td>
            <td class="mono">{{ e.duration_s }}</td>
            <td class="mono">{{ e.end_s }}</td>
            <td>{{ e.annotation }}</td>
            <td class="mono">{{ e.record_index }}</td>
            <td class="mono">{{ e.byte_offset }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
