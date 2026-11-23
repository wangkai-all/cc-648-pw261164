<script setup>
defineProps({
  channels: { type: Array, required: true },
  selected: { type: Number, default: -1 },
})
const emit = defineEmits(['select'])
</script>

<template>
  <div class="panel">
    <h2>通道（各通道独立采样率，不强制对齐）</h2>
    <table>
      <thead>
        <tr>
          <th>#</th><th>标签</th><th>单位</th><th>采样率(Hz)</th>
          <th>每记录样本</th><th>数字范围</th><th>物理范围</th><th>标定</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="ch in channels" :key="ch.signal_index"
            class="clickable" :class="{ selected: ch.signal_index === selected }"
            @click="emit('select', ch.signal_index)">
          <td class="mono">{{ ch.signal_index }}</td>
          <td>{{ ch.label }}</td>
          <td class="mono">{{ ch.physical_dimension || '—' }}</td>
          <td class="mono">{{ ch.sample_rate_hz }}</td>
          <td class="mono">{{ ch.samples_per_record }}</td>
          <td class="mono">{{ ch.digital_min }}..{{ ch.digital_max }}</td>
          <td class="mono">{{ ch.physical_min }}..{{ ch.physical_max }}</td>
          <td>
            <span class="badge" :class="ch.calibration_rejected ? 'danger' : 'ok'">
              {{ ch.calibration_rejected ? '拒收' : '有效' }}
            </span>
            <div v-if="ch.calibration_rejected" class="small muted" style="max-width:220px">
              {{ ch.calibration_reject_reason }}
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
