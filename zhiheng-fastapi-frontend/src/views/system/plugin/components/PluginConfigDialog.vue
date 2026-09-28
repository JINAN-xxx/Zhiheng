<template>
   <ResourceDrawer :title="title" :open="modelValue" :width="720" destroy-on-close @close="emit('update:modelValue', false)">
      <a-form ref="configRef" :model="configForm" layout="vertical">
         <a-empty v-if="!items.length" description="暂无插件配置" />
         <div v-for="group in configGroups" :key="group.name" class="plugin-config-section">
            <div v-if="configGroups.length > 1 || group.name !== 'default'" class="plugin-config-section-title">{{ formatConfigGroupLabel(group.name) }}</div>
            <a-form-item
               v-for="item in group.items"
               :key="item.key"
               :label="item.label || item.key"
               :name="['values', item.key]"
               :rules="getConfigItemRules(item)"
               class="plugin-config-item"
            >
               <a-switch v-if="item.type === 'boolean'" v-model:checked="configForm.values[item.key]" />
               <a-input-number
                  v-else-if="item.type === 'number'"
                  v-model:value="configForm.values[item.key]"
                  :min="item.min ?? undefined"
                  :max="item.max ?? undefined"
                  :placeholder="getConfigInputPlaceholder(item)"
                  class="plugin-config-control"
               />
               <a-select
                  v-else-if="item.type === 'select'"
                  v-model:value="configForm.values[item.key]"
                  :placeholder="getConfigInputPlaceholder(item)"
                  class="plugin-config-control"
                  :options="item.options || []"
               />
               <template v-else-if="item.type === 'textarea' || item.type === 'json'">
                  <a-textarea
                     v-model:value="configForm.values[item.key]"
                     :rows="4"
                     :placeholder="getConfigInputPlaceholder(item)"
                     class="plugin-config-control"
                  />
                  <div v-if="item.type === 'json'" class="config-inline-actions">
                     <a-button type="link" @click="formatConfigJsonValue(item)"><FormatPainterOutlined />格式化 JSON</a-button>
                  </div>
               </template>
               <a-input-password
                  v-else-if="isSecretConfigItem(item)"
                  v-model:value="configForm.values[item.key]"
                  :placeholder="getConfigInputPlaceholder(item)"
                  class="plugin-config-control"
               />
               <a-input v-else v-model:value="configForm.values[item.key]" :placeholder="getConfigInputPlaceholder(item)" class="plugin-config-control" />
               <div class="config-help">
                  <span v-if="item.description" class="config-help-text">{{ item.description }}</span>
                  <div v-if="hasConfigMeta(item)" class="config-meta-list">
                     <a-tag>{{ item.key }}</a-tag>
                     <a-tag v-if="item.required" color="orange">必填</a-tag>
                     <a-tag v-if="item.secret" color="orange">敏感</a-tag>
                     <a-tag v-if="formatConfigDefaultValue(item) !== '-'">默认 {{ formatConfigDefaultValue(item) }}</a-tag>
                     <a-tag v-if="formatConfigConstraint(item) !== '-'">{{ formatConfigConstraint(item) }}</a-tag>
                  </div>
               </div>
            </a-form-item>
         </div>
      </a-form>
      <template #footer>
         <div class="dialog-footer">
            <a-button
               type="primary"
               :disabled="!items.length"
               :loading="loading"
               @click="submit"
               v-hasPermi="['system:plugin:edit']"
            >保 存</a-button>
            <a-button @click="emit('update:modelValue', false)">关 闭</a-button>
         </div>
      </template>
   </ResourceDrawer>
</template>

<script setup name="PluginConfigDialog">
import { computed, reactive, ref, watch } from "vue";
import { FormatPainterOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import feedback from '@/plugins/antdFeedback'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  title: {
    type: String,
    default: "插件配置"
  },
  items: {
    type: Array,
    default: () => []
  },
  loading: {
    type: Boolean,
    default: false
  },
  formatConfigDefaultValue: {
    type: Function,
    required: true
  },
  formatConfigConstraint: {
    type: Function,
    required: true
  }
});

const emit = defineEmits(["update:modelValue", "submit"]);
const configRef = ref(null);
const configForm = reactive({
  values: {}
});

const configGroups = computed(() => {
  const groups = new Map();
  const sortedItems = [...props.items].sort((left, right) => {
    const leftGroup = left.group || "default";
    const rightGroup = right.group || "default";
    if (leftGroup !== rightGroup) {
      return leftGroup.localeCompare(rightGroup);
    }
    const leftOrder = Number(left.order || 0);
    const rightOrder = Number(right.order || 0);
    if (leftOrder !== rightOrder) {
      return leftOrder - rightOrder;
    }
    return String(left.key || "").localeCompare(String(right.key || ""));
  });
  sortedItems.forEach(item => {
    const groupName = item.group || "default";
    if (!groups.has(groupName)) {
      groups.set(groupName, []);
    }
    groups.get(groupName).push(item);
  });
  return Array.from(groups, ([name, items]) => ({ name, items }));
});

watch(
  () => props.items,
  items => {
    configForm.values = {};
    items.forEach(item => {
      configForm.values[item.key] = normalizeConfigValueForForm(item);
    });
  },
  { immediate: true }
);

async function submit() {
  const valid = await configRef.value?.validateFields().then(() => true).catch(() => false);
  if (!valid) return;
    let values;
    try {
      values = buildConfigSubmitValues();
    } catch (error) {
      feedback.msgError(error.message || "配置格式不正确");
      return;
    }
    emit("submit", values);
}

function normalizeConfigValueForForm(item) {
  if (item.type !== "json") {
    return item.value;
  }
  if (item.value === null || item.value === undefined || item.value === "") {
    return "";
  }
  if (typeof item.value === "string") {
    return item.value;
  }
  return JSON.stringify(item.value, null, 2);
}

function buildConfigSubmitValues() {
  return props.items.reduce((values, item) => {
    const value = configForm.values[item.key];
    if (item.type === "json" && typeof value === "string" && value.trim()) {
      try {
        values[item.key] = JSON.parse(value);
      } catch {
        throw new Error((item.label || item.key) + " 不是合法 JSON");
      }
      return values;
    }
    values[item.key] = value;
    return values;
  }, {});
}

function getConfigItemRules(item) {
  const rules = [];
  if (item.required) {
    rules.push({ required: true, message: "不能为空", trigger: item.type === "select" ? "change" : "blur" });
  }
  if (item.type === "number") {
    rules.push({ validator: buildNumberConfigValidator(item), trigger: "change" });
  }
  if (item.pattern && ["string", "textarea", "password"].includes(item.type)) {
    rules.push({ validator: buildPatternConfigValidator(item), trigger: "blur" });
  }
  if (item.type === "json") {
    rules.push({ validator: validateJsonConfig, trigger: "blur" });
  }
  return rules;
}

function isSecretConfigItem(item) {
  return item.type === "password" || item.secret;
}

function buildNumberConfigValidator(item) {
  return (_rule, value, callback) => {
    if (value === undefined || value === null || value === "") {
      callback();
      return;
    }
    const numberValue = Number(value);
    if (Number.isNaN(numberValue)) {
      callback(new Error("请输入数字"));
      return;
    }
    if (item.min !== undefined && item.min !== null && numberValue < Number(item.min)) {
      callback(new Error("不能小于 " + item.min));
      return;
    }
    if (item.max !== undefined && item.max !== null && numberValue > Number(item.max)) {
      callback(new Error("不能大于 " + item.max));
      return;
    }
    callback();
  };
}

function buildPatternConfigValidator(item) {
  return (_rule, value, callback) => {
    if (value === undefined || value === null || value === "") {
      callback();
      return;
    }
    try {
      if (!new RegExp(item.pattern).test(String(value))) {
        callback(new Error("格式不符合要求"));
        return;
      }
    } catch {
      callback(new Error("配置正则表达式无效"));
      return;
    }
    callback();
  };
}

function validateJsonConfig(_rule, value, callback) {
  if (value === undefined || value === null || value === "") {
    callback();
    return;
  }
  try {
    JSON.parse(value);
    callback();
  } catch {
    callback(new Error("请输入合法 JSON"));
  }
}

function formatConfigJsonValue(item) {
  const value = configForm.values[item.key];
  if (value === undefined || value === null || value === "") {
    return;
  }
  try {
    configForm.values[item.key] = JSON.stringify(typeof value === "string" ? JSON.parse(value) : value, null, 2);
  } catch {
    feedback.msgError((item.label || item.key) + " 不是合法 JSON");
  }
}

function getConfigInputPlaceholder(item) {
  return item.placeholder || item.description || "";
}

function hasConfigMeta(item) {
  return Boolean(
    item.key ||
      item.required ||
      item.secret ||
      props.formatConfigDefaultValue(item) !== "-" ||
      props.formatConfigConstraint(item) !== "-"
  );
}

function formatConfigGroupLabel(groupName) {
  const groupMap = {
    default: "基础配置",
    model: "模型配置",
    security: "安全配置",
    advanced: "高级配置"
  };
  return groupMap[groupName] || groupName;
}
</script>

<style scoped>
.config-help {
  width: 100%;
  margin-top: 4px;
  color: var(--workspace-text-secondary);
  font-size: 12px;
  line-height: 18px;
}

.config-help-text {
  display: block;
}

.config-inline-actions {
  width: 100%;
  margin-top: 4px;
  line-height: 20px;
}

.config-meta-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.plugin-config-control {
  width: 320px;
  max-width: 100%;
}

.plugin-config-control :deep(.ant-input-affix-wrapper) {
  width: 100%;
}

.plugin-config-item {
  margin-bottom: 18px;
}

.plugin-config-section + .plugin-config-section {
  margin-top: 6px;
}

.plugin-config-section-title {
  padding-left: 0;
  margin: 0 0 10px;
  color: var(--workspace-text-secondary);
  font-size: 13px;
  font-weight: 600;
}
</style>
