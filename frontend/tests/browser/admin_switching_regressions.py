"""Switching keeps state and remains bound to current identity authority."""
import asyncio
import contextlib
import re
import unittest
from urllib.parse import urlparse
from playwright.async_api import expect
import admin_regressions as base
from files_regressions import FILE


class AdminSwitchingRegressions(base.AdminRegressions):
    async def group(self,name):
        return self.page.get_by_role('navigation',name='后台管理导航',exact=True).get_by_role('link',name=name,exact=True)

    def count(self,path):
        return sum(method=='GET' and target==path for method,target,_,_ in self.requests)

    async def test_return_restores_search_page_and_does_not_repeat_recent_list_request(self):
        self.users.extend({**self.users[1],'id':f'extra-{i}','username':f'user-{i}','display_name':f'用户{i}'} for i in range(40))
        await self.open('users')
        search=self.page.get_by_role('textbox',name='搜索',exact=True)
        await search.fill('page-state')
        await search.press('Enter')
        await self.page.locator('.ant-pagination-item-2').click()
        await expect(self.page.locator('.ant-pagination-item-2')).to_have_class(re.compile('active'))
        await expect(self.page.get_by_text('用户20',exact=True)).to_be_visible()
        count=self.count('/api/admin/users')
        await self.page.locator('.admin-navigation').evaluate('el=>window.originalAdminNavigation=el')
        menu_count=self.count('/api/admin/menus')
        await (await self.group('角色权限')).click()
        await expect(self.page.get_by_text('运营员',exact=True)).to_be_visible()
        await (await self.group('用户管理')).click()
        await expect(search).to_have_value('page-state')
        await expect(self.page.locator('.ant-pagination-item-2')).to_have_class(re.compile('active'))
        await expect(self.page.get_by_text('用户20',exact=True)).to_be_visible()
        self.assertEqual(self.count('/api/admin/users'),count)
        self.assertEqual(self.count('/api/admin/menus'),menu_count)
        self.assertTrue(await self.page.locator('.admin-navigation').evaluate('el=>el===window.originalAdminNavigation'))

    async def test_slow_identity_check_keeps_content_and_moves_selection_immediately(self):
        await self.open('users')
        await expect(self.page.get_by_text('小艾',exact=True)).to_be_visible()
        started,release=asyncio.Event(),asyncio.Event()
        async def identity(route):
            started.set()
            await release.wait()
            with contextlib.suppress(Exception):await route.fulfill(json=self.user)
        await self.page.route('**/api/auth/me',identity)
        try:
            await (await self.group('角色权限')).click()
            await asyncio.wait_for(started.wait(),5)
            await expect(await self.group('角色权限')).to_have_class(re.compile('is-active'))
            await expect(self.page.locator('.admin-content-page h1')).to_have_text('用户管理')
            await expect(self.page.get_by_text('小艾',exact=True)).to_be_visible()
            await expect(self.page.locator('.admin-content-page')).to_have_attribute('inert','')
            release.set()
            await expect(self.page.get_by_role('heading',name='角色权限',exact=True)).to_be_visible()
        finally:release.set()

    async def test_rapid_switch_finishes_on_latest_destination(self):
        await self.open('users')
        await expect(self.page.get_by_text('小艾',exact=True)).to_be_visible()
        started,release=asyncio.Event(),asyncio.Event()
        async def identity(route):
            started.set();await release.wait()
            with contextlib.suppress(Exception):await route.fulfill(json=self.user)
        await self.page.route('**/api/auth/me',identity)
        try:
            await (await self.group('角色权限')).click()
            await asyncio.wait_for(started.wait(),5)
            await (await self.group('通知公告')).click()
            await expect(await self.group('通知公告')).to_have_class(re.compile('is-active'))
            release.set()
            await expect(self.page).to_have_url(self.origin+'/admin/notices')
            await expect(self.page.get_by_text('维护安排',exact=True)).to_be_visible()
            self.assertEqual(self.count('/api/admin/roles'),0)
        finally:release.set()

    async def test_mutation_invalidates_other_cached_pages(self):
        await self.open('roles')
        await expect(self.page.get_by_text('运营员',exact=True)).to_be_visible()
        before=self.count('/api/admin/roles')
        await (await self.group('用户管理')).click()
        await self.page.get_by_role('row').filter(has_text='小艾').get_by_role('button',name='编辑',exact=True).click()
        dialog=self.page.get_by_role('dialog')
        await dialog.get_by_label('显示名称',exact=True).fill('更新用户')
        await dialog.get_by_role('button',name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).to_be_hidden()
        await (await self.group('角色权限')).click()
        await expect(self.page.get_by_text('运营员',exact=True)).to_be_visible()
        self.assertEqual(self.count('/api/admin/roles'),before+1)

    async def test_cached_page_is_not_exposed_after_permission_revocation(self):
        self.users[1]['display_name']='CACHE_PRIVATE_USER'
        await self.open('users')
        await expect(self.page.get_by_text('CACHE_PRIVATE_USER',exact=True)).to_be_visible()
        await (await self.group('角色权限')).click()
        await expect(self.page.get_by_text('运营员',exact=True)).to_be_visible()
        await self.page.evaluate('''() => {
          window.cacheExposure=false;
          new MutationObserver(()=>{if(document.body.textContent.includes('CACHE_PRIVATE_USER'))window.cacheExposure=true}).observe(document.body,{childList:true,subtree:true});
        }''')
        self.user={'id':'self','username':'reader','is_admin':False,'permissions':['system:roles:read']}
        await (await self.group('用户管理')).click()
        await expect(self.page).to_have_url(self.origin+'/chat')
        self.assertFalse(await self.page.evaluate('window.cacheExposure'))

    async def test_failed_switch_keeps_current_page_usable(self):
        await self.open('users')
        await expect(self.page.get_by_text('小艾',exact=True)).to_be_visible()
        await self.page.route('**/api/auth/me',lambda route:route.fulfill(status=503,json={'error':{'code':'unavailable','message':'身份服务暂时不可用'}}))
        await (await self.group('角色权限')).click()
        await expect(self.page.get_by_text('身份服务暂时不可用',exact=False)).to_be_visible()
        await expect(self.page.get_by_role('heading',name='用户管理',exact=True)).to_be_visible()
        await expect(await self.group('用户管理')).to_have_class(re.compile('is-active'))
        await expect(self.page.get_by_role('button',name='新建用户',exact=True)).to_be_enabled()

    async def test_no_edit_dialog_survives_a_cached_return(self):
        await self.open('roles')
        await (await self.group('用户管理')).click()
        await self.page.get_by_role('row').filter(has_text='小艾').get_by_role('button',name='编辑',exact=True).click()
        await expect(self.page.get_by_role('dialog')).to_be_visible()
        await self.page.go_back()
        await expect(self.page.get_by_role('heading',name='角色权限',exact=True)).to_be_visible()
        await (await self.group('用户管理')).click()
        await expect(self.page.get_by_text('小艾',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('dialog')).to_have_count(0)

    async def test_reduced_motion_does_not_animate_page_entry(self):
        await self.page.emulate_media(reduced_motion='reduce')
        await self.open('users')
        await (await self.group('角色权限')).click()
        await expect(self.page.get_by_role('heading',name='角色权限',exact=True)).to_be_visible()
        animation=await self.page.locator('.admin-content-page').evaluate('el=>getComputedStyle(el).animationName')
        self.assertEqual(animation,'none')

    async def test_expired_cache_keeps_rows_visible_during_background_refresh(self):
        await self.open('users')
        await expect(self.page.get_by_text('小艾',exact=True)).to_be_visible()
        await (await self.group('角色权限')).click()
        await expect(self.page.get_by_text('运营员',exact=True)).to_be_visible()
        await self.page.evaluate('() => {const now=Date.now;Date.now=()=>now()+20000}')
        started,release=asyncio.Event(),asyncio.Event()
        async def delayed(route):
            started.set();await release.wait()
            with contextlib.suppress(Exception):await self.respond(route)
        await self.page.route('**/api/admin/users?*',delayed)
        try:
            await (await self.group('用户管理')).click()
            await asyncio.wait_for(started.wait(),5)
            await expect(self.page.get_by_text('小艾',exact=True)).to_be_visible()
            self.users[1]['display_name']='最新用户'
            release.set()
            await expect(self.page.get_by_text('最新用户',exact=True)).to_be_visible()
        finally:release.set()

    async def test_monitor_returns_without_refetch_and_manual_refresh_still_fetches(self):
        await self.open('monitor')
        await expect(self.page.get_by_text('PostgreSQL 17',exact=True)).to_be_visible()
        before=self.count('/api/admin/monitor')
        await (await self.group('用户管理')).click()
        await expect(self.page.get_by_text('小艾',exact=True)).to_be_visible()
        await (await self.group('系统运维')).click()
        await expect(self.page.get_by_text('PostgreSQL 17',exact=True)).to_be_visible()
        self.assertEqual(self.count('/api/admin/monitor'),before)
        async with self.page.expect_response('**/api/admin/monitor'):
            await self.page.get_by_role('button',name='刷新系统状态',exact=True).click()
        self.assertEqual(self.count('/api/admin/monitor'),before+1)

    async def test_log_filters_restore_independently_between_login_and_operation(self):
        await self.open('login-logs')
        await expect(self.page.get_by_text('用户已更新',exact=True)).to_be_visible()
        async with self.page.expect_response(lambda response:'/api/admin/logs?' in response.url and 'outcome=failed' in response.url):
            await self.select_value(self.page.locator('.admin-filter'),'失败')
        await expect(self.page.get_by_role('row').filter(has_text='用户已更新')).to_be_visible()
        before=self.count('/api/admin/logs')
        tabs=self.page.get_by_role('navigation',name='系统运维子页面',exact=True)
        await tabs.get_by_role('link',name='操作日志',exact=True).click()
        await expect(self.page.get_by_role('heading',name='操作日志',exact=True)).to_be_visible()
        await expect(self.page.locator('.admin-filter')).to_contain_text('全部结果')
        await tabs.get_by_role('link',name='登录日志',exact=True).click()
        await expect(self.page.get_by_role('heading',name='登录日志',exact=True)).to_be_visible()
        await expect(self.page.locator('.admin-filter')).to_contain_text('失败')
        self.assertEqual(self.count('/api/admin/logs'),before+1)

    async def test_storage_returns_with_search_and_statistics_without_reloading(self):
        async def storage(route):
            path=urlparse(route.request.url).path
            self.requests.append(('GET',path,{},None))
            if path=='/api/files':
                body={'items':[FILE],'total':1}
            elif path=='/api/files/stats':
                body={'total_count':1,'total_bytes':12,'active_count':1,'deleted_count':0,'public_count':0,'public_bytes':0,'private_bytes':12,'expired_count':0,'retention_expiring_count':0,'acl_expiring_count':0}
            elif path=='/api/files/users':body=[{'id':'user-1','username':'admin'}]
            else:return await route.fulfill(status=404,json={})
            await route.fulfill(json=body)
        await self.page.route('**/api/files**',storage)
        await self.open('storage')
        await expect(self.page.get_by_role('button',name='查看文件：业务报告',exact=True)).to_be_visible()
        await self.page.get_by_role('textbox',name='搜索文件名').fill('report')
        await self.page.get_by_role('button',name='搜索文件',exact=True).click()
        await expect(self.page.get_by_role('button',name='刷新文件',exact=True)).to_be_enabled()
        await self.page.get_by_role('button',name='高级筛选',exact=True).click()
        await self.page.get_by_role('button',name='高级管理',exact=True).click()
        await self.page.get_by_role('menuitem',name='详细统计',exact=True).click()
        before={path:self.count(path) for path in ['/api/files','/api/files/stats','/api/files/users']}
        await (await self.group('用户管理')).click()
        await expect(self.page.get_by_text('小艾',exact=True)).to_be_visible()
        await (await self.group('存储管理')).click()
        await expect(self.page.get_by_role('button',name='查看文件：业务报告',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('textbox',name='搜索文件名')).to_have_value('report')
        await expect(self.page.get_by_role('button',name='高级筛选',exact=True)).to_have_attribute('aria-expanded','true')
        await expect(self.page.locator('.file-statistics')).to_be_visible()
        self.assertEqual({path:self.count(path) for path in before},before)


if __name__=='__main__':unittest.main(verbosity=2)
