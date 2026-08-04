from fastapi import HTTPException


class LLMGovernanceError(HTTPException):
    def __init__(self, *, status_code: int, code: str, message: str, detail=None):
        self.code = code
        payload = {
            "code": code,
            "message": message,
            "detail": detail if detail is not None else message,
        }
        super().__init__(status_code=status_code, detail=payload)
