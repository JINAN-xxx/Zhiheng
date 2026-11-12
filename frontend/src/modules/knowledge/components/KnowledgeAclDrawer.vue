<script setup lang="ts">
import { copyText } from '../../../shared/clipboard.ts'
import { createRequestId } from '../../../shared/requestId.ts'
import { computed, onBeforeUnmount, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Checkbox as ACheckbox,
  Input as AInput,
  Modal as AModal,
  Tag as ATag,
  message
} from 'ant-design-vue'
import { LinkOutlined, CopyOutlined } from '@ant-design/icons-vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
import { identity } from '../../identity/public.ts'
import {
  getBaseAcl,
  cleanupKnowledgeSharing,
  listKnowledgeMembers,
  listKnowledgeInvitations,
  createKnowledgeInvitation,
  getKnowledgeInvitationLink,
  revokeKnowledgeInvitation,
  removeKnowledgeMember
} from '../api/index.ts'
import type {
  KnowledgeAcl,
  KnowledgeBase,
  KnowledgeInvitation,
  KnowledgeMember
} from '../api/index.ts'
import { formatTime } from '../presentation.ts'
const emit = defineEmits<{ refresh: [] }>()
const visible = ref(false),
  loading = ref(false),
  busy = ref(false),
  error = ref(''),
  cleanup = ref(false)
const acl = ref<KnowledgeAcl | null>(null)
const members = ref<KnowledgeMember[]>([]),
  invitations = ref<KnowledgeInvitation[]>([]),
  issuedLink = ref('')
const uncertain = ref(false)
const read = new RequestScope(),
  write = new RequestScope(),
  confirmation = useConfirmation()
let baseId = '',
  pending: { expected_acl_version: number; client_request_id: string } | null = null
const storageKey = () => `zhiheng:knowledge-invite-create:${identity.value?.id ?? ''}:${baseId}`
const restricted = computed(
  () => acl.value?.entries.filter((entry) => entry.effect === 'deny' || entry.expires_at) ?? []
)
const canInvite = computed(
  () => acl.value?.base.document_policy === 'library' && !restricted.value.length
)
const statuses: Record<KnowledgeInvitation['status'], string> = {
  active: '待接受',
  used: '已使用',
  expired: '已过期',
  revoked: '已失效'
}
async function reload(silent = false) {
  const ticket = read.replace(),
    id = baseId
  if (!silent) loading.value = true
  try {
    const [value, people, links] = await Promise.all([
      getBaseAcl(id, ticket.signal),
      listKnowledgeMembers(id, ticket.signal),
      listKnowledgeInvitations(id, ticket.signal)
    ])
    if (!ticket.isCurrent() || baseId !== id) return
    acl.value = value
    members.value = people
    invitations.value = links
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
function selectLink(event: FocusEvent) {
  if (event.target instanceof HTMLInputElement) event.target.select()
}
function close() {
  if (busy.value) return
  visible.value = false
  read.replace()
  write.replace()
  issuedLink.value = ''
  error.value = ''
  acl.value = null
  members.value = []
  invitations.value = []
  confirmation.cancel()
}
async function open(base: KnowledgeBase) {
  if (busy.value || base.owner_id !== identity.value?.id || !base.can_manage) return
  close()
  baseId = base.id
  visible.value = true
  cleanup.value = false
  uncertain.value = false
  pending = null
  try {
    const saved = sessionStorage.getItem(storageKey())
    if (saved) {
      const d: unknown = JSON.parse(saved)
      if (
        typeof d === 'object' &&
        d !== null &&
        'expected_acl_version' in d &&
        typeof d.expected_acl_version === 'number' &&
        'client_request_id' in d &&
        typeof d.client_request_id === 'string'
      ) {
        pending = {
          expected_acl_version: d.expected_acl_version,
          client_request_id: d.client_request_id
        }
        uncertain.value = true
      }
    }
  } catch {
    error.value = '无法恢复上次生成请求，请先核对邀请记录'
  }
  await reload()
}
function remember(body: NonNullable<typeof pending>) {
  sessionStorage.setItem(storageKey(), JSON.stringify(body))
  pending = body
}
function forget() {
  sessionStorage.removeItem(storageKey())
  pending = null
  uncertain.value = false
}
function showLink(token: string) {
  issuedLink.value = `${window.location.origin}/knowledge/join#${token}`
}
async function generate() {
  if (!acl.value || !canInvite.value || busy.value || loading.value) return
  const body = pending ?? {
    expected_acl_version: acl.value.base.acl_version,
    client_request_id: createRequestId()
  }
  try {
    remember(body)
  } catch {
    error.value = '无法保留生成请求，请恢复浏览器会话存储后重试'
    return
  }
  const ticket = write.replace()
  busy.value = true
  error.value = ''
  try {
    const result = await createKnowledgeInvitation(baseId, body, ticket.signal)
    if (!ticket.isCurrent()) return
    forget()
    invitations.value = [
      result.invitation,
      ...invitations.value.filter((item) => item.id !== result.invitation.id)
    ]
    if (result.invitation.status === 'active') showLink(result.token)
    else error.value = '原邀请已失效，请重新生成'
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) {
      uncertain.value = !(reason instanceof ApiError) || reason.status >= 500
      if (!uncertain.value) forget()
      error.value = errorMessage(reason)
    }
  } finally {
    if (ticket.isCurrent()) busy.value = false
  }
}
async function copy() {
  if (!issuedLink.value) return
  try {
    await copyText(issuedLink.value)
    message.success('邀请链接已复制')
  } catch {
    message.info('请选中邀请链接后复制')
  }
}
async function copyExisting(invitation: KnowledgeInvitation) {
  if (busy.value) return
  const ticket = write.replace()
  busy.value = true
  error.value = ''
  try {
    const result = await getKnowledgeInvitationLink(baseId, invitation.id, ticket.signal)
    if (ticket.isCurrent()) {
      showLink(result.token)
      await copy()
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) busy.value = false
  }
}
async function revoke(invitation: KnowledgeInvitation) {
  if (busy.value || !(await confirmation.confirm('使这个邀请链接立即失效？已加入成员需单独移除。')))
    return
  const ticket = write.replace()
  busy.value = true
  error.value = ''
  try {
    await revokeKnowledgeInvitation(baseId, invitation.id, ticket.signal)
    if (ticket.isCurrent()) {
      issuedLink.value = ''
      await reload(true)
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) busy.value = false
  }
}
async function remove(member: KnowledgeMember) {
  if (
    !acl.value ||
    busy.value ||
    !(await confirmation.confirm(`移除“${member.name}”的成员授权？移除后将不能再读取本库。`))
  )
    return
  const ticket = write.replace()
  busy.value = true
  error.value = ''
  try {
    await removeKnowledgeMember(baseId, member.id, acl.value.base.acl_version, ticket.signal)
    if (ticket.isCurrent()) {
      issuedLink.value = ''
      await reload(true)
      emit('refresh')
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) busy.value = false
  }
}
async function save() {
  if (
    !acl.value ||
    loading.value ||
    busy.value ||
    uncertain.value ||
    (restricted.value.length && !cleanup.value)
  )
    return
  if (!restricted.value.length) {
    close()
    return
  }
  const ticket = write.replace()
  busy.value = true
  error.value = ''
  try {
    await cleanupKnowledgeSharing(baseId, acl.value.base.acl_version, ticket.signal)
    if (ticket.isCurrent()) {
      busy.value = false
      emit('refresh')
      close()
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) busy.value = false
  }
}
onBeforeUnmount(() => {
  read.dispose()
  write.dispose()
  issuedLink.value = ''
})
defineExpose({ open, close })
</script>
<template>
  <AModal
    :open="visible"
    :title="'共享 · ' + (acl?.base.name ?? '')"
    centered
    :width="640"
    :mask-closable="false"
    :closable="!busy"
    :confirm-loading="busy"
    :ok-text="restricted.length ? '整理旧规则' : '完成'"
    cancel-text="关闭"
    :ok-button-props="{
      disabled: loading || !acl || uncertain || (!!restricted.length && !cleanup)
    }"
    @cancel="close"
    @ok="save"
  >
    <AAlert v-if="error" :message="error" type="error" show-icon class="share-alert" />
    <AAlert
      v-if="uncertain"
      message="生成结果待确认，继续操作将沿用原请求。"
      type="warning"
      show-icon
      class="share-alert"
    />
    <p class="share-help">共享成员可检索、查看和下载本库全部资料，权限固定为只读。</p>
    <p class="share-help">
      通过邀请链接添加成员，没有成员时只有自己可以访问。
      <AButton type="link" size="small" :disabled="busy" @click="reload()">刷新共享</AButton>
    </p>
    <section class="share-section">
      <h3>邀请链接</h3>
      <p class="share-help">
        对方登录后确认接受。链接 24
        小时有效，由首位登录并接受的用户领取；关闭页面不会撤销已生成的邀请。
      </p>
      <AAlert
        v-if="acl?.base.document_policy !== 'library'"
        type="warning"
        message="旧库请先预览并确认资料迁移，再生成邀请。"
        show-icon
      />
      <AButton :disabled="!canInvite || busy || loading" @click="generate"
        ><LinkOutlined aria-hidden="true" />{{
          uncertain ? '确认原生成请求' : '生成一次性邀请链接'
        }}</AButton
      >
      <div v-if="issuedLink" class="share-issued">
        <AInput :value="issuedLink" readonly aria-label="邀请链接" @focus="selectLink" /><AButton
          @click="copy"
          ><CopyOutlined aria-hidden="true" />复制</AButton
        >
      </div>
      <div v-for="link in invitations" :key="link.id" class="share-row">
        <div>
          <ATag :color="link.status === 'active' ? 'processing' : 'default'">{{
            statuses[link.status]
          }}</ATag
          ><span class="share-date">有效至 {{ formatTime(link.expires_at) }}</span>
        </div>
        <span v-if="link.status === 'active'"
          ><AButton type="link" size="small" :disabled="busy" @click="copyExisting(link)"
            >复制链接</AButton
          ><AButton type="text" size="small" :disabled="busy" @click="revoke(link)"
            >撤销邀请</AButton
          ></span
        >
      </div>
    </section>
    <section class="share-section">
      <h3>
        已授权成员 <span>{{ members.length }}</span>
      </h3>
      <p v-if="!members.length" class="share-help">暂无成员，生成邀请链接并发给对方即可。</p>
      <div v-for="member in members" :key="member.id" class="share-row">
        <span>{{ member.name }} <ATag v-if="!member.active">已停用</ATag></span
        ><span
          ><span class="share-date">只读</span
          ><AButton type="link" size="small" danger :disabled="busy" @click="remove(member)"
            >移除成员</AButton
          ></span
        >
      </div>
    </section>
    <div v-if="restricted.length" class="share-section">
      <AAlert
        type="warning"
        message="整理将移除以下旧规则，仅保留无到期时间的已有成员授权。"
        show-icon
      />
      <p v-for="entry in restricted" :key="entry.user_id" class="share-help">
        {{ entry.user_id }} · {{ entry.effect === 'deny' ? '拒绝访问' : '到期授权' }}
      </p>
      <ACheckbox v-model:checked="cleanup"
        >确认移除所列历史拒绝和到期授权；需要共享的用户请重新通过链接加入</ACheckbox
      >
    </div>
  </AModal>
</template>
<style scoped>
.share-help {
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.8;
}
.share-alert {
  margin-bottom: 16px;
}
.share-section {
  margin-top: 20px;
  border-top: 1px solid var(--gray-150);
  padding-top: 16px;
}
h3 {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 8px;
}
h3 span {
  color: var(--color-text-secondary);
  font-weight: 400;
}
.share-issued {
  display: flex;
  gap: 8px;
  margin: 12px 0;
}
.share-issued :deep(input) {
  font-size: 12px;
}
.share-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  flex-wrap: wrap;
}
.share-date {
  font-size: 12px;
  color: var(--color-text-secondary);
}
</style>
