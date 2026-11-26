"""The MCP resource menu is independent of workspace turns."""

import re
from urllib.parse import urlsplit

from playwright.async_api import expect

from developer_regressions import DeveloperBoundary
from permission_fixture import granted_permissions
from work_regressions import WorkUIRegressions

TOOL = {
    'name': 'policy_lookup',
    'description': '查询公开制度',
    'input_schema': {'type': 'object', 'properties': {'query': {'type': 'string'}}},
    'read_only': True,
}
CATALOG_TOOL = {**TOOL, 'server_id': 'server-1', 'server_name': '制度服务', 'version': 4}
SERVER = {
    'id': 'server-1', 'name': '制度服务', 'endpoint': 'https://tools.example.com/mcp',
    'enabled': True, 'has_token': True, 'version': 4, 'synced_at': '2026-11-25T01:00:00Z',
    'tools': [{**TOOL, 'enabled': True, 'role_ids': ['role-1']}],
}


class McpUIRegressions(WorkUIRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.mcp_operations = []
        self.mcp_management_requests = []
        self.mcp_server = SERVER
        self.mcp_available_tools = [CATALOG_TOOL]

    async def respond(self, route):
        path = urlsplit(route.request.url).path
        if path == '/api/auth/me':
            permissions = ['platform:work:create', 'platform:work:use', 'platform:mcp:use']
            permissions.extend('platform:mcp:' + operation for operation in self.mcp_operations)
            return await route.fulfill(json={
                'id': 'user-1', 'username': 'member', 'is_admin': False,
                'permissions': granted_permissions(*permissions),
            })
        if path == '/api/mcp/tools':
            return await route.fulfill(json=self.mcp_available_tools)
        if path in {'/api/mcp/servers', '/api/mcp/roles', '/api/mcp/servers/server-1/calls'}:
            self.mcp_management_requests.append(path)
            server = dict(self.mcp_server)
            if 'configure' not in self.mcp_operations:
                server.update(endpoint=None, has_token=None)
            if 'grant' not in self.mcp_operations:
                server['tools'] = ([{**tool, 'role_ids': []} for tool in server['tools']]
                                   if 'configure' in self.mcp_operations else [])
            payloads = {
                '/api/mcp/servers': [server],
                '/api/mcp/roles': [{'id': 'role-1', 'name': '办公室'}],
                '/api/mcp/servers/server-1/calls': [],
            }
            return await route.fulfill(json=payloads[path])
        return await super().respond(route)

    async def test_mcp_catalog_is_available_without_workspace_tool_selection(self):
        await self.page.goto(self.origin + '/work')
        await expect(self.page.get_by_role('button', name='选择 MCP 工具')).to_have_count(0)
        resources = self.page.get_by_role('group', name='资源', exact=True)
        await resources.get_by_role('link', name='MCP', exact=True).click()
        await expect(self.page.get_by_text('policy_lookup', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('tab', name='服务管理', exact=True)).to_have_count(0)
        await self.open_task()
        await expect(self.page.get_by_role('button', name='选择 MCP 工具')).to_have_count(0)
        await self.page.get_by_role('textbox', name='工作消息', exact=True).fill('核对费用说明')
        await self.page.get_by_role('button', name='发送', exact=True).click()
        await expect(self.page.get_by_text('核实后的结论', exact=True)).to_be_visible()
        self.assertNotIn('mcp_tool', self.runs[0])
        self.assertEqual(self.mcp_management_requests, [])

    async def test_mcp_manage_shows_service_tools_and_recent_calls(self):
        self.mcp_operations = ['configure', 'grant', 'audit']
        await self.page.goto(self.origin + '/mcp')
        catalog_search = self.page.get_by_role('textbox', name='搜索 MCP 工具')
        await expect(catalog_search).to_be_visible()
        page = self.page.locator('.mcp-page')
        catalog_layout = await page.bounding_box()
        await self.page.get_by_role('tab', name='服务管理', exact=True).click()
        await expect(self.page.get_by_text('制度服务', exact=True)).to_be_visible()
        servers_layout = await page.bounding_box()
        self.assertAlmostEqual(catalog_layout['x'], servers_layout['x'], delta=1)
        self.assertAlmostEqual(catalog_layout['width'], servers_layout['width'], delta=1)
        await self.page.get_by_role('button', name='详情', exact=True).click()
        await expect(self.page.get_by_text('工具授权', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('暂无调用记录', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='编辑授权', exact=True).click()
        await expect(self.page.get_by_text('办公室', exact=True)).to_be_visible()

    async def test_mcp_catalog_shows_synced_tool_definitions_to_configurers(self):
        self.mcp_operations = ['configure']
        self.mcp_available_tools = []
        self.mcp_server = {
            **SERVER,
            'enabled': False,
            'tools': [{**TOOL, 'read_only': False, 'enabled': False, 'role_ids': []}],
        }
        await self.page.goto(self.origin + '/mcp')
        row = self.page.get_by_role('row').filter(has_text='policy_lookup')
        await expect(self.page.get_by_role('columnheader', name='状态', exact=True)).to_have_count(0)
        await expect(row.get_by_text('制度服务', exact=True)).to_be_visible()
        await row.get_by_role('button', name='查看', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='policy_lookup')
        await expect(dialog).to_contain_text('"query"')
        self.assertEqual(self.mcp_management_requests, ['/api/mcp/servers'])

        self.mcp_operations = []
        await self.page.reload()
        await expect(self.page.get_by_text('暂无可用工具', exact=True)).to_be_visible()
        await expect(row).to_have_count(0)
        self.assertEqual(self.mcp_management_requests, ['/api/mcp/servers'])

    async def test_mcp_sync_results_float_above_the_table(self):
        self.mcp_operations = ['configure']
        fail = False

        async def sync(route):
            if fail:
                await route.fulfill(status=400, json={'error': {
                    'code': 'mcp_connection_failed', 'message': '无法连接 MCP 服务',
                }})
            else:
                await route.fulfill(json=SERVER)

        await self.page.route('**/api/mcp/servers/server-1/sync', sync)
        await self.page.goto(self.origin + '/mcp/servers')
        sync_button = self.page.get_by_role('button', name='测试并同步', exact=True)
        await expect(sync_button).to_be_visible()
        table = self.page.get_by_role('table')
        before = await table.bounding_box()
        await sync_button.click()
        toast = self.page.locator('.platform-feedback')
        await expect(toast.get_by_role('status')).to_have_text('连接成功，工具清单已更新')
        self.assertAlmostEqual((await table.bounding_box())['y'], before['y'], delta=1)
        await toast.get_by_role('button', name='关闭提示').click()
        await expect(toast).to_have_count(0)

        await self.page.set_viewport_size({'width': 390, 'height': 760})
        fail = True
        await sync_button.click()
        await expect(toast.get_by_role('alert')).to_have_text('无法连接 MCP 服务')
        content = toast.locator('.ant-message-notice-content')
        box = await content.bounding_box()
        self.assertGreaterEqual(box['x'], 0)
        self.assertLessEqual(box['x'] + box['width'], 390)
        self.assertAlmostEqual(box['x'] + box['width'] / 2, 195, delta=2)

    async def test_mcp_audit_role_loads_only_call_records(self):
        self.mcp_operations = ['audit']
        await self.page.goto(self.origin + '/mcp/servers')
        await expect(self.page.get_by_text('制度服务', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('服务地址', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='添加服务', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='详情', exact=True).click()
        await expect(self.page.get_by_text('暂无调用记录', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('工具授权', exact=True)).to_have_count(0)
        self.assertEqual(self.mcp_management_requests, ['/api/mcp/servers', '/api/mcp/servers/server-1/calls'])

    async def test_mcp_grant_role_loads_tools_and_roles_without_call_records(self):
        self.mcp_operations = ['grant']
        await self.page.goto(self.origin + '/mcp/servers')
        await expect(self.page.get_by_text('制度服务', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='测试并同步', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='详情', exact=True).click()
        await expect(self.page.get_by_text('最近调用', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='编辑授权', exact=True).click()
        await expect(self.page.get_by_text('办公室', exact=True)).to_be_visible()
        self.assertCountEqual(self.mcp_management_requests, ['/api/mcp/servers', '/api/mcp/roles'])


class McpPublicationRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        self.publications = []
        self.published = []
        self.agent_candidate_requests = 0
        await super().asyncSetUp()

    async def respond(self, route):
        path = urlsplit(route.request.url).path
        if path == '/api/developer/applications/app-1/agent-candidates':
            self.agent_candidate_requests += 1
        if path == '/api/developer/applications/scope-catalog':
            return await route.fulfill(json={
                'scopes': ['mcp:invoke'],
                'definitions': [{
                    'scope': 'mcp:invoke', 'label': 'MCP 工具调用', 'description': '指定工具',
                    'requires_models': False, 'grantable': True, 'reason': None,
                }],
                'caps': {'resource_rpm_min': 1, 'resource_rpm_max': 120},
            })
        if path == '/api/mcp/publication-sources':
            return await route.fulfill(json=[{
                'server_id': 'server-1', 'server_name': '制度服务', 'version': 4,
                'name': 'policy_lookup', 'read_only': False,
            }])
        if path == '/api/developer/applications/app-1/mcp-publications':
            if route.request.method == 'POST':
                body = route.request.post_data_json
                self.published.append(body)
                self.publications = [{
                    'id': 'publication-1', 'name': body['name'], 'tool_name': body['tool_name'],
                    'server_name': '制度服务', 'available': True,
                }]
                return await route.fulfill(status=201, json=self.publications[0])
            return await route.fulfill(json=self.publications)
        return await super().respond(route)

    async def test_mcp_publish_then_select_tool_for_key(self):
        await self.page.goto(self.origin + '/developer?applicationId=app-1&tab=mcp')
        await expect(self.page.get_by_text(self.origin + '/v1/mcp', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='发布工具').click()
        dialog = self.page.get_by_role('dialog', name='发布 MCP 工具')
        await dialog.locator('.ant-select-selector').click()
        await self.page.get_by_text('制度服务 · policy_lookup', exact=True).click()
        await dialog.get_by_role('textbox', name='发布名称').fill('company_policy')
        publish = dialog.get_by_role('button', name=re.compile(r'^发\s*布$'))
        await expect(publish).to_be_disabled()
        await dialog.get_by_role('checkbox', name='我已确认此工具仅执行只读操作').check()
        await publish.click()
        await expect(self.page.get_by_text('company_policy', exact=True)).to_be_visible()
        assert self.published == [{
            'server_id': 'server-1', 'tool_name': 'policy_lookup', 'expected_version': 4,
            'name': 'company_policy', 'confirm_read_only': True,
        }]
        await self.page.get_by_role('tab', name='访问密钥', exact=True).click()
        await self.page.get_by_role('button', name='创建密钥', exact=True).click()
        await self.page.get_by_text('MCP 工具调用', exact=True).click()
        await self.page.get_by_role('dialog').locator('.ant-select-selector').click()
        await expect(self.page.get_by_text('company_policy · 制度服务', exact=True)).to_be_visible()
        self.assertEqual(self.agent_candidate_requests, 0)
