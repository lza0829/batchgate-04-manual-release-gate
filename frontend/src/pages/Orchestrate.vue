<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'
const history = ref([])
const current = ref(null)
const decisions = ref([])
const busy = ref(false)
const err = ref('')
const actor = ref('operator')
const note = ref('')
async function refresh() { history.value = await getJSON('/api/orchestrations') }
async function loadCurrent(id) {
  current.value = await getJSON(`/api/orchestrations/${id}`)
  decisions.value = await getJSON(`/api/orchestrations/${id}/releases`)
}
async function runOnce() {
  err.value = ''; busy.value = true
  try {
    const r = await postJSON('/api/orchestrations')
    await loadCurrent(r.id)
    await refresh()
  } catch (e) { err.value = String(e.message || e) }
  finally { busy.value = false }
}
async function decide(jobId, decision) {
  err.value = ''
  try {
    await postJSON(`/api/orchestrations/${current.value.id}/releases`, {
      job_id: jobId, decision, actor: actor.value || 'operator', note: note.value,
    })
    note.value = ''
    await loadCurrent(current.value.id)
    await refresh()
  } catch (e) { err.value = String(e.message || e) }
}
async function resume() {
  err.value = ''; busy.value = true
  try {
    const r = await postJSON(`/api/orchestrations/${current.value.id}/resume`)
    await loadCurrent(r.id)
    await refresh()
  } catch (e) { err.value = String(e.message || e) }
  finally { busy.value = false }
}
onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
const pending = computed(() => current.value?.report?.release_pending_jobs || [])
const statusClass = (s) => (s === 'ok' ? 'ok' : (s === 'awaiting_release' ? 'warn' : 'danger'))
</script>
<template>
  <div class="page">
    <h1>编排台</h1>
    <p class="muted">
      触发一次编排：按依赖层执行；上游未成功记 skipped_dep；标了「需人工放行」的作业在当趟未放行前不跑夹具，
      记 skipped_release，编排停在 awaiting_release，放行/拒绝后可继续。
    </p>
    <div class="panel">
      <button :disabled="busy" @click="runOnce">{{ busy ? '编排中…' : '执行编排' }}</button>
    </div>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" v-if="current">
      <h3>编排 #{{ current.id }}</h3>
      <p>
        <span class="tag" :class="statusClass(current.status)">{{ current.status }}</span>
        · succeeded {{ current.report?.succeeded }}
        / failed {{ current.report?.failed }}
        / 放行跳过 {{ current.report?.skipped_release }}
        / 依赖跳过 {{ current.report?.skipped_dep }}
        / attempts {{ current.report?.attempt_count }}
      </p>
      <p class="mono muted">
        已放行 {{ current.report?.released }} · 已拒绝 {{ current.report?.rejected }}
        · watermarked_jobs={{ current.report?.watermarked_jobs }} layers={{ current.report?.layer_count }}
      </p>

      <template v-if="current.status === 'awaiting_release'">
        <h4>待放行闸口</h4>
        <p>
          <label>操作人 <input v-model="actor" style="width:8rem" /></label>
          <label style="margin-left:0.75rem">备注 <input v-model="note" placeholder="可选" style="width:16rem" /></label>
        </p>
        <table>
          <thead><tr><th>作业</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="p in pending" :key="p.job_id">
              <td class="mono">{{ p.job_name }}</td>
              <td>
                <button @click="decide(p.job_id, 'released')">放行</button>
                <button class="secondary" style="margin-left:0.5rem" @click="decide(p.job_id, 'rejected')">拒绝</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p style="margin-top:0.75rem">
          <button :disabled="busy" @click="resume">{{ busy ? '继续中…' : '继续编排' }}</button>
          <span class="muted" style="margin-left:0.5rem">记录决定后点此继续跑剩余作业；未决定则保持等待。</span>
        </p>
      </template>

      <template v-if="decisions.length">
        <h4>放行记录（本趟）</h4>
        <table>
          <thead><tr><th>作业</th><th>决定</th><th>操作人</th><th>时间</th><th>备注</th></tr></thead>
          <tbody>
            <tr v-for="d in decisions" :key="d.id">
              <td class="mono">{{ d.job_name }}</td>
              <td><span class="tag" :class="d.decision === 'released' ? 'ok' : 'danger'">{{ d.decision }}</span></td>
              <td>{{ d.actor }}</td>
              <td class="mono">{{ d.decided_at }}</td>
              <td>{{ d.note || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </template>

      <p>
        <router-link :to="{ path: '/attempts', query: { orchestration_id: current.id } }">查看本次尝试 →</router-link>
        ·
        <router-link to="/watermarks">产出水位 →</router-link>
      </p>
    </div>
    <div class="panel">
      <h3>历史编排</h3>
      <table>
        <thead><tr><th>ID</th><th>状态</th><th>成功</th><th>失败</th><th>放行跳过</th><th>依赖跳过</th><th>待放行</th><th>开始</th><th></th></tr></thead>
        <tbody>
          <tr v-for="o in history" :key="o.id">
            <td>{{ o.id }}</td>
            <td><span class="tag" :class="statusClass(o.status)">{{ o.status }}</span></td>
            <td>{{ o.report?.succeeded }}</td>
            <td>{{ o.report?.failed }}</td>
            <td>{{ o.report?.skipped_release }}</td>
            <td>{{ o.report?.skipped_dep }}</td>
            <td>{{ o.report?.release_pending_jobs?.length || 0 }}</td>
            <td class="mono">{{ o.started_at }}</td>
            <td><button class="secondary" @click="loadCurrent(o.id)">查看/处理</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
