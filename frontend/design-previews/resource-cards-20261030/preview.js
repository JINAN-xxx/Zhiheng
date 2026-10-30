const icon = (name, className = '') =>
  `<svg class="${className}" aria-hidden="true"><use href="#icon-${name}"/></svg>`
const escape = (text) =>
  String(text).replace(
    /[&<>"']/g,
    (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]
  )
const resources = {
  models: [
    {
      id: '00000000-0000-4000-8000-000000000101',
      name: 'GLM-5.3-Flash',
      kind: 'model',
      caption: '对话模型',
      status: '已启用',
      upstream: 'zai-org/GLM-5.3-Flash',
      limit: '4,096 Token',
      purpose: '支持多轮对话',
      provider: '硅基流动'
    },
    {
      id: '00000000-0000-4000-8000-000000000102',
      name: 'Qwen3.8-27B',
      kind: 'model',
      caption: '对话模型',
      status: '已启用',
      upstream: 'Qwen/Qwen3.8-27B',
      limit: '4,096 Token',
      purpose: '支持多轮对话',
      provider: '硅基流动'
    },
    {
      id: '00000000-0000-4000-8000-000000000103',
      name: 'bge-m3',
      kind: 'vector',
      caption: '向量模型',
      status: '已启用',
      upstream: 'BAAI/bge-m3',
      purpose: '文档向量化与语义检索',
      provider: '硅基流动'
    },
    {
      id: '00000000-0000-4000-8000-000000000104',
      name: 'bge-reranker-v2-m3',
      kind: 'rerank',
      caption: '重排序模型',
      status: '已停用',
      upstream: 'BAAI/bge-reranker-v2-m3',
      purpose: '对检索结果重新排序',
      provider: '硅基流动'
    }
  ],
  agents: [
    {
      id: '00000000-0000-4000-8000-000000000201',
      name: '文档问答助手',
      kind: 'agent',
      caption: '智能体',
      status: '已发布',
      description: '结合知识库中的文档回答问题，提供相关资料与来源。',
      knowledge: 2,
      skills: 2,
      version: 3,
      pending: false
    },
    {
      id: '00000000-0000-4000-8000-000000000202',
      name: '研究与分析助手',
      kind: 'agent',
      caption: '智能体',
      status: '已发布',
      description: '拆解研究问题，梳理依据与结论，协助形成分析报告。',
      knowledge: 1,
      skills: 2,
      version: 2,
      pending: true
    },
    {
      id: '00000000-0000-4000-8000-000000000203',
      name: '日常写作助手',
      kind: 'agent',
      caption: '智能体',
      status: '草稿',
      description: '协助整理想法、完善表达，生成结构清楚的工作文稿。',
      knowledge: 0,
      skills: 1,
      version: 1,
      pending: false
    },
    {
      id: '00000000-0000-4000-8000-000000000204',
      name: '会议纪要助手',
      kind: 'agent',
      caption: '智能体',
      status: '草稿',
      description: '提炼会议结论、责任人和待办事项，减少会后整理时间。',
      knowledge: 0,
      skills: 1,
      version: 1,
      pending: false
    }
  ],
  work: [
    {
      id: '00000000-0000-4000-8000-000000000301',
      name: '整理本周项目周报',
      kind: 'work',
      caption: '工作任务',
      status: '进行中',
      description: '归纳本周进展、关键问题和下一步计划，整理为一份可发送的周报。',
      running: true,
      updated: '今天 10:45'
    },
    {
      id: '00000000-0000-4000-8000-000000000302',
      name: '梳理产品需求',
      kind: 'work',
      caption: '工作任务',
      status: '进行中',
      description: '汇总已有需求，区分目标、边界和验收标准，整理待确认事项。',
      running: false,
      updated: '今天 09:30'
    },
    {
      id: '00000000-0000-4000-8000-000000000303',
      name: '整理客户访谈结论',
      kind: 'work',
      caption: '工作任务',
      status: '已完成',
      description: '汇总访谈反馈，提炼共性问题和改进方向，保留可核查的访谈依据。',
      running: false,
      updated: '昨天 17:20'
    },
    {
      id: '00000000-0000-4000-8000-000000000304',
      name: '上季度资料整理',
      kind: 'work',
      caption: '工作任务',
      status: '已归档',
      description: '归档阶段性成果、项目资料和相关决策，方便后续回看。',
      running: false,
      updated: '10 月 28 日'
    }
  ]
}
const titles = { models: '模型目录', agents: '智能体', work: '工作管理' }
const units = { models: '个模型', agents: '个智能体', work: '个工作' }
const actions = { models: '测试', agents: '试聊', work: '打开工作' }
const state = { module: 'models', density: 'compact', search: '' }
const cards = document.querySelector('#cards')
const menu = document.querySelector('#menu')
const detail = document.querySelector('#detail')
let toastTimer, currentDetail, menuTrigger

function badge(status) {
  const tone = ['已停用', '草稿', '已归档'].includes(status)
    ? 'muted'
    : status === '进行中'
      ? 'processing'
      : ''
  return `<span class="badge ${tone}">${escape(status)}</span>`
}
function cardBody(item) {
  if (state.module === 'models')
    return `<dl class="card-fields"><div><dt>上游模型</dt><dd class="code" title="${escape(item.upstream)}">${escape(item.upstream)}</dd></div>${item.limit ? `<div><dt>输出上限</dt><dd>${item.limit}</dd></div>` : ''}</dl><span class="purpose">${escape(item.purpose)}</span>`
  if (state.module === 'agents')
    return `<p class="card-description">${escape(item.description)}</p><div class="resource-tags"><span>${item.knowledge ? `${item.knowledge} 个知识库` : '未绑定知识库'}</span><span>${item.skills} 个技能</span>${item.pending ? '<span class="pending">有未发布更改</span>' : ''}</div>`
  return `<p class="card-description">${escape(item.description)}</p>${item.status === '进行中' ? `<span class="running ${item.running ? 'is-working' : ''}">${item.running ? '当前正在执行' : '等待下一步指令'}</span>` : '<span class="purpose">历史内容保留，可随时回看</span>'}`
}
function render() {
  closeMenu()
  const rows = resources[state.module].filter((item) =>
    [item.name, item.description, item.upstream].some((value) =>
      value?.toLowerCase().includes(state.search.toLowerCase())
    )
  )
  document.querySelector('#stage-title').textContent = titles[state.module]
  document.querySelector('#count').textContent = `${rows.length} ${units[state.module]}`
  cards.dataset.density = state.density
  cards.innerHTML = rows
    .map((item) => {
      const footer =
        state.module === 'models'
          ? item.provider
          : state.module === 'agents'
            ? `${item.status === '已发布' ? '发布版本' : '草稿版本'} v${item.version}`
            : `更新于 ${item.updated}`
      const action =
        state.module === 'work' && item.status !== '进行中' ? '查看记录' : actions[state.module]
      return `<article class="resource-card" data-id="${item.id}">
      <header class="card-head"><span class="resource-icon ${item.kind}">${icon(item.kind)}</span><div class="card-identity"><div class="card-title-row"><button type="button" class="card-title" title="${escape(item.name)}" data-detail="${item.id}">${escape(item.name)}</button><button type="button" class="copy-button" data-copy="${item.id}" aria-label="复制${escape(item.name)}的示例ID" title="复制${state.module === 'models' ? '模型' : state.module === 'agents' ? '智能体' : '工作'}ID">${icon('copy')}</button></div><span class="card-caption">${item.caption}</span></div>${badge(item.status)}</header>
      <div class="card-body">${cardBody(item)}</div>
      <footer class="card-footer"><span class="footer-note">${escape(footer)}</span><div class="card-actions"><button type="button" class="primary-action" data-primary="${item.id}" ${state.module === 'models' && item.status === '已停用' ? 'disabled title="启用模型后可测试"' : ''}>${action}${icon('arrow')}</button><button type="button" class="more-button" data-more="${item.id}" aria-label="${escape(item.name)}的更多操作" aria-expanded="false">${icon('more')}</button></div></footer></article>`
    })
    .join('')
  document.querySelector('#empty').hidden = rows.length !== 0
}
function find(id) {
  return resources[state.module].find((item) => item.id === id)
}
function showToast(message) {
  clearTimeout(toastTimer)
  const toast = document.querySelector('#toast')
  toast.textContent = message
  toast.hidden = false
  toastTimer = setTimeout(() => {
    toast.hidden = true
  }, 2600)
}
async function copy(id) {
  try {
    await navigator.clipboard.writeText(id)
    showToast('已复制示例 ID')
  } catch {
    openDetail(id)
    showToast('可在详情中选择并复制示例 ID')
  }
}
function openDetail(id) {
  const item = find(id)
  if (!item) return
  closeMenu()
  currentDetail = item
  document.querySelector('#detail-kicker').textContent = item.caption
  document.querySelector('#detail-title').textContent = item.name
  const fields = [
    ['名称', item.name],
    ['状态', item.status]
  ]
  if (state.module === 'models') {
    fields.push(['上游模型', item.upstream], ['模型服务', item.provider])
    if (item.limit) fields.push(['输出上限', item.limit])
    fields.push(['用途', item.purpose])
  } else if (state.module === 'agents')
    fields.push(
      ['说明', item.description],
      ['知识与技能', `${item.knowledge} 个知识库 · ${item.skills} 个技能`],
      [
        '版本',
        `${item.status === '已发布' ? '发布' : '草稿'} v${item.version}${item.pending ? '，草稿有未发布更改' : ''}`
      ]
    )
  else
    fields.push(
      ['完整目标', item.description],
      [
        '执行情况',
        item.status === '进行中' ? (item.running ? '正在执行' : '等待下一步指令') : item.status
      ],
      ['最近更新', item.updated]
    )
  fields.push(['示例 ID', item.id])
  document.querySelector('#detail-fields').innerHTML = fields
    .map(
      ([name, value]) =>
        `<div class="detail-field"><dt>${escape(name)}</dt><dd class="${name === '示例 ID' ? 'id' : ''}">${escape(value)}</dd></div>`
    )
    .join('')
  if (!detail.open) detail.showModal()
}
function closeMenu() {
  menu.hidden = true
  menuTrigger?.setAttribute('aria-expanded', 'false')
  menuTrigger = undefined
}
function openMenu(id, trigger) {
  if (menuTrigger === trigger && !menu.hidden) {
    closeMenu()
    return
  }
  closeMenu()
  const item = find(id)
  const entries =
    state.module === 'models'
      ? [
          '查看详情',
          '编辑模型',
          '费率配置',
          '测试连接',
          '复制模型 ID',
          item.status === '已停用' ? '启用模型' : '停用模型',
          '删除模型'
        ]
      : state.module === 'agents'
        ? [
            '查看详情',
            '进入完整配置',
            ...(item.status === '已发布' ? ['API 接入', '取消发布'] : ['发布智能体']),
            '复制智能体 ID',
            '删除智能体'
          ]
        : [
            '查看完整目标',
            ...(item.status === '进行中' ? ['完成工作', '归档工作'] : ['重新打开']),
            '复制工作 ID',
            '删除工作'
          ]
  menu.innerHTML = entries
    .map(
      (entry) =>
        `${entry.startsWith('删除') ? '<div class="divider" role="separator"></div>' : ''}<button type="button" role="menuitem" data-entry="${entry}" data-resource="${id}" class="${entry.startsWith('删除') ? 'danger' : ''}">${entry}</button>`
    )
    .join('')
  menu.hidden = false
  const rect = trigger.getBoundingClientRect()
  menu.style.left = `${Math.min(innerWidth - menu.offsetWidth - 12, Math.max(12, rect.right - menu.offsetWidth))}px`
  menu.style.top = `${rect.bottom + menu.offsetHeight + 12 > innerHeight ? Math.max(12, rect.top - menu.offsetHeight - 5) : rect.bottom + 5}px`
  menuTrigger = trigger
  trigger.setAttribute('aria-expanded', 'true')
}
document.addEventListener('click', (event) => {
  const target = event.target.closest('button')
  if (!target) {
    if (!event.target.closest('#menu')) closeMenu()
    return
  }
  if (target.dataset.module) {
    state.module = target.dataset.module
    state.search = ''
    document.querySelector('#search').value = ''
    document.querySelector('#search').placeholder =
      `搜索${state.module === 'models' ? '模型名称' : state.module === 'agents' ? '智能体名称' : '工作标题'}`
    document
      .querySelectorAll('[data-module]')
      .forEach((button) => button.setAttribute('aria-pressed', String(button === target)))
    render()
  } else if (target.dataset.density) {
    state.density = target.dataset.density
    document.querySelectorAll('[data-density]').forEach((button) => {
      if (button.tagName === 'BUTTON')
        button.setAttribute('aria-pressed', String(button === target))
    })
    render()
  } else if (target.dataset.more) openMenu(target.dataset.more, target)
  else if (target.dataset.detail) openDetail(target.dataset.detail)
  else if (target.dataset.copy) void copy(target.dataset.copy)
  else if (target.dataset.primary)
    showToast(
      `正式页面将打开${state.module === 'models' ? '模型连接测试' : state.module === 'agents' ? '智能体试聊' : '工作详情'}，此处仅预览卡片。`
    )
  else if (target.dataset.entry) {
    const entry = target.dataset.entry,
      id = target.dataset.resource
    closeMenu()
    if (entry.startsWith('复制')) void copy(id)
    else if (entry.startsWith('查看')) openDetail(id)
    else showToast(`${entry}入口保留；此预览不会修改平台数据。`)
  } else if (!target.closest('#menu')) closeMenu()
})
document.querySelector('#search').addEventListener('input', (event) => {
  state.search = event.target.value
  render()
})
document.querySelector('#close-detail').addEventListener('click', () => detail.close())
document
  .querySelector('#detail-copy')
  .addEventListener('click', () => currentDetail && copy(currentDetail.id))
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeMenu()
})
window.addEventListener('resize', closeMenu)
window.addEventListener('scroll', closeMenu, { passive: true })
render()
