from importlib import import_module
from uuid import uuid4

from zhiheng.shared.principal import Principal


def test_admin_dependency_details_preserve_resource_navigation() -> None:
    module = import_module('zhiheng.shared.references')
    resource_id, owner_id = uuid4(), uuid4()
    reference = module.ResourceReference('agent', resource_id, '客服助手', 'published', owner_id, '/agents/example')
    error = module.reference_conflict(
        'skill_referenced', '技能仍被使用', Principal(uuid4(), 'admin', True), [reference]
    )
    assert error.status_code == 409 and error.code == 'skill_referenced'
    assert error.details == {
        'references': [
            {
                'kind': 'agent',
                'id': str(resource_id),
                'name': '客服助手',
                'status': 'published',
                'path': '/agents/example',
            }
        ]
    }


def test_delegated_manager_cannot_learn_private_dependency_identity() -> None:
    module = import_module('zhiheng.shared.references')
    actor_id, private_id = uuid4(), uuid4()
    references = [
        module.ResourceReference('work', private_id, '保密项目', 'active', uuid4(), '/work/private'),
        module.ResourceReference('agent', actor_id, '本人助手', 'draft', actor_id, '/agents/own'),
    ]
    error = module.reference_conflict(
        'skill_referenced', '技能仍被使用', Principal(actor_id, 'manager', False), references
    )
    hidden, own = error.details['references']
    assert hidden['id'] is None and hidden['path'] is None
    assert hidden['name'] == '其他用户的工作任务（无权查看）' and hidden['status'] == 'referenced'
    assert own['name'] == '本人助手' and own['path'] == '/agents/own'
    assert '保密项目' not in str(error.details) and str(private_id) not in str(error.details)


def test_global_dependency_can_be_named_without_private_owner() -> None:
    module = import_module('zhiheng.shared.references')
    reference = module.ResourceReference('model', uuid4(), '公开模型', 'enabled', path='/models')
    error = module.reference_conflict(
        'connection_referenced', '连接仍被使用', Principal(uuid4(), 'manager', False), [reference]
    )
    assert error.details['references'][0]['name'] == '公开模型'
    assert error.details['references'][0]['path'] == '/models'
