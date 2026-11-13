"""Grouped admin workflows and consistent UI, against local HTTP fixtures only."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import unittest

from playwright.async_api import expect
import admin_regressions as base


class AdminDesignRegressions(base.AdminRegressions):
    async def test_five_main_groups_and_two_user_pages_share_one_layout(self):
        await self.open('users')
        nav=self.page.get_by_role('navigation',name='后台管理导航',exact=True)
        await expect(nav.get_by_role('link')).to_have_count(5)
        await expect(self.page.locator('.admin-section-navigation a')).to_have_count(3)
        await expect(self.page.locator('.admin-section-navigation').get_by_role('link',name='应用治理',exact=True)).to_be_visible()
        self.assertGreater((await nav.bounding_box())['width'],900)
        await expect(self.page.locator('aside.admin-navigation')).to_have_count(0)
        await self.page.locator('.admin-section-navigation').get_by_role('link',name='部门',exact=True).click()
        await expect(self.page.get_by_role('heading',name='部门管理',exact=True)).to_be_visible()
        await expect(nav.get_by_role('link',name='用户管理',exact=True)).to_have_class(re.compile('is-active'))
        await expect(self.page.locator('.admin-section-navigation').get_by_role('link',name='岗位',exact=True)).to_have_count(0)

    async def test_notice_display_switch_and_content_are_in_one_place(self):
        await self.open('notices')
        await expect(self.page.get_by_role('heading',name='通知公告',exact=True)).to_be_visible()
        await expect(self.page.get_by_text('维护安排',exact=True)).to_be_visible()
        switch=self.page.get_by_role('switch',name='在平台展示公告',exact=True)
        await expect(switch).to_be_enabled()
        await switch.click()
        await expect(switch).not_to_be_checked()
        await expect(self.page.get_by_role('navigation',name='后台管理导航').get_by_role('link',name='参数管理',exact=True)).to_have_count(0)

    async def test_notice_reader_never_fetches_or_changes_configuration(self):
        self.user={'id':'self','username':'notice-reader','is_admin':False,'permissions':['system:notices:read']}
        await self.open('notices')
        await expect(self.page.get_by_text('维护安排',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('switch',name='在平台展示公告')).to_have_count(0)
        self.assertFalse(any(path.startswith('/api/admin/configs') for _,path,_,_ in self.requests))

    async def test_configuration_only_reader_uses_notice_group_without_notice_requests(self):
        self.user={'id':'self','username':'config-reader','is_admin':False,'permissions':['system:configs:read']}
        await self.page.goto(self.origin+'/admin')
        await expect(self.page).to_have_url(self.origin+'/admin/configs')
        await expect(self.page.get_by_role('navigation',name='后台管理导航').get_by_role('link')).to_have_count(1)
        await expect(self.page.get_by_role('switch',name='在平台展示公告')).to_be_disabled()
        self.assertFalse(any(path=='/api/admin/notices' for _,path,_,_ in self.requests))

    async def test_user_options_loading_can_be_cancelled_without_a_write(self):
        started,release=asyncio.Event(),asyncio.Event()
        async def options(route):
            started.set()
            await release.wait()
            with contextlib.suppress(Exception):await route.fulfill(json={'departments':[],'posts':[],'roles':[]})
        await self.page.route('**/api/admin/users/options',options)
        try:
            await self.open('users')
            await self.page.get_by_role('row').filter(has_text='小艾').get_by_role('button',name='编辑',exact=True).click()
            await asyncio.wait_for(started.wait(),5)
            dialog=self.page.get_by_role('dialog')
            await expect(dialog.get_by_role('button',name=re.compile(r'^保\s*存$'))).to_be_disabled()
            await dialog.get_by_role('button',name=re.compile(r'^取\s*消$')).click()
            await expect(dialog).to_be_hidden()
            self.assertFalse(any(method in ['POST','PATCH','DELETE'] for method,_,_,_ in self.requests))
        finally:release.set()

    async def test_mobile_admin_has_no_page_overflow_and_active_group_is_visible(self):
        await self.page.set_viewport_size({'width':375,'height':812})
        await self.open('monitor')
        active=self.page.get_by_role('navigation',name='后台管理导航').get_by_role('link',name='系统运维',exact=True)
        await expect(active).to_be_in_viewport()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        await self.page.get_by_role('navigation',name='系统运维子页面',exact=True).get_by_role('link',name='在线会话',exact=True).click()
        await expect(self.page.get_by_role('heading',name='在线会话',exact=True)).to_be_visible()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))

    async def test_notice_update_conflict_keeps_the_authoritative_value(self):
        async def conflict(route):
            self.configs[0].update(value='true',version=7)
            await route.fulfill(status=409,json={'error':{'code':'version_conflict','message':'设置已更新'}})
        await self.page.route('**/api/admin/configs/config-1',conflict)
        await self.open('notices')
        switch=self.page.get_by_role('switch',name='在平台展示公告')
        await expect(switch).to_be_enabled()
        await switch.click()
        await expect(self.page.get_by_text('设置已更新',exact=False)).to_be_visible()
        await expect(switch).to_be_checked()


    async def test_permission_selection_preserves_existing_codes_outside_the_catalog(self):
        self.roles[0]['permissions']=['historical:permission','system:notices:read']
        await self.open('roles')
        await self.page.get_by_role('button',name='编辑',exact=True).click()
        dialog=self.page.get_by_role('dialog')
        await dialog.get_by_label('管理公告',exact=True).check()
        await dialog.get_by_role('button',name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).to_be_hidden()
        body=next(body for method,path,_,body in self.requests if method=='PATCH' and path.endswith('/role-1'))
        self.assertEqual(body['permissions'],['historical:permission','system:notices:read','system:notices:write'])

    async def test_empty_sort_is_validated_before_a_create_request(self):
        await self.open('departments')
        await self.page.get_by_role('button',name='新建部门',exact=True).click()
        dialog=self.page.get_by_role('dialog')
        await dialog.get_by_label('部门名称',exact=True).fill('业务部')
        await dialog.get_by_label('排序',exact=True).fill('')
        await dialog.get_by_role('button',name=re.compile(r'^保\s*存$')).click()
        await expect(dialog.get_by_text('排序必须为 0 至 1000000 之间的整数',exact=True)).to_be_visible()
        self.assertFalse(any(method=='POST' for method,_,_,_ in self.requests))

    async def test_notice_unknown_save_is_confirmed_by_reading_the_current_value(self):
        attempts=[]
        async def lost(route):
            attempts.append(route.request.post_data_json)
            self.configs[0].update(value='false',version=7)
            await route.fulfill(status=503,json={'error':{'code':'unavailable','message':'保存响应丢失'}})
        await self.page.route('**/api/admin/configs/config-1',lost)
        await self.open('notices')
        switch=self.page.get_by_role('switch',name='在平台展示公告')
        await expect(switch).to_be_enabled()
        await switch.click()
        await expect(self.page.get_by_text(re.compile('保存结果待确认'))).to_be_visible()
        await expect(switch).to_be_disabled()
        await self.page.get_by_role('button',name='刷新状态',exact=True).click()
        await expect(switch).to_be_enabled()
        await expect(switch).not_to_be_checked()
        self.assertEqual(len(attempts),1)

    async def test_notice_toggle_preserves_existing_setting_name_and_remark(self):
        self.configs[0].update(name='企业公告展示',remark='保留原有业务说明')
        await self.open('notices')
        switch=self.page.get_by_role('switch',name='在平台展示公告')
        await expect(switch).to_be_enabled()
        await switch.click()
        await expect(switch).not_to_be_checked()
        body=next(body for method,path,_,body in self.requests if method=='PATCH' and path.endswith('/config-1'))
        self.assertEqual((body['name'],body['remark']),('企业公告展示','保留原有业务说明'))

    async def test_navigation_does_not_depend_on_retired_menu_service(self):
        await self.page.route('**/api/admin/menus**', lambda route:route.abort())
        await self.open('roles')
        await expect(self.page.get_by_text('运营员',exact=True)).to_be_visible()
        await expect(self.page.locator('.admin-navigation-error')).to_have_count(0)
        self.assertFalse(any(path=='/api/admin/menus' for _,path,_,_ in self.requests))

    async def test_dictionary_permissions_alone_do_not_show_or_grant_console_access(self):
        for permission in ['system:dictionaries:read','system:dictionaries:write']:
            with self.subTest(permission=permission):
                self.user={'id':'self','username':'former-dictionary-admin','is_admin':False,'permissions':[permission]}
                await self.page.goto(self.origin+'/admin/dictionaries')
                await expect(self.page).to_have_url(self.origin+'/chat')
                await self.page.get_by_role('button',name='用户菜单',exact=True).click()
                await expect(self.page.get_by_role('menuitem').filter(has_text='后台管理')).to_have_count(0)
        self.assertFalse(any(path.startswith('/api/admin/dictionaries') for _,path,_,_ in self.requests))

    async def test_dictionary_legacy_link_respects_the_remaining_role_permissions(self):
        self.user={'id':'self','username':'role-reader','is_admin':False,'permissions':['system:dictionaries:read','system:roles:read']}
        await self.page.goto(self.origin+'/admin/dictionaries')
        await expect(self.page).to_have_url(self.origin+'/admin/roles')
        await expect(self.page.get_by_text('运营员',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button',name='新建角色',exact=True)).to_have_count(0)
        self.assertFalse(any(path.startswith('/api/admin/dictionaries') or path=='/api/admin/users' for _,path,_,_ in self.requests))

    async def test_retired_permission_choices_are_hidden_and_old_codes_are_not_resubmitted(self):
        self.roles[0]['permissions']=['system:dictionaries:read','system:menus:write','system:posts:read','system:notices:read']
        items=[{'code':code,'label':label,'group':group} for code,label,group in [
            ('system:dictionaries:read','查看字典','字典管理'),('system:dictionaries:write','维护字典','字典管理'),
            ('system:menus:read','查看导航','导航配置'),('system:menus:write','维护导航','导航配置'),
            ('system:posts:read','查看岗位','岗位管理'),('system:posts:write','维护岗位','岗位管理'),
            ('system:notices:read','查看公告','通知公告'),('system:notices:write','管理公告','通知公告')]]
        await self.page.route('**/api/admin/permissions',lambda route:route.fulfill(json={'items':items}))
        await self.open('roles')
        await self.page.get_by_role('button',name='编辑',exact=True).click()
        dialog=self.page.get_by_role('dialog')
        for label in ['查看字典','维护字典','查看导航','维护导航','查看岗位','维护岗位']:
            await expect(dialog.get_by_label(label,exact=True)).to_have_count(0)
        await dialog.get_by_label('管理公告',exact=True).check()
        await dialog.get_by_role('button',name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).to_be_hidden()
        body=next(body for method,path,_,body in self.requests if method=='PATCH' and path.endswith('/role-1'))
        self.assertEqual(body['permissions'],['system:notices:read','system:notices:write'])

    async def test_post_legacy_link_respects_active_permissions_and_never_reads_posts(self):
        self.user={'id':'self','username':'department-reader','is_admin':False,'permissions':['system:posts:read','system:departments:read']}
        await self.page.goto(self.origin+'/admin/posts')
        await expect(self.page).to_have_url(self.origin+'/admin/departments')
        await expect(self.page.get_by_role('heading',name='部门管理',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button',name='新建部门',exact=True)).to_have_count(0)
        self.assertFalse(any(path=='/api/admin/posts' or path=='/api/admin/users' for _,path,_,_ in self.requests))

    async def test_post_permissions_alone_do_not_grant_a_console_entry(self):
        for permission in ['system:posts:read','system:posts:write']:
            with self.subTest(permission=permission):
                self.user={'id':'self','username':'former-post-admin','is_admin':False,'permissions':[permission]}
                await self.page.goto(self.origin+'/admin/posts')
                await expect(self.page).to_have_url(self.origin+'/chat')
                await self.page.get_by_role('button',name='用户菜单',exact=True).click()
                await expect(self.page.get_by_role('menuitem').filter(has_text='后台管理')).to_have_count(0)
        self.assertFalse(any(path=='/api/admin/posts' for _,path,_,_ in self.requests))

    async def test_editing_user_omits_post_field_and_preserves_historical_memberships(self):
        self.users[1]['post_ids']=['historical-post']
        await self.open('users')
        await self.page.get_by_role('row').filter(has_text='小艾').get_by_role('button',name='编辑',exact=True).click()
        dialog=self.page.get_by_role('dialog')
        await expect(dialog.get_by_label('岗位',exact=True)).to_have_count(0)
        await expect(dialog.get_by_label('角色',exact=True)).to_be_visible()
        await dialog.get_by_label('显示名称',exact=True).fill('更新成员')
        await dialog.get_by_role('button',name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).to_be_hidden()
        body=next(body for method,path,_,body in self.requests if method=='PATCH' and path.endswith('/user-2'))
        self.assertNotIn('post_ids',body)
        self.assertEqual(body['expected_version'],7)
        self.assertEqual(self.users[1]['post_ids'],['historical-post'])

if __name__=='__main__':unittest.main(verbosity=2)
