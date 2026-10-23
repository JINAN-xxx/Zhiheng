<template>
  <WorkspacePage title="缓存列表" subtitle="查看并清理 Redis 缓存键"><template #actions><a-button danger @click="handleClearCacheAll"><DeleteOutlined />清理全部</a-button></template>
    <a-row :gutter="16"><a-col :xs="24" :lg="8"><a-card><template #title>缓存列表</template><template #extra><a-button type="text" @click="refreshCacheNames"><ReloadOutlined /></a-button></template><a-table :columns="nameColumns" :data-source="cacheNames" :loading="loading" :pagination="false" row-key="cacheName" :custom-row="nameRow"><template #bodyCell="{column,record,index}"><template v-if="column.key==='index'">{{index+1}}</template><template v-else-if="column.key==='cacheName'">{{nameFormatter(record)}}</template><template v-else-if="column.key==='action'"><a-button type="link" danger @click.stop="handleClearCacheName(record)"><DeleteOutlined /></a-button></template></template></a-table></a-card></a-col>
    <a-col :xs="24" :lg="8"><a-card><template #title>键名列表</template><template #extra><a-button type="text" @click="refreshCacheKeys"><ReloadOutlined /></a-button></template><a-table :columns="keyColumns" :data-source="cacheKeys" :loading="subLoading" :pagination="false" :row-key="(row)=>row" :custom-row="keyRow"><template #bodyCell="{column,record,index}"><template v-if="column.key==='index'">{{index+1}}</template><template v-else-if="column.key==='key'">{{keyFormatter(record)}}</template><template v-else-if="column.key==='action'"><a-button type="link" danger @click.stop="handleClearCacheKey(record)"><DeleteOutlined /></a-button></template></template></a-table></a-card></a-col>
    <a-col :xs="24" :lg="8"><a-card title="缓存内容"><a-form layout="vertical"><a-form-item label="缓存名称"><a-input :value="cacheForm.cacheName" readonly /></a-form-item><a-form-item label="缓存键名"><a-input :value="cacheForm.cacheKey" readonly /></a-form-item><a-form-item label="缓存内容"><a-textarea :value="cacheForm.cacheValue" :rows="8" readonly /></a-form-item></a-form></a-card></a-col></a-row>
  </WorkspacePage>
</template>
<script setup name="CacheList">
import { DeleteOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import feedback from '@/plugins/antdFeedback'
import {listCacheName,listCacheKey,getCacheValue,clearCacheName,clearCacheKey,clearCacheAll} from '@/api/monitor/cache'
const cacheNames=ref([]),cacheKeys=ref([]),cacheForm=ref({}),loading=ref(true),subLoading=ref(false),nowCacheName=ref('')
const nameColumns=[{title:'序号',key:'index',width:60},{title:'缓存名称',key:'cacheName',ellipsis:true},{title:'备注',dataIndex:'remark',ellipsis:true},{title:'操作',key:'action',width:60}]
const keyColumns=[{title:'序号',key:'index',width:60},{title:'缓存键名',key:'key',ellipsis:true},{title:'操作',key:'action',width:60}]
async function getCacheNames(){loading.value=true;try{cacheNames.value=(await listCacheName()).data||[]}finally{loading.value=false}}
function refreshCacheNames(){getCacheNames();feedback.msgSuccess('刷新缓存列表成功')}
async function handleClearCacheName(row){await clearCacheName(row.cacheName);feedback.msgSuccess(`清理缓存名称[${row.cacheName}]成功`);getCacheKeys()}
async function getCacheKeys(row){const name=row?.cacheName||nowCacheName.value;if(!name)return;subLoading.value=true;try{cacheKeys.value=(await listCacheKey(name)).data||[];nowCacheName.value=name}finally{subLoading.value=false}}
function refreshCacheKeys(){getCacheKeys();feedback.msgSuccess('刷新键名列表成功')}
async function handleClearCacheKey(key){await clearCacheKey(key);feedback.msgSuccess(`清理缓存键名[${key}]成功`);getCacheKeys()}
const nameFormatter=row=>row.cacheName.replace(':',''),keyFormatter=key=>key.replace(nowCacheName.value,'')
async function handleCacheValue(key){cacheForm.value=(await getCacheValue(nowCacheName.value,key)).data}
async function handleClearCacheAll(){await clearCacheAll();feedback.msgSuccess('清理全部缓存成功');getCacheNames();cacheKeys.value=[];cacheForm.value={}}
const nameRow=record=>({onClick:()=>getCacheKeys(record)}),keyRow=record=>({onClick:()=>handleCacheValue(record)})
getCacheNames()
</script>
<style scoped>:deep(.ant-card){height:calc(100vh - 150px);overflow:auto}@media(max-width:991px){:deep(.ant-card){height:auto;margin-bottom:16px}}</style>
