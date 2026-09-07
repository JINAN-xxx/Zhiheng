from collections.abc import Callable

from fastapi.routing import APIRoute

from plugins.agent_extension.controller.skill_controller import skill_controller


def route_map() -> dict[tuple[str, str], APIRoute]:
    return {
        (next(iter(route.methods)), route.path): route
        for route in skill_controller.routes
        if isinstance(route, APIRoute) and len(route.methods) == 1
    }


def dependency_calls(route: APIRoute) -> list[Callable[..., object]]:
    calls: list[Callable[..., object]] = []
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        if dependant.call is not None:
            calls.append(dependant.call)
        pending.extend(dependant.dependencies)
    return calls


def test_skill_routes_use_exact_methods_and_permissions() -> None:
    assert skill_controller.prefix == '/agent-extension/skills'
    expected = {
        ('GET', '/agent-extension/skills'): 'agent_extension:skill:list',
        ('GET', '/agent-extension/skills/available'): 'agent_extension:skill:use',
        ('GET', '/agent-extension/skills/{skill_id}'): 'agent_extension:skill:query',
        ('GET', '/agent-extension/skills/{skill_id}/content'): 'agent_extension:skill:query',
        ('POST', '/agent-extension/skills/import/prepare'): 'agent_extension:skill:add',
        ('POST', '/agent-extension/skills/import/{draft_id}/confirm'): 'agent_extension:skill:add',
        ('PUT', '/agent-extension/skills/{skill_id}/status'): 'agent_extension:skill:edit',
        ('DELETE', '/agent-extension/skills/{skill_id}'): 'agent_extension:skill:remove',
    }

    routes = route_map()
    assert set(routes) == set(expected)
    for key, permission in expected.items():
        calls = dependency_calls(routes[key])
        assert any(type(call).__name__ == 'PreAuth' for call in calls)
        permissions = [call.perm for call in calls if type(call).__name__ == 'CheckUserInterfaceAuth']
        assert permissions == [permission]

