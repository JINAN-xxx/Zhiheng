"""MCP is a resource menu and selected tools travel with a work turn."""

from urllib.parse import urlsplit

from playwright.async_api import expect

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

    async def test_mcp_catalog_and_explicit_arguments_in_workspace(self):
        await self.page.goto(self.origin + '/work')
        resources = self.page.get_by_role('group', name='资源', exact=True)
        await resources.get_by_role('link', name='MCP', exact=True).click()
        await expect(self.page.get_by_text('policy_lookup', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('tab', name='服务管理', exact=True)).to_have_count(0)
        await self.open_task()
        await self.page.get_by_role('button', name='选择 MCP 工具').click()
        dialog = self.page.get_by_role('dialog', name='选择 MCP 工具')
        await dialog.locator('.ant-select-selector').click()
        await self.page.get_by_text('制度服务 · policy_lookup', exact=True).click()
        await self.page.get_by_role('textbox', name='MCP 工具参数').fill('{"query":"费用制度"}')
        await self.page.get_by_role('button', name='确认选择').click()
        await self.page.get_by_role('textbox', name='工作消息', exact=True).fill('核对费用说明')
        await self.page.get_by_role('button', name='发送', exact=True).click()
        await expect(self.page.get_by_text('核实后的结论', exact=True)).to_be_visible()
        self.assertEqual(self.runs[0]['mcp_tool'], {
            'server_id': 'server-1', 'name': 'policy_lookup', 'version': 4, 'arguments': {'query': '费用制度'},
        })
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
        await expect(row.get_by_role('cell').first).to_contain_text('制度服务')
        await expect(row.get_by_role('cell').first.locator('svg')).to_have_count(0)
        await expect(self.page.get_by_role('columnheader', name='状态')).to_have_count(0)
        await expect(self.page.get_by_role('columnheader', name='工具')).to_have_css(
            'background-color', 'rgb(255, 255, 255)'
        )
        await row.get_by_role('button', name='查看参数', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='policy_lookup')
        await expect(dialog).to_contain_text('"query"')
        await expect(dialog).to_contain_text('未声明只读')
        await self.page.keyboard.press('Escape')
        await self.page.set_viewport_size({'width': 390, 'height': 780})
        await expect(row.get_by_role('button', name='查看参数', exact=True)).to_be_in_viewport()
        self.assertTrue(await self.page.locator('.catalog-table .ant-table-content').evaluate(
            '(table) => table.scrollWidth <= table.clientWidth + 1'
        ))
        self.assertEqual(self.mcp_management_requests, ['/api/mcp/servers'])

        self.mcp_operations = []
        await self.page.reload()
        await expect(self.page.get_by_text('暂无可用工具', exact=True)).to_be_visible()
        await expect(row).to_have_count(0)
        self.assertEqual(self.mcp_management_requests, ['/api/mcp/servers'])

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
