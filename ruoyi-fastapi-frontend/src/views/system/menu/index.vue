<template>
  <WorkspacePage title="菜单管理" subtitle="导航、路由与权限标识">
    <template #actions><a-space wrap>
      <a-button type="primary" @click="handleAdd" v-hasPermi="['system:menu:add']"><PlusOutlined />新增</a-button>
      <a-button @click="handleSaveSort" v-hasPermi="['system:menu:edit']"><SaveOutlined />保存排序</a-button>
      <a-button @click="toggleExpandAll"><NodeExpandOutlined />展开/折叠</a-button>
      <a-tooltip title="显示/隐藏搜索"><a-button shape="circle" @click="showSearch = !showSearch"><FilterOutlined /></a-button></a-tooltip>
      <a-tooltip title="刷新"><a-button shape="circle" @click="getList"><ReloadOutlined /></a-button></a-tooltip>
    </a-space></template>

    <a-form v-show="showSearch" ref="queryRef" :model="queryParams" layout="inline" class="query-form">
      <a-form-item label="菜单名称" name="menuName"><a-input v-model:value="queryParams.menuName" allow-clear placeholder="请输入菜单名称" @press-enter="handleQuery" /></a-form-item>
      <a-form-item label="状态" name="status"><a-select v-model:value="queryParams.status" allow-clear placeholder="菜单状态" :options="normalDisableOptions" /></a-form-item>
      <a-form-item><a-space><a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button><a-button @click="resetQuery"><ReloadOutlined />重置</a-button></a-space></a-form-item>
    </a-form>

    <a-table v-if="refreshTable" :columns="columns" :data-source="menuList" :loading="loading" :pagination="false" row-key="menuId" :expanded-row-keys="expandedRowKeys" :scroll="{ x: 1050 }" @expanded-rows-change="expandedRowKeys = $event">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'menuName'"><SvgIcon :icon-class="record.icon" /><span class="name-label">{{ record.menuName }}</span></template>
        <template v-else-if="column.key === 'type'"><a-tag :color="menuTypeMeta(record).color">{{ menuTypeMeta(record).label }}</a-tag></template>
        <a-input-number v-else-if="column.key === 'orderNum'" v-model:value="record.orderNum" :min="0" class="sort-input" />
        <dict-tag v-else-if="column.key === 'status'" :options="sys_normal_disable" :value="record.status" />
        <template v-else-if="column.key === 'actions'">
          <a-space class="desktop-row-actions">
            <a-button type="link" @click="handleUpdate(record)" v-hasPermi="['system:menu:edit']"><EditOutlined />修改</a-button>
            <a-button type="link" @click="handleAdd(record)" v-hasPermi="['system:menu:add']"><PlusOutlined />新增</a-button>
            <a-button type="link" danger @click="handleDelete(record)" v-hasPermi="['system:menu:remove']"><DeleteOutlined />删除</a-button>
          </a-space>
          <div v-if="menuRowActions.length" class="mobile-row-actions"><WorkspaceMoreMenu :items="menuRowActions" @command="handleRowCommand($event, record)" /></div>
        </template>
      </template>
    </a-table>

    <ResourceDrawer :open="open" :title="title" :width="680" destroy-on-close @close="cancel">
      <a-form ref="menuRef" :model="form" :rules="rules" layout="vertical">
        <a-row :gutter="16">
          <a-col :span="24"><a-form-item label="上级菜单"><a-tree-select v-model:value="form.parentId" :tree-data="menuOptions" :field-names="menuFieldNames" tree-default-expand-all placeholder="选择上级菜单" /></a-form-item></a-col>
          <a-col :span="24"><a-form-item label="菜单类型" name="menuType"><a-radio-group v-model:value="form.menuType" :options="menuTypeOptions" /></a-form-item></a-col>
          <a-col v-if="form.menuType != 'F'" :xs="24" :md="12"><a-form-item label="菜单图标" name="icon"><a-popover placement="bottomLeft" trigger="click" :overlay-style="{ width: '540px' }"><template #content><IconSelect ref="iconSelectRef" :active-icon="form.icon" @selected="selected" /></template><a-input v-model:value="form.icon" readonly placeholder="点击选择图标" @blur="showSelectIcon"><template #prefix><SvgIcon v-if="form.icon" :icon-class="form.icon" /><SearchOutlined v-else /></template></a-input></a-popover></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="显示排序" name="orderNum"><a-input-number v-model:value="form.orderNum" :min="0" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="菜单名称" name="menuName"><a-input v-model:value="form.menuName" placeholder="请输入菜单名称" /></a-form-item></a-col>
          <a-col v-if="form.menuType == 'C'" :xs="24" :md="12"><a-form-item name="routeName"><template #label><HelpLabel title="路由名称" tip="默认不填则和路由地址相同；特殊情况下请自定义以保证唯一性" /></template><a-input v-model:value="form.routeName" placeholder="请输入路由名称" /></a-form-item></a-col>
          <a-col v-if="form.menuType != 'F'" :xs="24" :md="12"><a-form-item><template #label><HelpLabel title="是否外链" tip="选择是外链则路由地址需要以 http(s):// 开头" /></template><a-radio-group v-model:value="form.isFrame" :options="frameOptions" /></a-form-item></a-col>
          <a-col v-if="form.menuType != 'F'" :xs="24" :md="12"><a-form-item name="path"><template #label><HelpLabel title="路由地址" tip="访问的路由地址；外网地址需内链访问则以 http(s):// 开头" /></template><a-input v-model:value="form.path" placeholder="请输入路由地址" /></a-form-item></a-col>
          <a-col v-if="form.menuType == 'C'" :xs="24" :md="12"><a-form-item name="component"><template #label><HelpLabel title="组件路径" tip="访问的组件路径，默认在 views 目录下" /></template><a-input v-model:value="form.component" placeholder="请输入组件路径" /></a-form-item></a-col>
          <a-col v-if="form.menuType != 'M'" :xs="24" :md="12"><a-form-item><template #label><HelpLabel title="权限字符" tip="控制器中定义的权限字符，如 system:user:list" /></template><a-input v-model:value="form.perms" :maxlength="100" placeholder="请输入权限标识" /></a-form-item></a-col>
          <a-col v-if="form.menuType == 'C'" :xs="24" :md="12"><a-form-item><template #label><HelpLabel title="路由参数" tip="访问路由的默认传递参数" /></template><a-input v-model:value="form.query" :maxlength="255" placeholder="请输入路由参数" /></a-form-item></a-col>
          <a-col v-if="form.menuType == 'C'" :xs="24" :md="12"><a-form-item><template #label><HelpLabel title="是否缓存" tip="缓存时组件 name 需要和地址保持一致" /></template><a-radio-group v-model:value="form.isCache" :options="cacheOptions" /></a-form-item></a-col>
          <a-col v-if="form.menuType != 'F'" :xs="24" :md="12"><a-form-item><template #label><HelpLabel title="显示状态" tip="隐藏后不会出现在侧边栏，但仍然可以访问" /></template><a-radio-group v-model:value="form.visible" :options="showHideOptions" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item><template #label><HelpLabel title="菜单状态" tip="停用后不会出现在侧边栏，也不能被访问" /></template><a-radio-group v-model:value="form.status" :options="normalDisableOptions" /></a-form-item></a-col>
        </a-row>
      </a-form>
      <template #footer><a-button @click="cancel">取 消</a-button><a-button type="primary" @click="submitForm">确 定</a-button></template>
    </ResourceDrawer>
  </WorkspacePage>
</template>

<script setup name="Menu">
import { h, resolveComponent } from 'vue'
import { useWindowSize } from '@vueuse/core'
import { DeleteOutlined, EditOutlined, FilterOutlined, NodeExpandOutlined, PlusOutlined, QuestionCircleOutlined, ReloadOutlined, SaveOutlined, SearchOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import WorkspaceMoreMenu from '@/components/Workspace/WorkspaceMoreMenu.vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import feedback from '@/plugins/antdFeedback'
import { checkPermi } from '@/utils/permission'
import { addMenu, delMenu, getMenu, listMenu, updateMenu, updateMenuSort } from "@/api/system/menu";
import SvgIcon from "@/components/SvgIcon";
import IconSelect from "@/components/IconSelect";

const HelpLabel = (props) => h('span', [h(resolveComponent('a-tooltip'), { title: props.tip }, { default: () => h(QuestionCircleOutlined) }), ' ', props.title]);

const { proxy } = getCurrentInstance();
const { sys_show_hide, sys_normal_disable } = proxy.useDict("sys_show_hide", "sys_normal_disable");

const menuList = ref([]);
const open = ref(false);
const loading = ref(true);
const showSearch = ref(true);
const title = ref("");
const menuOptions = ref([]);
const isExpandAll = ref(false);
const refreshTable = ref(true);
const iconSelectRef = ref(null);
const originalOrders = ref({});
const expandedRowKeys = ref([]);
const queryRef = ref();
const menuRef = ref();
const normalDisableOptions = computed(() => sys_normal_disable.value.map(item => ({ label: item.label, value: item.value })));
const showHideOptions = computed(() => sys_show_hide.value.map(item => ({ label: item.label, value: item.value })));
const menuTypeOptions = [{ label: '目录', value: 'M' }, { label: '菜单', value: 'C' }, { label: '按钮', value: 'F' }];
const frameOptions = [{ label: '是', value: 0 }, { label: '否', value: 1 }];
const cacheOptions = [{ label: '缓存', value: 0 }, { label: '不缓存', value: 1 }];
const menuFieldNames = { value: 'menuId', label: 'menuName', children: 'children' };
const { width: viewportWidth } = useWindowSize();
const columns = computed(() => [
  { title: '菜单名称', key: 'menuName', width: 220, ellipsis: true }, { title: '类型', key: 'type', width: 100 },
  { title: '排序', key: 'orderNum', width: 120 }, { title: '权限标识', dataIndex: 'perms', key: 'perms', ellipsis: true },
  { title: '组件路径', dataIndex: 'component', key: 'component', ellipsis: true }, { title: '状态', key: 'status', width: 80 },
  { title: '操作', key: 'actions', width: viewportWidth.value <= 767 ? 64 : 230, align: 'center' },
]);
const menuRowActions = computed(() => [
  checkPermi(['system:menu:edit']) && { command: 'edit', label: '修改', icon: EditOutlined },
  checkPermi(['system:menu:add']) && { command: 'add', label: '新增', icon: PlusOutlined },
  checkPermi(['system:menu:remove']) && { command: 'delete', label: '删除', icon: DeleteOutlined, danger: true, divided: true },
].filter(Boolean));
const menuTypeMeta = row => ['M', 'C'].includes(row.menuType) && row.isFrame === 0 ? { label: '外链', color: 'red' } : row.menuType === 'M' ? { label: '目录', color: 'blue' } : row.menuType === 'C' ? { label: '菜单', color: 'green' } : { label: '按钮', color: 'orange' };
const collectMenuKeys = list => list.flatMap(item => [item.menuId, ...collectMenuKeys(item.children || [])]);

const data = reactive({
  form: {},
  queryParams: {
    menuName: undefined,
    visible: undefined
  },
  rules: {
    menuName: [{ required: true, message: "菜单名称不能为空", trigger: "blur" }],
    orderNum: [{ required: true, message: "菜单顺序不能为空", trigger: "blur" }],
    path: [{ required: true, message: "路由地址不能为空", trigger: "blur" }]
  },
});

const { queryParams, form, rules } = toRefs(data);

/** 查询菜单列表 */
function getList() {
  loading.value = true;
  listMenu(queryParams.value).then(response => {
    menuList.value = proxy.handleTree(response.data, "menuId");
    const availableKeys = collectMenuKeys(menuList.value);
    expandedRowKeys.value = isExpandAll.value ? availableKeys : expandedRowKeys.value.filter(key => availableKeys.includes(key));
    recordOriginalOrders(menuList.value);
    loading.value = false;
  });
}
/** 查询菜单下拉树结构 */
function getTreeselect() {
  menuOptions.value = [];
  listMenu().then(response => {
    const menu = { menuId: 0, menuName: "主类目", children: [] };
    menu.children = proxy.handleTree(response.data, "menuId");
    menuOptions.value.push(menu);
  });
}
/** 取消按钮 */
function cancel() {
  open.value = false;
  reset();
}
/** 表单重置 */
function reset() {
  form.value = {
    menuId: undefined,
    parentId: 0,
    menuName: undefined,
    routeName: undefined,
    icon: undefined,
    menuType: "M",
    orderNum: undefined,
    isFrame: 1,
    isCache: 0,
    visible: "0",
    status: "0"
  };
  nextTick(() => menuRef.value?.clearValidate());
}
/** 展示下拉图标 */
function showSelectIcon() {
  iconSelectRef.value?.reset();
}
/** 选择图标 */
function selected(name) {
  form.value.icon = name;
}
/** 搜索按钮操作 */
function handleQuery() {
  getList();
}
/** 重置按钮操作 */
function resetQuery() {
  queryRef.value?.resetFields();
  handleQuery();
}
/** 新增按钮操作 */
function handleAdd(row) {
  reset();
  getTreeselect();
  if (row != null && row.menuId) {
    form.value.parentId = row.menuId;
  } else {
    form.value.parentId = 0;
  }
  open.value = true;
  title.value = "添加菜单";
}
/** 展开/折叠操作 */
function toggleExpandAll() {
  refreshTable.value = false;
  isExpandAll.value = !isExpandAll.value;
  expandedRowKeys.value = isExpandAll.value ? collectMenuKeys(menuList.value) : [];
  nextTick(() => {
    refreshTable.value = true;
  });
}
/** 修改按钮操作 */
async function handleUpdate(row) {
  reset();
  await getTreeselect();
  getMenu(row.menuId).then(response => {
    form.value = response.data;
    open.value = true;
    title.value = "修改菜单";
  });
}
/** 提交按钮 */
async function submitForm() {
  const valid = await menuRef.value.validateFields().then(() => true).catch(() => false);
  if (valid) {
      if (form.value.menuId != undefined) {
        updateMenu(form.value).then(response => {
          feedback.msgSuccess("修改成功");
          open.value = false;
          getList();
        });
      } else {
        addMenu(form.value).then(response => {
          feedback.msgSuccess("新增成功");
          open.value = false;
          getList();
        });
      }
  }
}

/** 递归记录原始排序 */
function recordOriginalOrders(list) {
  list.forEach(item => {
    originalOrders.value[item.menuId] = item.orderNum;
    if (item.children && item.children.length) {
      recordOriginalOrders(item.children);
    }
  });
}

/** 保存排序 */
function handleSaveSort() {
  const changedMenuIds = [];
  const changedOrderNums = [];
  const collectChanged = (list) => {
    list.forEach(item => {
      if (String(originalOrders.value[item.menuId]) !== String(item.orderNum)) {
        changedMenuIds.push(item.menuId);
        changedOrderNums.push(item.orderNum);
      }
      if (item.children && item.children.length) {
        collectChanged(item.children);
      }
    });
  };
  collectChanged(menuList.value);
  if (changedMenuIds.length === 0) {
    feedback.msgWarning("未检测到排序修改");
    return;
  }
  updateMenuSort({ menuIds: changedMenuIds.join(","), orderNums: changedOrderNums.join(",") }).then(() => {
    feedback.msgSuccess("排序保存成功");
    recordOriginalOrders(menuList.value);
  });
}

/** 删除按钮操作 */
function handleDelete(row) {
  feedback.confirm('是否确认删除名称为"' + row.menuName + '"的数据项?').then(function() {
    return delMenu(row.menuId);
  }).then(() => {
    getList();
    feedback.msgSuccess("删除成功");
  }).catch(() => {});
}
function handleRowCommand(command, row) {
  const actions = { edit: handleUpdate, add: handleAdd, delete: handleDelete };
  actions[command]?.(row);
}

getList();
</script>

<style scoped>
.query-form { margin-bottom: 16px; gap: 8px 0; }.query-form :deep(.ant-input), .query-form :deep(.ant-select) { width: 200px; }
.name-label { margin-left: 6px; }.sort-input { width: 88px; }
.desktop-row-actions { display: inline-flex; }.mobile-row-actions { display: none; }
@media (max-width: 767px) { .query-form { display: grid; }.query-form :deep(.ant-form-item), .query-form :deep(.ant-input), .query-form :deep(.ant-select) { width: 100%; }.desktop-row-actions { display: none; }.mobile-row-actions { display: flex; justify-content: center; } }
</style>
