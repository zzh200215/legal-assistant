"""口令 / 令牌不得从"打印路径"漏出：422 回显与 pydantic 模型 repr。

两条链路各自独立：
- 422：pydantic 的 error item 带 input（missing 类错误里更是整个请求体），一回显就把用户
  刚提交的密码送回客户端，再被前端 Sentry、访问日志各留一份。
- repr：模型实例的 repr 会进异常局部变量快照、日志 %s、mock 断言失败的 call args。
"""

import unittest

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core.api_response import validation_exception_handler
from app.schemas.agent import AgentApprovalRequestOut
from app.schemas.outbound import SmtpConnectorCreateRequest
from app.schemas.user import (
    LDAPLoginRequest,
    RegisterWithCodeRequest,
    ResetPasswordConfirmRequest,
    UserCreate,
    UserLogin,
    UserPasswordReset,
)

_PASSWORD_CANARY = "canary7"  # 7 位：会被 min_length=8 拒绝
_LONG_CANARY = "canary-long-secret"


class _LoginBody(BaseModel):
    username: str
    password: str = Field(min_length=8)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    @app.post("/login")
    def login(body: _LoginBody):  # pragma: no cover - 只用来触发校验失败
        return {"ok": True}

    return app


class ValidationErrorDoesNotEchoInputTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(_build_app())

    def test_rejected_password_is_not_echoed(self):
        resp = self.client.post("/login", json={"username": "u", "password": _PASSWORD_CANARY})

        self.assertEqual(resp.status_code, 422)
        self.assertNotIn(_PASSWORD_CANARY, resp.text)

    def test_error_on_another_field_does_not_echo_the_whole_body(self):
        """missing 类错误的 input 是整个 body——密码就躺在里面，哪怕它自己校验通过。"""
        resp = self.client.post("/login", json={"password": _LONG_CANARY})

        self.assertEqual(resp.status_code, 422)
        self.assertNotIn(_LONG_CANARY, resp.text)
        self.assertEqual(resp.json()["error"]["field_errors"][0]["field"], "username")

    def test_root_level_body_error_does_not_echo_payload(self):
        """body 整体类型不对时 input 是原始载荷，按字段名遮不住，只能不回显。"""
        resp = self.client.post("/login", json=[_LONG_CANARY])

        self.assertEqual(resp.status_code, 422)
        self.assertNotIn(_LONG_CANARY, resp.text)

    def test_diagnostics_survive_without_the_raw_value(self):
        """不回显不等于没法定位：loc / type / msg / ctx 与 field_errors 都还在。"""
        resp = self.client.post("/login", json={"username": "u", "password": _PASSWORD_CANARY})
        error = resp.json()["error"]
        detail = error["detail"][0]

        self.assertEqual(detail["loc"], ["body", "password"])
        self.assertEqual(detail["type"], "string_too_short")
        self.assertEqual(detail["ctx"], {"min_length": "8"})
        self.assertNotIn("input", detail)
        self.assertEqual(error["field_errors"][0]["field"], "password")
        self.assertEqual(error["code"], "VALIDATION_ERROR")


class SecretFieldReprTests(unittest.TestCase):
    def test_password_and_token_fields_stay_out_of_model_repr(self):
        models = [
            UserLogin(username="u", password=_LONG_CANARY),
            UserCreate(username="u", email="u@example.com", password=_LONG_CANARY),
            LDAPLoginRequest(username="u", password=_LONG_CANARY),
            UserPasswordReset(new_password=_LONG_CANARY),
            RegisterWithCodeRequest(
                username="u",
                email="u@example.com",
                password=_LONG_CANARY,
                code="123456",
            ),
            ResetPasswordConfirmRequest(token=_LONG_CANARY, new_password=_LONG_CANARY),
            SmtpConnectorCreateRequest(
                name="smtp",
                host="smtp.example.com",
                username="u",
                password=_LONG_CANARY,
                from_address="u@example.com",
            ),
        ]

        for model in models:
            with self.subTest(model=type(model).__name__):
                self.assertNotIn(_LONG_CANARY, repr(model))

    def test_repr_keeps_the_non_secret_fields(self):
        text = repr(UserLogin(username="demo_lawyer", password=_LONG_CANARY))

        self.assertIn("demo_lawyer", text)
        self.assertNotIn(_LONG_CANARY, text)

    def test_approval_token_stays_out_of_model_repr(self):
        row = AgentApprovalRequestOut(
            id=1,
            user_id=7,
            tool_name="sql_query",
            risk_level="high",
            status="pending",
            approval_token=_LONG_CANARY,
            created_at="2026-08-28T10:00:00",
        )

        self.assertNotIn(_LONG_CANARY, repr(row))
        self.assertIn("sql_query", repr(row))

    def test_hidden_fields_still_carry_their_values(self):
        """遮 repr 不等于清空字段：业务代码照样要能取到原值。"""
        login = UserLogin(username="u", password=_LONG_CANARY)

        self.assertEqual(login.password, _LONG_CANARY)
        self.assertEqual(login.model_dump()["password"], _LONG_CANARY)


if __name__ == "__main__":
    unittest.main()
