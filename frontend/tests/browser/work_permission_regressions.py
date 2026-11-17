"""An owner may safely stop an existing Work run after losing execution permission."""
from urllib.parse import urlparse
import unittest

from playwright.async_api import expect

from permission_fixture import granted_permissions
from work_regressions import WorkUIRegressions


class WorkPermissionRegressions(WorkUIRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.cancellations = []
        self.task['current_run_id'] = 'work-run'
        self.run.update(status='running', revision=2, projection_seq=2)

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/auth/me':
            return await route.fulfill(json={'id': 'user-1', 'username': 'work-owner', 'is_admin': False,
                'permissions': granted_permissions('platform:work:read')})
        if path == '/api/admin/notices/published':
            return await route.fulfill(json={'items': [], 'total': 0, 'page': 1, 'page_size': 20})
        if path == '/api/work/tasks/task-1/runs/work-run/cancel':
            self.cancellations.append(path)
        return await super().respond(route)

    async def test_read_only_work_owner_can_stop_existing_run_without_admitting_another(self):
        await self.open_task()
        stop = self.page.get_by_role('button', name='停止', exact=True)
        await expect(stop).to_be_enabled()
        await expect(self.page.get_by_role('textbox', name='工作消息', exact=True)).to_be_disabled()
        await stop.click()
        await expect(stop).to_have_count(0)
        self.assertEqual(self.cancellations, ['/api/work/tasks/task-1/runs/work-run/cancel'])
        self.assertEqual(self.runs, [])
        self.assertEqual(self.posts, [])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([WorkPermissionRegressions('test_read_only_work_owner_can_stop_existing_run_without_admitting_another')])
