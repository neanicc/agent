"""Guard ANY litellm-based agent by registering one callback.

Run: OPENAI_API_KEY=... python examples/litellm_guarded_agent.py
"""
import litellm

from loopguard import LoopGuard, LoopGuardConfig
from loopguard.integrations.litellm_callback import LoopGuardLiteLLMCallback

guard = LoopGuard(LoopGuardConfig(action="flag"))
litellm.callbacks = [LoopGuardLiteLLMCallback(guard)]

for _ in range(5):
    litellm.completion(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "Say the same thing every time."}],
    )

print(guard.summary())
