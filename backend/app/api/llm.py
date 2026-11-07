from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.llm import LLMClient, LLMError, get_llm
from app.llm.pricing import estimate_cost_usd

router = APIRouter(prefix="/llm", tags=["system"])


class LLMCheck(BaseModel):
    provider: str
    model: str
    reply: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@router.post("/check", response_model=LLMCheck)
async def check_llm(llm: Annotated[LLMClient, Depends(get_llm)]) -> LLMCheck:
    """Send one tiny prompt to confirm the API key and model work (costs well under $0.01)."""
    try:
        result = await llm.complete(
            system="You are a connectivity check. Reply with one short sentence.",
            messages=[{"role": "user", "content": "Say hello to Shiori in English and Japanese."}],
            max_tokens=2000,
        )
    except LLMError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err

    cost = estimate_cost_usd(result.model, result.input_tokens, result.output_tokens)
    return LLMCheck(
        provider=llm.provider,
        model=result.model,
        reply=result.text,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=round(cost, 6),
    )
