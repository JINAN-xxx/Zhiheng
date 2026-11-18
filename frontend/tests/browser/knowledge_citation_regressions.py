"""Numbered references preserve source identity through real Markdown and preview interactions."""
import copy
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from assistant_conversation_regressions import AssistantConversationRegressions

FIRST = 'bcbf48c3-6102-44e7-af5c-badf0b8574d0'
SECOND = 'a4fe7421-bc97-42bd-b9e2-72509b92a840'


class KnowledgeCitationRegressions(AssistantConversationRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.answer = f'已找到接口资料[{FIRST}]。\n\n## 接入规范 [{FIRST}]\n\n另一份资料[2]。'
        self.preview_requests = []
        self.preview_denied = False
        self.sources = [self.source(FIRST, '接口说明.pdf', 'chunk-1'),
                        self.source(SECOND, '接口补充.pdf', 'chunk-2'),
                        self.source(FIRST, '接口说明.pdf', 'chunk-3')]
        self.sessions = [self.session('citation-session')]
        self.turns = {'citation-session': [{
            'id': 'citation-turn', 'sequence': 1, 'content': '查询接口文档',
            'run_id': 'citation-run', 'request_id': 'citation-request', 'request': 'citation-request',
            'sources': self.sources, 'agent_name': '制度助手', 'attachments': [],
            'created_at': '2026-11-18T02:00:00Z'}]}

    @staticmethod
    def source(document, title, chunk):
        return {'knowledge_base_id': 'base-1', 'document_id': document, 'source_id': document,
                'file_id': 'file-' + document, 'chunk_id': chunk, 'index_revision': 'revision-1',
                'title': title, 'content': '实际资料片段', 'score': 0.9}

    def snapshot(self, run_id, cid):
        value = super().snapshot(run_id, cid)
        value['text'] = self.answer
        return value

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path.startswith('/api/knowledge/bases/base-1/documents/') and path.endswith('/preview'):
            self.preview_requests.append(path)
            if self.preview_denied:
                return await route.fulfill(status=403, json={'error': {'code': 'forbidden', 'message': '无权查看该资料'}})
            return await route.fulfill(json={'document_id': path.split('/')[-2], 'title': '接口资料',
                                            'file_id': 'fixture-file', 'parser_version': 'fixture-parser',
                                            'chunk_count': 2, 'text': '授权后返回的真实文档正文'})
        return await super().respond(route)

    async def show_answer(self):
        await self.page.goto(self.origin + '/agents/assistant-1/chat?conversation=citation-session')
        reply = self.page.locator('.assistant-chat__reply').last
        await expect(reply).to_contain_text('已找到接口资料')
        return reply

    async def test_existing_uuid_and_numeric_citations_open_the_corresponding_document(self):
        original = copy.deepcopy(self.sources)
        reply = await self.show_answer()
        await expect(reply).not_to_contain_text(FIRST)
        await expect(reply.locator('.ai-message-content').get_by_role('link', name='Link: 查看来源 1：接口说明.pdf', exact=True)).to_have_count(2)
        await expect(reply.get_by_text('知识来源 · 2', exact=True)).to_be_visible()
        await reply.locator('.ai-message-content').get_by_role('link', name='Link: 查看来源 2：接口补充.pdf', exact=True).click()
        modal = self.page.get_by_role('dialog')
        await expect(modal).to_contain_text('授权后返回的真实文档正文')
        self.assertEqual(self.preview_requests, [f'/api/knowledge/bases/base-1/documents/{SECOND}/preview'])
        self.assertEqual(self.sources, original)
        self.assertEqual(self.submissions, [])

    async def test_numbered_sources_deduplicate_passages_and_keep_the_document_title(self):
        reply = await self.show_answer()
        await reply.get_by_text('知识来源 · 2', exact=True).click()
        await expect(reply.locator('.knowledge-sources__document')).to_have_count(2)
        await expect(reply.get_by_role('button', name='查看来源：接口说明.pdf', exact=True)).to_contain_text('[1] 接口说明.pdf')
        await expect(reply.get_by_role('button', name='查看来源：接口补充.pdf', exact=True)).to_contain_text('[2] 接口补充.pdf')
        await expect(reply.get_by_text('命中 2 个片段', exact=True)).to_be_visible()

    async def test_source_preview_continues_to_enforce_server_access(self):
        self.preview_denied = True
        reply = await self.show_answer()
        await reply.locator('.ai-message-content').get_by_role('link', name='Link: 查看来源 1：接口说明.pdf', exact=True).first.click()
        modal = self.page.get_by_role('dialog')
        await expect(modal).to_contain_text('无权查看该资料')
        await expect(modal.get_by_role('button', name='下载资料', exact=True)).to_be_disabled()
        self.assertEqual(self.preview_requests, [f'/api/knowledge/bases/base-1/documents/{FIRST}/preview'])

    async def test_each_answer_resolves_its_own_source_number(self):
        self.answer = '已找到接口资料[1]。'
        self.turns['citation-session'][0]['sources'] = [self.sources[0]]
        second = copy.deepcopy(self.turns['citation-session'][0])
        second.update(id='second-turn', sequence=2, run_id='second-run', request_id='second-request',
                      request='second-request', sources=[self.sources[1]])
        self.turns['citation-session'].append(second)
        await self.show_answer()
        replies = self.page.locator('.assistant-chat__reply')
        await expect(replies).to_have_count(2)
        await replies.first.get_by_role('link', name='Link: 查看来源 1：接口说明.pdf', exact=True).click()
        modal = self.page.get_by_role('dialog')
        await expect(modal).to_contain_text('授权后返回的真实文档正文')
        await modal.get_by_role('button', name='Close', exact=True).click()
        await expect(modal).to_be_hidden()
        await replies.last.get_by_role('link', name='Link: 查看来源 1：接口补充.pdf', exact=True).click()
        await expect(self.page.get_by_role('dialog')).to_contain_text('授权后返回的真实文档正文')
        self.assertEqual(self.preview_requests, [f'/api/knowledge/bases/base-1/documents/{FIRST}/preview',
                                                f'/api/knowledge/bases/base-1/documents/{SECOND}/preview'])

    async def test_streaming_reference_and_code_block_remain_separate(self):
        self.completed = False
        self.answer = f'已找到接口资料[{FIRST[:12]}'
        reply = await self.show_answer()
        await expect(reply.locator('.ai-message-content')).not_to_contain_text(FIRST[:12])
        self.answer = f'已找到接口资料[{FIRST}]。\n\n```text\n业务示例 [{FIRST}]\n```'
        self.completed = True
        await self.page.reload()
        reply = self.page.locator('.assistant-chat__reply').last
        await expect(reply.locator('.ai-message-content').get_by_role('link', name='Link: 查看来源 1：接口说明.pdf', exact=True)).to_have_count(1)
        await expect(reply.locator('code')).to_contain_text(f'业务示例 [{FIRST}]')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(KnowledgeCitationRegressions(name) for name in KnowledgeCitationRegressions.__dict__
                              if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
