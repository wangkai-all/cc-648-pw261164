// 后端 API 封装：所有调用集中在这里，组件只关心数据。
async function request(path, options = {}) {
  const res = await fetch(path, options)
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      detail = body.detail || detail
    } catch (_) {
      /* 非 JSON 错误体，保留状态码 */
    }
    throw new Error(detail)
  }
  return res.json()
}

export const api = {
  health: () => request('/api/health'),
  samples: () => request('/api/samples'),
  overview: (fileId) => request(`/api/files/${fileId}/overview`),
  segment: (fileId, signalIndex, tStart, tEnd, maxPoints = 4000) =>
    request(
      `/api/files/${fileId}/segment?` +
        new URLSearchParams({
          signal_index: signalIndex,
          t_start: tStart,
          t_end: tEnd,
          max_points: maxPoints,
        }),
    ),
  outOfRange: (fileId, signalIndex) =>
    request(`/api/files/${fileId}/channels/${signalIndex}/out-of-range`),
  upload: (formData) =>
    request('/api/upload', { method: 'POST', body: formData }),
  segmentCsvUrl: (fileId, signalIndex, tStart, tEnd) =>
    `/api/files/${fileId}/export/segment.csv?` +
    new URLSearchParams({
      signal_index: signalIndex,
      t_start: tStart,
      t_end: tEnd,
    }),
  eventsCsvUrl: (fileId, tStart, tEnd) =>
    `/api/files/${fileId}/export/events.csv?` +
    new URLSearchParams({ t_start: tStart, t_end: tEnd }),
}
