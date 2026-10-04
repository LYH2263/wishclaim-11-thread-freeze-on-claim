<template>
  <div class="wall">
    <h1 class="serif">{{ w.title }}</h1>
    <p>{{ w.note }}</p>
    <p class="tag">状态 {{ w.status }} · 认领人 {{ w.claimer || '—' }}</p>
    <p v-if="err" class="err">{{ err }}</p>
    <input v-model="claimer" placeholder="你的名字" />
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button @click="claim">认领锁定</button>
      <button class="ghost" @click="release">释放</button>
      <button class="ghost" @click="fulfill">核销完成</button>
    </div>

    <h2 class="serif">留言串 · {{ thread.count }} 楼</h2>
    <p v-if="!thread.writable" class="tag">
      {{ w.status === 'fulfilled' ? '已归档：留言串永久只读' : '认领中：留言串已冻结，历史只读' }}
    </p>
    <article v-for="m in thread.comments" :key="m.id" class="card" style="margin-bottom:8px">
      <h3>#{{ m.floor }} {{ m.author }}</h3>
      <p style="white-space:pre-wrap;margin:4px 0">{{ m.content }}</p>
      <span class="tag">{{ m.created_at }}</span>
      <button
        v-if="author.trim() && author.trim() === m.author"
        class="ghost"
        style="margin-left:8px"
        @click="remove(m)"
      >删除</button>
    </article>
    <p v-if="thread.comments.length === 0" class="tag">还没有留言，来占一楼。</p>

    <template v-if="thread.writable">
      <input v-model="author" placeholder="留言署名" style="margin-top:12px" />
      <textarea v-model="content" rows="3" placeholder="写点什么（空内容不能提交）" />
      <p v-if="formErr" class="err">{{ formErr }}</p>
      <button @click="submit">追加留言</button>
    </template>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const props = defineProps({ id: String })
const w = ref({})
const claimer = ref('访客')
const author = ref('访客')
const content = ref('')
const err = ref('')
const formErr = ref('')
const thread = ref({ count: 0, writable: true, comments: [] })

const ERROR_TEXT = {
  author_empty: '署名不能为空',
  author_too_long: '署名不能超过 50 字',
  content_empty: '留言内容不能为空',
  content_too_long: '留言不能超过 500 字',
  frozen: '认领中，留言串已冻结',
  archived: '已核销归档，永久只读',
}

async function load() {
  const [wish, t] = await Promise.all([
    api('/wishes/' + props.id),
    api('/wishes/' + props.id + '/comments'),
  ])
  w.value = wish; thread.value = t
}
async function claim() {
  err.value=''; try { await api('/wishes/'+props.id+'/claim',{method:'POST',body:JSON.stringify({claimer:claimer.value})}); await load() } catch(e){ err.value=e.message }
}
async function release() {
  err.value=''; try { await api('/wishes/'+props.id+'/release',{method:'POST',body:'{}'}); await load() } catch(e){ err.value=e.message }
}
async function fulfill() {
  err.value=''; try { await api('/wishes/'+props.id+'/fulfill',{method:'POST',body:'{}'}); await load() } catch(e){ err.value=e.message }
}
async function submit() {
  formErr.value = ''
  const body = JSON.stringify({ author: author.value, content: content.value })
  try {
    const pre = await api('/wishes/' + props.id + '/comments/precheck', { method: 'POST', body })
    if (!pre.ok) { formErr.value = pre.errors.map(c => ERROR_TEXT[c] || c).join('；'); return }
    await api('/wishes/' + props.id + '/comments', { method: 'POST', body })
    content.value = ''
    await load()
  } catch (e) { formErr.value = ERROR_TEXT[e.message] || e.message }
}
async function remove(m) {
  try {
    await api('/wishes/' + props.id + '/comments/' + m.id + '?author=' + encodeURIComponent(author.trim()),
      { method: 'DELETE' })
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
