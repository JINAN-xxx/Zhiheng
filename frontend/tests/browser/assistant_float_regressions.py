"""Floating character navigation, pointer gestures and identity boundaries."""
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary
from permission_fixture import granted_permissions


class AssistantFloatRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.user = {'id': 'float-A', 'username': 'admin', 'is_admin': False,
                     'permissions': granted_permissions('platform:overview:read',
                         'platform:developer:read', 'platform:agents:read', 'platform:chat:use')}
        self.evidence = Path(os.environ.get('ASSISTANT_FLOAT_EVIDENCE', '/tmp/assistant-float-evidence'))
        self.evidence.mkdir(parents=True, exist_ok=True)

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/auth/me':
            return await route.fulfill(json=self.user)
        if path == '/api/overview':
            return await route.fulfill(json={'scope': 'mine', 'can_view_platform': False,
                'period_days': 7, 'timezone': 'Asia/Shanghai', 'observed_at': '2026-11-17T06:30:00Z',
                'metrics': {'today_calls': 128, 'yesterday_calls': 114, 'successful_calls': 1240,
                    'failed_calls': 10, 'cancelled_calls': 2, 'today_known_tokens': 86400,
                    'today_unconfirmed_calls': 0, 'trend': [
                        {'day': f'2026-11-{11+i}', 'calls': n}
                        for i,n in enumerate([33,56,48,98,74,112,128])]},
                'credits': {'enabled': True, 'monthly_points': '1280', 'unconfirmed_calls': 0},
                'recent': [], 'activities': [], 'alerts': []})
        if path in ['/api/agents', '/api/agents/available']:
            return await route.fulfill(json=[])
        if path == '/api/admin/notices/published':
            return await route.fulfill(json={'items': [], 'total': 0, 'page': 1, 'page_size': 20})
        return await super().respond(route)

    def launcher(self):
        return self.page.locator('.assistant-float__launcher')

    def panel(self):
        return self.page.locator('.assistant-float__panel')

    async def open_float(self, path='/overview'):
        await self.page.goto(self.origin + path)
        await expect(self.launcher()).to_be_visible()
        return self.launcher()

    async def assert_inside(self, locator):
        await self.page.wait_for_function("""element => {
            const box = element.getBoundingClientRect(), v = visualViewport;
            return box.x >= -1 && box.y >= -1 && box.right <= v.width+1 && box.bottom <= v.height+1;
        }""", arg=await locator.element_handle(), timeout=3000)
        box = await locator.bounding_box()
        width, height = await self.page.evaluate('[visualViewport.width, visualViewport.height]')
        self.assertGreaterEqual(box['x'], -1)
        self.assertGreaterEqual(box['y'], -1)
        self.assertLessEqual(box['x'] + box['width'], width + 1)
        self.assertLessEqual(box['y'] + box['height'], height + 1)
        return box

    async def test_keyboard_outside_close_and_existing_routes(self):
        launcher = await self.open_float()
        await expect(self.panel()).to_have_count(0)
        await launcher.focus()
        before = await launcher.bounding_box()
        await self.page.keyboard.press('ArrowUp')
        after = await launcher.bounding_box()
        self.assertAlmostEqual(before['y'] - after['y'], 16, delta=1)
        await self.page.keyboard.press('Enter')
        await expect(self.panel().get_by_role('heading', name='你好，我是小助手', exact=True)).to_be_visible()
        await expect(launcher.locator('.character-head')).to_have_css(
            'animation-name', re.compile(r'^assistant-nod')
        )
        await expect(self.panel().get_by_role('link', name='打开智能助手', exact=True)).to_be_focused()
        await self.page.screenshot(path=str(self.evidence/'desktop-expanded.png'))
        await self.page.keyboard.press('Escape')
        await expect(self.panel()).to_have_count(0)
        await expect(launcher).to_be_focused()
        await launcher.click()
        await self.page.locator('.workbench-hero h1').click()
        await expect(self.panel()).to_have_count(0)
        await launcher.click()
        await self.panel().get_by_role('link', name='打开对话体验', exact=True).click()
        await expect(self.page).to_have_url(self.origin+'/chat')
        await expect(self.panel()).to_have_count(0)
        await launcher.click()
        await self.panel().get_by_role('link', name='打开智能助手', exact=True).click()
        await expect(self.page).to_have_url(self.origin+'/agents')
        await expect(self.panel()).to_have_count(0)

    async def test_drag_is_not_click_and_position_survives_reload_and_resize(self):
        launcher = await self.open_float()
        box = await launcher.bounding_box()
        await self.page.mouse.move(box['x']+box['width']/2, box['y']+box['height']/2)
        await self.page.mouse.down()
        await self.page.mouse.move(470, 180, steps=12)
        await expect(launcher).to_have_css('--assistant-drag-lean', '-10deg')
        await launcher.evaluate("""element => element.addEventListener('pointerup', () => {
            const character = element.querySelector('.assistant-float__character');
            const bouncedInward = character.getAnimations().some(animation =>
                animation.effect.getKeyframes().some(frame => frame.translate === '4px')
            );
            element.dataset.bouncedInward = String(bouncedInward);
        }, { once: true })""")
        await self.page.mouse.up()
        await expect(launcher).to_have_attribute('data-bounced-inward', 'true')
        await expect(self.panel()).to_have_count(0)
        await self.page.wait_for_function("document.querySelector('.assistant-float__launcher').getBoundingClientRect().x < 1")
        box = await self.assert_inside(launcher)
        self.assertLessEqual(box['x'], 1)
        self.assertGreater(box['y'], 12)
        await launcher.hover()
        await expect(launcher.locator('.assistant-float__character')).to_have_css('transform', 'none')
        await self.page.reload()
        await expect(launcher).to_be_visible()
        restored = await launcher.bounding_box()
        self.assertAlmostEqual(restored['x'], box['x'], delta=1)
        self.assertAlmostEqual(restored['y'], box['y'], delta=1)
        await self.page.set_viewport_size({'width': 390, 'height': 600})
        await self.assert_inside(launcher)
        await launcher.click()
        await self.assert_inside(self.panel())
        await self.panel().get_by_role('button', name='关闭小助手浮窗', exact=True).click()
        box = await launcher.bounding_box()
        await self.page.mouse.move(box['x']+box['width']/2, box['y']+box['height']/2)
        await self.page.mouse.down()
        await self.page.mouse.move(280, 220, steps=8)
        await self.page.mouse.up()
        await self.page.wait_for_function("Math.abs(document.querySelector('.assistant-float__launcher').getBoundingClientRect().right - innerWidth) < 1")
        await expect(self.panel()).to_have_count(0)
        await self.assert_inside(launcher)

    async def test_dock_preferences_are_per_account_and_live_permission_changes_are_applied(self):
        launcher = await self.open_float('/developer')
        await launcher.click()
        await launcher.evaluate("""element => element.addEventListener('transitionrun', event => {
            if (event.propertyName === 'opacity' && event.target.classList.contains('assistant-float__character'))
                element.dataset.dockTransition = event.propertyName;
        })""")
        await self.panel().get_by_role('button', name='贴边收起小助手', exact=True).click()
        await expect(launcher).to_have_class('assistant-float__launcher is-docked')
        await expect(launcher).to_have_attribute('data-dock-transition', 'opacity')
        await expect(launcher.locator('.assistant-float__character')).to_have_count(0)
        await self.page.reload()
        await expect(launcher).to_have_attribute('aria-label', '展开小助手')
        await self.page.screenshot(path=str(self.evidence/'desktop-docked.png'))
        self.user = {**self.user, 'id': 'float-B', 'username': 'B'}
        await self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        await expect(launcher).to_have_attribute('aria-label', '小助手')
        self.user = {**self.user, 'id': 'float-A', 'username': 'admin'}
        await self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        await expect(launcher).to_have_attribute('aria-label', '展开小助手')
        await launcher.click()
        self.user['permissions'] = granted_permissions('platform:developer:read', 'platform:agents:read')
        await self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        await expect(self.panel().get_by_role('link')).to_have_count(1)
        await expect(self.panel().get_by_role('link', name='打开智能助手', exact=True)).to_be_visible()
        self.user['permissions'] = granted_permissions('platform:developer:read')
        await self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        await expect(launcher).to_have_count(0)
        await expect(self.panel()).to_have_count(0)
        await self.page.goto(self.origin+'/login')
        await expect(self.page.get_by_role('button', name='登录', exact=True)).to_be_visible()
        await expect(launcher).to_have_count(0)

    async def test_mobile_touch_composer_small_viewport_and_navigation_drawer(self):
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        launcher = await self.open_float('/chat')
        await expect(self.page.get_by_role('textbox', name='消息内容', exact=True)).to_be_visible()
        box = await self.assert_inside(launcher)
        composer = await self.page.get_by_role('textbox', name='消息内容', exact=True).bounding_box()
        self.assertLessEqual(box['y']+box['height'], composer['y'])
        cdp = await self.context.new_cdp_session(self.page)
        await cdp.send('Input.dispatchTouchEvent', {'type':'touchStart', 'touchPoints':[{'x':box['x']+30,'y':box['y']+34}]})
        await cdp.send('Input.dispatchTouchEvent', {'type':'touchMove', 'touchPoints':[{'x':35,'y':100}]})
        await cdp.send('Input.dispatchTouchEvent', {'type':'touchEnd', 'touchPoints':[]})
        await expect(self.panel()).to_have_count(0)
        box = await self.assert_inside(launcher)
        self.assertLess(box['x'], 50)
        await launcher.click()
        await self.assert_inside(self.panel())
        await self.page.screenshot(path=str(self.evidence/'mobile-expanded.png'))
        await self.page.set_viewport_size({'width':390,'height':360})
        await self.assert_inside(launcher)
        await self.assert_inside(self.panel())
        await self.panel().get_by_role('button', name='关闭小助手浮窗', exact=True).click()
        await self.page.set_viewport_size({'width':390,'height':844})
        await self.page.get_by_role('button', name='打开主菜单', exact=True).click()
        await expect(launcher).to_be_hidden()
        await self.page.locator('.platform-mobile-drawer').get_by_role('link', name='对话体验', exact=True).click()
        await expect(launcher).to_be_visible()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))

    async def test_blocked_storage_and_reduced_motion_preserve_basic_use(self):
        await self.page.add_init_script("""for (const method of ['getItem','setItem']) {
            const original = Storage.prototype[method];
            Storage.prototype[method] = function(key, ...args) {
                if(key.startsWith('zhiheng:assistant-float:')) throw new DOMException('Unavailable','SecurityError');
                return original.call(this,key,...args);
            };
        }""")
        await self.page.emulate_media(reduced_motion='reduce')
        launcher = await self.open_float()
        self.assertEqual(await launcher.locator('.mascot-body').evaluate("e=>getComputedStyle(e).animationName"), 'none')
        await launcher.click()
        await expect(self.panel()).to_be_visible()
        await expect(launcher.locator('.character-head')).to_have_css('animation-name', 'none')
        await self.panel().get_by_role('button', name='贴边收起小助手', exact=True).click()
        await expect(launcher.locator('.assistant-float__dock-icon')).to_have_css('animation-name', 'none')
        await launcher.click()
        await expect(self.panel()).to_be_visible()
