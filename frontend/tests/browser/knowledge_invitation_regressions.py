"""Invitation UI uses explicit login/consent and never reads a global account directory."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import unittest
from urllib.parse import urlparse
from playwright.async_api import expect
from knowledge_v2_regressions import KnowledgeV2Regressions

TOKEN='A'*43

class KnowledgeInvitationRegressions(KnowledgeV2Regressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.actor={'id':'user-1','username':'owner','is_admin':False,'permissions':[]}
        self.signed_in=True
        self.accepted=False
        self.raw_requests=[]
        self.create_bodies=[]
        self.drop_create=False
        self.invalid=False
        self.accept_calls=0
        self.cleanup_bodies=[]
        self.hold_accept=False
        self.accept_started=asyncio.Event()
        self.release_accept=asyncio.Event()
        self.members=[]
        self.invitation={'id':'invite-1','status':'active','expires_at':'2026-11-10T03:00:00Z','created_at':'2026-11-09T03:00:00Z','accepted_at':None}
        self.page.on('request',lambda request:self.raw_requests.append(request.url))

    async def asyncTearDown(self):
        self.release_accept.set()
        path=Path(os.environ.get('KNOWLEDGE_INVITATION_EVIDENCE','/tmp/knowledge-invitations'))
        path.mkdir(parents=True,exist_ok=True)
        (path/(self._testMethodName+'-trace.json')).write_text(json.dumps({'urls':self.raw_requests,'create_bodies':self.create_bodies,'accept_calls':self.accept_calls,'errors':self.errors},ensure_ascii=False,indent=2))
        await super().asyncTearDown()

    async def respond(self,route):
        path=urlparse(route.request.url).path;method=route.request.method
        if path=='/api/auth/me':
            return await route.fulfill(status=200 if self.signed_in else 401,json=self.actor if self.signed_in else {'error':{'code':'unauthenticated','message':'请先登录'}})
        if path=='/api/auth/login':
            self.signed_in=True
            return await route.fulfill(json={'user':self.actor})
        if path=='/api/knowledge/invitations/preview':
            self.assertEqual(route.request.post_data_json,{'token':TOKEN})
            if self.invalid:return await route.fulfill(status=404,json={'error':{'code':'invitation_unavailable','message':'邀请无效、已过期、已使用或已撤销'}})
            return await route.fulfill(json={'base_id':'base-1','base_name':'业务知识','owner_name':'创建者',
                'expires_at':self.invitation['expires_at'],'already_member':self.accepted or self.actor['id']=='user-1'})
        if path=='/api/knowledge/invitations/accept':
            self.accept_calls+=1
            self.accept_started.set()
            if self.hold_accept:await self.release_accept.wait()
            self.accepted=True
            self.base['can_manage']=False
            return await route.fulfill(json={'base_id':'base-1','already_member':False})
        if path=='/api/knowledge/bases/base-1/sharing/cleanup':
            self.cleanup_bodies.append(route.request.post_data_json)
            self.acl_entries=[entry for entry in self.acl_entries if entry['effect']=='allow' and not entry.get('expires_at')]
            self.base['acl_version']+=1
            return await route.fulfill(json={'base':self.base,'entries':self.acl_entries,'builtin_permissions':[]})
        if path=='/api/knowledge/bases/base-1/invitations':
            if method=='POST':
                self.create_bodies.append(route.request.post_data_json)
                if self.drop_create:self.drop_create=False;return await route.abort('failed')
                self.invitations=[self.invitation]
                return await route.fulfill(status=201,json={'invitation':self.invitation,'token':TOKEN})
            return await route.fulfill(json=self.invitations)
        if path=='/api/knowledge/bases/base-1/invitations/invite-1/link':
            return await route.fulfill(json={'invitation':self.invitation,'token':TOKEN})
        if path=='/api/knowledge/bases/base-1/invitations/invite-1/revoke':
            self.invitation['status']='revoked'
            return await route.fulfill(json=self.invitation)
        if path=='/api/knowledge/bases/base-1/members/user-2' and method=='DELETE':
            self.members=[];self.base['acl_version']+=1
            return await route.fulfill(status=204)
        return await super().respond(route)

    async def owner_modal(self):
        await self.page.goto(self.origin+'/knowledge/base-1/documents')
        await self.page.get_by_role('button',name='共享',exact=True).click()
        modal=self.page.get_by_role('dialog',name='共享 · 业务知识',exact=True)
        await expect(modal.get_by_role('button',name='生成一次性邀请链接',exact=True)).to_be_enabled()
        return modal

    async def test_owner_invites_without_loading_or_searching_accounts(self):
        modal=await self.owner_modal()
        await expect(modal.get_by_role('combobox')).to_have_count(0)
        await expect(modal.get_by_role('radio')).to_have_count(0)
        await expect(modal).not_to_contain_text('所有已登录用户')
        await expect(modal).to_contain_text('暂无成员')
        await modal.get_by_role('button',name='生成一次性邀请链接',exact=True).click()
        link=modal.get_by_role('textbox',name='邀请链接',exact=True)
        await expect(link).to_have_value(self.origin+'/knowledge/join#'+TOKEN)
        self.assertFalse(any('/users' in url for url in self.raw_requests))
        self.assertFalse(any(TOKEN in url for url in self.raw_requests))
        destination=Path(os.environ.get('KNOWLEDGE_INVITATION_EVIDENCE','/tmp/knowledge-invitations'))
        destination.mkdir(parents=True,exist_ok=True)
        await self.page.screenshot(path=str(destination/'invitation-owner.png'),full_page=True,animations='disabled')

    async def test_pasted_link_opens_preview_and_still_requires_acceptance(self):
        self.actor={'id':'user-2','username':'recipient','is_admin':False,'permissions':[]}
        await self.page.goto(self.origin+'/knowledge')
        await self.page.get_by_role('button',name='加入知识库',exact=True).click()
        modal=self.page.get_by_role('dialog',name='加入知识库',exact=True)
        field=modal.get_by_role('textbox',name='知识库邀请链接',exact=True)
        await field.fill('https://outside.invalid/knowledge/join#'+TOKEN)
        await modal.get_by_role('button',name='查看邀请',exact=True).click()
        await expect(modal).to_contain_text('请输入本站有效的知识库邀请链接')
        self.assertEqual(self.accept_calls,0)
        await field.fill(self.origin+'/knowledge/join#'+TOKEN)
        destination=Path(os.environ.get('KNOWLEDGE_INVITATION_EVIDENCE','/tmp/knowledge-invitations'))
        destination.mkdir(parents=True,exist_ok=True)
        await self.page.screenshot(path=str(destination/'join-by-link.png'),full_page=True,animations='disabled')
        await self.page.set_viewport_size({'width':390,'height':844})
        await expect(modal).to_be_visible()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        await self.page.screenshot(path=str(destination/'join-by-link-mobile.png'),full_page=True,animations='disabled')
        await modal.get_by_role('button',name='查看邀请',exact=True).click()
        await expect(self.page.get_by_role('button',name='接受邀请并进入',exact=True)).to_be_visible()
        self.assertEqual(self.accept_calls,0)
        self.assertFalse(any(TOKEN in url for url in self.raw_requests))
        await self.page.get_by_role('button',name='接受邀请并进入',exact=True).click()
        await expect(self.page).to_have_url(re.compile('/knowledge/base-1/documents$'))
        self.assertEqual(self.accept_calls,1)

    async def test_legacy_cleanup_sends_no_user_selection_or_scope(self):
        self.base['document_policy']='legacy'
        self.acl_entries=[{'user_id':'user-2','effect':'deny','expires_at':None}]
        await self.page.goto(self.origin+'/knowledge')
        await self.page.get_by_role('button',name='更多操作：业务知识',exact=True).click()
        await self.page.get_by_role('menuitem',name='共享',exact=True).click()
        modal=self.page.get_by_role('dialog',name='共享 · 业务知识',exact=True)
        await expect(modal.get_by_role('button',name='生成一次性邀请链接',exact=True)).to_be_disabled()
        await expect(modal.get_by_role('radio')).to_have_count(0)
        await modal.get_by_role('checkbox').check()
        await modal.get_by_role('button',name='整理旧规则',exact=True).click()
        await expect(modal).not_to_be_visible()
        self.assertEqual(self.cleanup_bodies,[{'expected_acl_version':1}])
        self.assertFalse(any('/users' in url for url in self.raw_requests))

    async def test_uncertain_generation_reuses_its_request(self):
        self.drop_create=True
        modal=await self.owner_modal()
        await modal.get_by_role('button',name='生成一次性邀请链接',exact=True).click()
        await expect(modal.get_by_role('button',name='确认原生成请求',exact=True)).to_be_visible()
        await modal.get_by_role('button',name='确认原生成请求',exact=True).click()
        await expect(modal.get_by_role('textbox',name='邀请链接',exact=True)).to_be_visible()
        self.assertEqual(len(self.create_bodies),2)
        self.assertEqual(self.create_bodies[0],self.create_bodies[1])

    async def test_logged_out_invitation_returns_after_login_and_requires_acceptance(self):
        self.actor={'id':'user-2','username':'recipient','is_admin':False,'permissions':[]};self.signed_in=False
        await self.page.goto(self.origin+'/knowledge/join#'+TOKEN)
        await expect(self.page.get_by_role('button',name='登录后继续',exact=True)).to_be_visible()
        await expect(self.page.get_by_text('业务知识',exact=True)).to_have_count(0)
        self.assertEqual(self.accept_calls,0)
        await self.page.get_by_role('button',name='登录后继续',exact=True).click()
        await expect(self.page).to_have_url(re.compile(r'/login\?'))
        self.assertNotIn(TOKEN,self.page.url)
        await self.page.get_by_role('textbox',name='账号',exact=True).fill('recipient')
        await self.page.get_by_role('textbox',name='密码',exact=True).fill('fixture-password')
        await self.page.get_by_role('button',name=re.compile(r'^登\s*录$')).click()
        await expect(self.page.get_by_role('button',name='接受邀请并进入',exact=True)).to_be_visible()
        self.assertEqual(self.accept_calls,0)
        self.assertFalse(any(TOKEN in url for url in self.raw_requests))
        await self.page.get_by_role('button',name='接受邀请并进入',exact=True).click()
        await expect(self.page).to_have_url(re.compile('/knowledge/base-1/documents$'))
        await expect(self.page.get_by_role('button',name='共享',exact=True)).to_have_count(0)
        self.assertEqual(await self.page.evaluate('sessionStorage.getItem("zhiheng:pending-knowledge-invitation")'),None)

    async def test_recipient_accepts_once_even_with_a_slow_response(self):
        self.actor={'id':'user-2','username':'recipient','is_admin':False,'permissions':[]};self.hold_accept=True
        await self.page.goto(self.origin+'/knowledge/join#'+TOKEN)
        button=self.page.get_by_role('button',name='接受邀请并进入',exact=True)
        await expect(button).to_be_visible()
        await button.click()
        await asyncio.wait_for(self.accept_started.wait(),timeout=5)
        await self.page.locator('.knowledge-invitation-card .ant-btn-primary').dispatch_event('click')
        self.assertEqual(self.accept_calls,1)
        self.release_accept.set()
        await expect(self.page).to_have_url(re.compile('/knowledge/base-1/documents$'))

    async def test_late_acceptance_cannot_redirect_a_different_invitation(self):
        self.actor={'id':'user-2','username':'recipient','is_admin':False,'permissions':[]};self.hold_accept=True
        await self.page.goto(self.origin+'/knowledge/join#'+TOKEN)
        await self.page.get_by_role('button',name='接受邀请并进入',exact=True).click()
        await asyncio.wait_for(self.accept_started.wait(),timeout=5)
        await self.page.evaluate("location.hash = 'bad'")
        await expect(self.page.get_by_text('邀请链接无效，请向所有者获取新的邀请',exact=True)).to_be_visible()
        self.release_accept.set()
        await expect(self.page).to_have_url(re.compile('/knowledge/join#bad$'))
        await expect(self.page.get_by_role('button',name='接受邀请并进入',exact=True)).to_have_count(0)
        await self.page.wait_for_function("document.querySelector('.ant-spin') === null")
        self.assertTrue(self.page.url.endswith('/knowledge/join#bad'))

    async def test_mobile_recipient_page_has_no_horizontal_overflow(self):
        self.actor={'id':'user-2','username':'recipient','is_admin':False,'permissions':[]}
        await self.page.set_viewport_size({'width':390,'height':844})
        await self.page.goto(self.origin+'/knowledge/join#'+TOKEN)
        await expect(self.page.get_by_role('button',name='接受邀请并进入',exact=True)).to_be_visible()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        destination=Path(os.environ.get('KNOWLEDGE_INVITATION_EVIDENCE','/tmp/knowledge-invitations'))
        destination.mkdir(parents=True,exist_ok=True)
        await self.page.screenshot(path=str(destination/'invitation-recipient-mobile.png'),full_page=True,animations='disabled')

    async def test_expired_or_revoked_invitation_does_not_show_private_metadata(self):
        self.actor={'id':'user-2','username':'recipient','is_admin':False,'permissions':[]};self.invalid=True
        await self.page.goto(self.origin+'/knowledge/join#'+TOKEN)
        await expect(self.page.get_by_text('邀请无效、已过期、已使用或已撤销',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button',name='接受邀请并进入',exact=True)).to_have_count(0)
        await expect(self.page.get_by_text('业务知识',exact=True)).to_have_count(0)
        self.assertEqual(self.accept_calls,0)

    async def test_owner_revoke_and_member_removal_show_only_related_people(self):
        self.invitations=[self.invitation];self.members=[{'id':'user-2','name':'已加入成员','active':True}]
        modal=await self.owner_modal()
        await expect(modal).to_contain_text('已加入成员')
        await modal.get_by_role('button',name='撤销邀请',exact=True).click()
        await self.page.get_by_role('button',name=re.compile(r'^确\s*定$')).last.click()
        await expect(modal).to_contain_text('已失效')
        await modal.get_by_role('button',name='移除成员',exact=True).click()
        await self.page.get_by_role('button',name=re.compile(r'^确\s*定$')).last.click()
        await expect(modal).not_to_contain_text('已加入成员')
        self.assertFalse(any('/users' in url for url in self.raw_requests))

if __name__=='__main__':unittest.main()
