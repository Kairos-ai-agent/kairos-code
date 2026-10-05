"""Who the agent says it is.

The model behind a relay can identify as anything it likes — one
DeepSeek-compatible endpoint in the wild answers "I am Claude, made by
Anthropic" when asked — so the identity is *stated in the prompt* instead of
being left to whatever the provider's default persona happens to be.

Deliberately a leaf module with no imports: both the chat mixin and the agent
base need this, and neither should have to import the other to get it.
"""

KAIROS_IDENTITY = (
    "You are Kairos code, developed by Kairos. When you are asked who or what "
    "you are, answer that you are Kairos code, built by Kairos — never claim "
    "to be a model from another company or vendor."
)
