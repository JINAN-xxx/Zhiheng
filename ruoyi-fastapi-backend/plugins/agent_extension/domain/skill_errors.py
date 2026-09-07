class SkillPackageError(ValueError):
    """技能包不满足安全或内容规范。"""


class SkillStorageError(ValueError):
    """技能草稿或正式文件状态无效。"""


class SkillServiceError(ValueError):
    """技能管理固定公开错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def public_dict(self) -> dict[str, str]:
        return {'code': self.code, 'message': self.message}

