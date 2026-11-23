<script setup>
// 原始样本核对表：逐记录展示数字值、物理值与精确字节偏移。
// 坏标定通道物理值显示为「拒收」，数字值仍完整保留。
defineProps({
  segment: { type: Object, default: null },
  channel: { type: Object, default: null },
})
</script>

<template>
  <div class="panel" v-if="segment">
    <h2>原始样本核对（按数据记录分组，未跨通道对齐）</h2>
    <div class="pill-row" style="margin-bottom:10px">
      <span class="badge info">窗口样本总数 {{ segment.total_samples }}</span>
      <span class="badge" :class="segment.calibration_rejected ? 'danger' : 'ok'">
        标定{{ segment.calibration_rejected ? '拒收：仅数字值' : '有效：数字→物理' }}
      </span>
      <span v-if="segment.pyedflib_crosscheck" class="badge ok">
        pyedflib 交叉校验 {{ segment.pyedflib_crosscheck.sample_count }} 样本一致
      </span>
    </div>
    <p v-if="segment.calibration_rejected" class="error small">
      {{ segment.calibration_reject_reason }}
    </p>
    <div class="scroll">
      <table>
        <thead>
          <tr>
            <th>记录#</th><th>记录起点(s)</th><th>记录内样本区间</th>
            <th>样本时间(s)</th><th>数字值</th><th>物理值</th><th>字节偏移</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="sp in segment.spans" :key="sp.record_index">
            <tr v-for="(d, j) in sp.digital" :key="sp.record_index + '-' + j">
              <td class="mono">{{ sp.record_index }}</td>
              <td class="mono">{{ sp.record_start_s }}</td>
              <td class="mono">[{{ sp.sample_index_start }}, {{ sp.sample_index_end }})</td>
              <td class="mono">{{ sp.sample_times[j] }}</td>
              <td class="mono">{{ d }}</td>
              <td class="mono">
                <span v-if="sp.rejected" class="badge danger">拒收</span>
                <span v-else>{{ sp.physical[j] }}</span>
              </td>
              <td class="mono">{{ sp.channel_byte_offset + j * 2 }}</td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
    <p v-if="segment.pyedflib_crosscheck" class="small muted" style="margin-top:8px">
      pyedflib 物理值范围：
      min={{ segment.pyedflib_crosscheck.min_physical?.toFixed(6) }}，
      max={{ segment.pyedflib_crosscheck.max_physical?.toFixed(6) }}
    </p>
  </div>
</template>
